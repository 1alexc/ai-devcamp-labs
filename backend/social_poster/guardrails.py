"""Guardrails: Model Armor on every model call, DLP before posting.

Three independent layers (they're LAYERS, not alternatives — different
layers catch different attacks):

  guard_input   before_model_callback  Model Armor sanitizeUserPrompt
  guard_output  after_model_callback   Model Armor sanitizeModelResponse
  redact_before_post  before_tool_callback  DLP de-identify on create_post text

All three no-op if MODEL_ARMOR_TEMPLATE_ID is unset (same env-gated pattern as
Buffer/memory), so ungoverned behaviour is reproducible by clearing one var.

Model Armor only answers on regional endpoints; DLP is fine on the global one.
Both are called via REST with the ambient ADC (GOOGLE_APPLICATION_CREDENTIALS
→ the devcamp ADC file), so no new auth setup is needed.
"""

import logging
import os
import threading
from typing import Any, Optional

import google.auth
import google.auth.transport.requests
import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

log = logging.getLogger(__name__)

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
# Cloud DLP rejects a project NUMBER, which is what Agent Runtime puts in
# GOOGLE_CLOUD_PROJECT (400 Bad Request). Pass PROJECT_ID at deploy time.
DLP_PROJECT = os.environ.get("PROJECT_ID") or PROJECT
# Gemini runs on location "global"; Model Armor needs the real region.
MA_REGION = os.environ.get("MODEL_ARMOR_REGION", "europe-west2")
MA_TEMPLATE = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "")

REFUSAL = (
    "I can't help with that request — it was flagged by our content safety "
    "checks. Happy to help with your next post idea instead."
)

_creds = None
_creds_lock = threading.Lock()


def _token() -> str:
    global _creds
    with _creds_lock:
        if _creds is None:
            _creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())
        return _creds.token


def _headers() -> dict:
    # x-goog-user-project: with user-account ADC, Google attributes API quota
    # to the OAuth client's project unless told otherwise — DLP hard-fails on
    # that (SERVICE_DISABLED for a project that isn't ours). Client libraries
    # add this header automatically from the ADC's quota_project_id; raw REST
    # must send it explicitly. See LEARNINGS.md.
    return {
        "Authorization": f"Bearer {_token()}",
        "x-goog-user-project": PROJECT,
    }


