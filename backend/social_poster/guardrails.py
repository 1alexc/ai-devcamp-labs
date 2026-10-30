"""Guardrails: Model Armor on every model call, DLP before posting.

Three independent layers:
  1. Model Armor request screening on incoming user prompts (root_agent)
  2. Model Armor response screening on model output (all agents)
  3. Cloud DLP de-identification before creating posts (create_post)

Switched by MODEL_ARMOR_TEMPLATE_ID (blank or 'off' means off).

Model Armor templates are attached inline via GenerateContentConfig
(model_armor_config). This avoids the 401 Unauthorized errors that raw REST
sanitize calls encounter inside Agent Runtime due to mTLS agent token binding.
"""

import logging
import os
import pathlib
import threading
from typing import Any, Optional

from dotenv import load_dotenv
import google.auth
import google.auth.transport.requests
import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import Gemini
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

# Ensure backend/social_poster/.env is loaded (e.g. during adk eval, testing)
_env_file = pathlib.Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

log = logging.getLogger(__name__)

# Use the project ID (alphanumeric string), NOT the project number. Inside
# Agent Runtime, GOOGLE_CLOUD_PROJECT is often populated with the numeric
# project number, which Cloud DLP and Model Armor template lookups reject.
PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DLP_PROJECT = PROJECT_ID

# Model Armor operates regionally; us-central1 is the default region.
MA_REGION = os.environ.get("MODEL_ARMOR_REGION", "us-central1")
MA_TEMPLATE = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "").strip()

REFUSAL = "I can't help with that request: it was flagged by our content safety checks."


def is_enabled() -> bool:
    """Returns True if Model Armor guardrails are active (blank or 'off' means off)."""
    val = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "").strip()
    return bool(val) and val.lower() != "off"


def is_pii_enabled() -> bool:
    """Returns True if PII redaction is active (PII_REDACTION=on)."""
    val = os.environ.get("PII_REDACTION", "").strip().lower()
    return val in ("on", "true", "1")


def content_config(
    screen_input: bool = True,
) -> Optional[genai_types.GenerateContentConfig]:
    """Returns a GenerateContentConfig carrying a Model Armor config.

    Request template: devcamp-guardrails (or MODEL_ARMOR_TEMPLATE_ID).
    Response template: devcamp-guardrails-output (or MODEL_ARMOR_RESPONSE_TEMPLATE_ID / <template>-output).

    The two sub-agents use screen_input=False (prompt_template_name=None) because
    their input is text the orchestrator wrote, avoiding false-positive injection
    cascades. Only root_agent screens user input (screen_input=True).
    """
    if not is_enabled():
        return None

    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    region = os.environ.get("MODEL_ARMOR_REGION", "us-central1")
    template_id = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "").strip()

    def _full_template_path(name: str) -> str:
        if name.startswith("projects/"):
            return name
        return f"projects/{project_id}/locations/{region}/templates/{name}"

    prompt_template_name = _full_template_path(template_id) if screen_input else None
    response_template_id = os.environ.get(
        "MODEL_ARMOR_RESPONSE_TEMPLATE_ID", f"{template_id}-output"
    )
    response_template_name = _full_template_path(response_template_id)

    return genai_types.GenerateContentConfig(
        model_armor_config=genai_types.ModelArmorConfig(
            prompt_template_name=prompt_template_name,
            response_template_name=response_template_name,
        )
    )


def friendly_block(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """after_model_callback: turns a Model Armor block into a readable refusal.

    When Model Armor blocks a prompt or response, Gemini returns an empty
    content response with error_code='MODEL_ARMOR'. This callback intercepts
    it and replaces it with a readable refusal.
    """
    if not is_enabled():
        return None
    if llm_response and getattr(llm_response, "error_code", None) == "MODEL_ARMOR":
        return LlmResponse(
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part.from_text(text=REFUSAL)],
            )
        )
    return None


def model(name: str) -> Gemini:
    """The ADK model object with retries.

    Model Armor's template lookup sometimes fails at random with:
    'The template named ... was not found' (HTTP 400), even though the
    template exists. A retry fixes it.

    Retry options must be set on the ADK model object itself
    (Gemini(model=..., retry_options=...)); retry options set anywhere else
    are ignored once deployed. The same retry also covers 429 RESOURCE_EXHAUSTED
    (which means the regional model is busy).
    """
    if is_enabled():
        region = os.environ.get("MODEL_ARMOR_REGION", "us-central1")
        # Gemini on `global` (the agents-cli deploy default) cannot resolve a
        # regional Model Armor template and fails with 400 TEMPLATE_NOT_FOUND.
        if os.environ.get("GOOGLE_CLOUD_LOCATION") in (None, "", "global"):
            os.environ["GOOGLE_CLOUD_LOCATION"] = region
        return Gemini(
            model=name,
            retry_options=genai_types.HttpRetryOptions(
                attempts=8,
                initial_delay=0.3,
                max_delay=8.0,
                exp_base=2.0,
                jitter=0.3,
                http_status_codes=[400, 429],
            ),
        )
    return Gemini(model=name)


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
    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    return {
        "Authorization": f"Bearer {_token()}",
        "x-goog-user-project": project_id,
    }


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
    if not is_pii_enabled():  # PII redaction off ⇒ skip pre-post redaction
        return None
    if tool.name == "create_post" and isinstance(args.get("text"), str):
        redacted = redact_pii(args["text"])
        if redacted != args["text"]:
            log.warning("DLP redacted PII from outgoing post")
            args["text"] = redacted
    return None