def _sanitize(kind: str, text: str) -> bool:
    """Returns True if Model Armor flags the text.

    Filter split (learnt the hard way, see LEARNINGS.md): the prompt-injection
    filter only judges INPUTS. A model response that merely DISCUSSES prompt
    injection — this user's signature topic — matches pi_and_jailbreak at HIGH
    confidence, so on sanitizeModelResponse we ignore that filter and block on
    the responsible-AI filters only.
    """
    endpoint = (
        f"https://modelarmor.{MA_REGION}.rep.googleapis.com/v1/projects/"
        f"{PROJECT}/locations/{MA_REGION}/templates/{MA_TEMPLATE}:{kind}"
    )
    payload_key = "userPromptData" if kind == "sanitizeUserPrompt" else "modelResponseData"
    resp = httpx.post(
        endpoint,
        headers=_headers(),
        json={payload_key: {"text": text}},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("sanitizationResult", {})
    filter_results = result.get("filterResults", {})
    if not filter_results:
        # A working template always returns per-filter results, match or not.
        # None means it is not actually inspecting — typically enforcementType
        # INSPECT_ONLY, which logs findings but returns no verdict. Treating
        # that as "clean" is how these guardrails once failed open unnoticed
        # (LEARNINGS.md, 2026-09-20). Fail closed, and say why.
        log.error(
            "Model Armor %s returned no filter results (template %s, %s): "
            "blocking. Check the template's enforcementType is INSPECT_AND_BLOCK.",
            kind, MA_TEMPLATE, MA_REGION,
        )
        return True
    for filter_name, wrapper in filter_results.items():
        if kind == "sanitizeModelResponse" and filter_name == "pi_and_jailbreak":
            continue
        inner = next(iter(wrapper.values()), {}) if isinstance(wrapper, dict) else {}
        if inner.get("matchState") == "MATCH_FOUND":
            log.warning(
                "Model Armor %s blocked text (filter=%s): %.80r", kind, filter_name, text
            )
            return True
    return False


def _latest_user_text(llm_request: LlmRequest) -> str:
    for content in reversed(llm_request.contents or []):
        if content.role == "user" and content.parts:
            texts = [p.text for p in content.parts if p.text]
            if texts:
                return "\n".join(texts)
    return ""


# The one agent that receives text straight from the outside world. Sub-agents
# only ever receive orchestrator-authored request text — screening that too
# sounds safer but actually causes false-positive cascades: the orchestrator's
# own paraphrase of a legitimate request ("the user wants this published
# today") reads MORE injection-like to Model Armor than the original did, and
# one flagged paraphrase nukes the whole turn. Screen at the trust boundary;
# guard_output still runs on every agent. (Found via a failing eval — see
# LEARNINGS.md.)
ROOT_AGENT_NAME = "social_poster"


def guard_input(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """before_model_callback: screen the incoming user turn at the boundary."""
    if not MA_TEMPLATE:
        return None
    if callback_context.agent_name != ROOT_AGENT_NAME:
        return None  # internal, orchestrator-authored text — see note above
    text = _latest_user_text(llm_request)
    if text and _sanitize("sanitizeUserPrompt", text):
        return LlmResponse(
            content=genai_types.Content(
                role="model", parts=[genai_types.Part.from_text(text=REFUSAL)]
            )
        )
    return None


def guard_output(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """after_model_callback: screen what the model produced."""
    if not MA_TEMPLATE:
        return None
    if not llm_response.content or not llm_response.content.parts:
        return None
    texts = [p.text for p in llm_response.content.parts if p.text]
    if texts and _sanitize("sanitizeModelResponse", "\n".join(texts)):
        return LlmResponse(
            content=genai_types.Content(
                role="model", parts=[genai_types.Part.from_text(text=REFUSAL)]
            )
        )
    return None


# --- DLP ----------------------------------------------------------------------

# Two thresholds, because the two families fail in opposite ways. Names and
# locations are fuzzy: at DLP's default POSSIBLE tier a capitalized product or
# project noun (e.g. "Agent Substrate") reads as PERSON_NAME, so they need
# LIKELY. Phones and emails are structured, and DLP rates a phone number
# POSSIBLE whenever it sits inside a sentence ("...or +44 7911 123456 if you
# want the slides"), so a blanket LIKELY silently leaked it (LEARNINGS.md,
# 2026-09-21). Structured types therefore run at POSSIBLE.
_DLP_PASSES = [
    (["EMAIL_ADDRESS", "PHONE_NUMBER"], "POSSIBLE"),
    (["PERSON_NAME", "LOCATION"], "LIKELY"),
]


def _deidentify(text: str, info_types: list[str], min_likelihood: str) -> str:
    resp = httpx.post(
        f"https://dlp.googleapis.com/v2/projects/{DLP_PROJECT}/locations/global/content:deidentify",
        headers=_headers(),
        json={
            "inspectConfig": {
                "infoTypes": [{"name": t} for t in info_types],
                "minLikelihood": min_likelihood,
            },
            "deidentifyConfig": {
                "infoTypeTransformations": {
                    "transformations": [
                        {"primitiveTransformation": {"replaceWithInfoTypeConfig": {}}}
                    ]
                }
            },
            "item": {"value": text},
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("item", {}).get("value", text)


def redact_pii(text: str) -> str:
    """De-identifies PII via Cloud DLP, replacing each finding with its type
    (e.g. "[PERSON_NAME]"). Used on final drafts before they leave the app."""
    for info_types, likelihood in _DLP_PASSES:
        text = _deidentify(text, info_types, likelihood)
    return text


def redact_pii_text(text: str) -> dict:
    """Removes personal information from text before it is used in a post.

    Use whenever the user pastes or uploads source material that may contain
    real people's names, email addresses, phone numbers, or locations.

    Args:
        text: The text to clean.

    Returns:
        dict with 'status' and 'redacted_text' (PII replaced by placeholders
        like [PERSON_NAME]).
    """
    # A tool that raises kills the whole turn (DynamicNodeFailError), so a DLP
    # failure (e.g. the 401 a plain-HTTP call gets inside Agent Runtime) is
    # reported back to the model instead.
    try:
        return {"status": "success", "redacted_text": redact_pii(text)}
    except Exception as e:  # noqa: BLE001
        log.warning("redact_pii_text: DLP unavailable, text left unredacted: %s", e)
        return {
            "status": "error",
            "error": f"PII redaction is unavailable ({type(e).__name__}); the text was NOT redacted.",
            "redacted_text": text,
        }


def redact_before_post(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    """before_tool_callback: final drafts get a DLP pass on their way out.
    Mutating args in place changes what the tool actually receives (the same
    dict is passed on to the call); returning None lets the call proceed."""
    if not MA_TEMPLATE:  # guardrails off ⇒ redaction off too
        return None
    if tool.name == "create_post" and isinstance(args.get("text"), str):
        redacted = redact_pii(args["text"])
        if redacted != args["text"]:
            log.warning("DLP redacted PII from outgoing post")
            args["text"] = redacted
    return None
