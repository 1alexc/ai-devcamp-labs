"""The social_poster multi-agent system: an orchestrator (`social_poster`)
over `research_agent`, `draft_agent`, an optional `memory_agent`, and posting
toolsets, plus three guardrail layers from guardrails.py wired onto EVERY
agent:

- Model Armor screens each model call's input (before_model_callback) and
  output (after_model_callback); MEDIUM_AND_ABOVE findings get a friendly
  refusal instead of a model call.
- Cloud DLP redacts PII (person names, emails, phone numbers, locations) from
  final drafts on their way into create_post (before_tool_callback).

All three no-op when MODEL_ARMOR_TEMPLATE_ID is unset, so ungoverned
behaviour is one env var away for A/B demos.
"""

import datetime
import json
import logging
import os
import pathlib
import re
import sys
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types
from mcp import StdioServerParameters

from . import db, guardrails
from .tools import check_text_length, generate_image, upload_image

log = logging.getLogger(__name__)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
MCP_SERVER = REPO_ROOT / "mcp" / "linkedin_server.py"  # McpToolset needs absolute paths

# --- Image hosting (optional) --------------------------------------------------
# Buffer's create_post can't fetch a locally-generated image file — it needs a
# real HTTPS URL (see _BUFFER_ROUTING below). Set GCS_BUCKET_NAME to have
# draft_agent upload generated images to Cloud Storage (via upload_image, an
# in-process tool — no separate server, unlike LinkedIn/Buffer, since it's
# just the next step after generate_image in the same agent) and hand Buffer
# a signed URL instead. Leave blank to skip: images stay local-only and
# Buffer posts stay text-only, exactly like before this existed.
use_gcs = bool(os.environ.get("GCS_BUCKET_NAME", "").strip())

# --- Per-agent model pinning ---------------------------------------------
# Each agent's model comes from env so cost/latency can be tuned per role
# without touching code, and re-run through evals to confirm parity. Defaults
# are all flash. One cost experiment: move the two agents that don't need
# deep reasoning (research summarisation, the routing orchestrator) to
# flash-lite, keep draft_agent on flash where quality shows most.
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "gemini-flash-latest")
DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "gemini-flash-latest")
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gemini-flash-latest")

# --- Specialist: research -----------------------------------------------------
# google_search stays isolated in its own agent (Vertex AI rejects mixing the
# built-in search tool with function tools in one model call — see LEARNINGS).
# output_key writes the summary into session state for draft_agent to read.
research_agent = Agent(
    name="research_agent",
    model=RESEARCH_MODEL,
    description="Researches facts, dates, and context on the web for a post idea.",
    instruction="""Research the given topic with google_search and return a
concise, factual summary: key facts, dates, numbers, and anything surprising
or quotable. No drafting — just the research notes.""",
    tools=[google_search],
    output_key="research_notes",
    before_model_callback=guardrails.guard_input,
    after_model_callback=guardrails.guard_output,
)

# --- Specialist: drafting -----------------------------------------------------
skill_toolset = SkillToolset(
    skills=[
        load_skill_from_dir(SKILLS_DIR / "post-formatter"),
        load_skill_from_dir(SKILLS_DIR / "platform-style"),
        load_skill_from_dir(SKILLS_DIR / "brand-voice"),
        load_skill_from_dir(SKILLS_DIR / "poster-style"),
    ],
)

_GCS_UPLOAD_STEP = (
    """ If you just generated a NEW image, or there's no hosted URL yet for
   the current one, call upload_image with the image_path to get a public
   HTTPS URL, and report BOTH the local path and that URL — the public URL
   is what lets Buffer attach the image to a post. If a hosted URL already
   exists below for the current image, reuse it — don't re-upload."""
    if use_gcs
    else ""
)

# Images cost real money per generation, so draft_agent's own final response
# (its output_key text) isn't enough state to check "does an image already
# exist" — this callback pulls the actual path/URL out of the tool results
# themselves into session state, so it survives across the multiple
# draft_agent calls one post typically goes through (initial draft, platform
# switch, text revisions, ...).
def _track_image_state(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict
) -> Optional[dict]:
    if tool.name == "generate_image" and tool_response.get("status") == "success":
        tool_context.state["current_image_path"] = tool_response["image_path"]
        tool_context.state.pop("current_image_url", None)  # belonged to the old image
    elif (
        tool.name == "upload_image"
        and tool_response.get("status") == "success"
        and not tool_response.get("dry_run")
    ):
        tool_context.state["current_image_url"] = tool_response["url"]
    return None

# Confirmed live (2026-09-17): despite the instruction above, draft_agent
# sometimes writes "Image Description: <text>" (or "(Image: ...)") instead of
# actually calling generate_image — an instruction-following gap on
# gemini-flash-latest that a stronger prompt alone didn't fix. Rather than
# just showing the user an error, this intercepts that exact pattern and
# turns the model's own (usually well-written) description into a REAL
# generate_image call, so the turn self-corrects instead of silently
# skipping the image.
_IMAGE_PLACEHOLDER_RE = re.compile(
    r"(?im)^\s*\(?\s*image(?:\s+description)?\s*:\s*(.+?)\)?\s*$"
)


def _catch_image_placeholder(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    if not llm_response.content or not llm_response.content.parts:
        return None
    parts = llm_response.content.parts
    if any(p.function_call for p in parts):
        return None  # already a real tool call this turn — nothing to fix
    if callback_context.state.get("current_image_path"):
        # An image already exists for this post — draft_agent should reuse
        # it (see instruction below), not have a fresh one forced on it.
        # Forcing a NEW generate_image call here would just be another paid
        # image for no reason, the opposite of what this guard is for.
        return None
    text = "\n".join(p.text for p in parts if p.text)
    match = _IMAGE_PLACEHOLDER_RE.search(text)
    if not match:
        return None
    prompt = match.group(1).strip()
    if not prompt:
        return None
    log.warning(
        "draft_agent wrote an image placeholder instead of calling generate_image;"
        " forcing the real call with its own description as the prompt"
    )
    return LlmResponse(
        content=genai_types.Content(
            role="model",
            parts=[
                genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        name="generate_image", args={"prompt": prompt}
                    )
                )
            ],
        )
    )


draft_agent = Agent(
    name="draft_agent",
    model=DRAFT_MODEL,
    description="Writes and revises the social media post draft, and generates images.",
    instruction=f"""You write social media post drafts.

Research notes from an earlier step (may be empty):
{{research_notes?}}

Style guidance from the user's past posts (may be empty):
{{memory_notes?}}

Image already generated for the CURRENT post, if any (empty means none yet):
  local path: {{current_image_path?}}
  hosted URL: {{current_image_url?}}

Rules:
1. Load and follow the relevant skills before drafting: post-formatter
   (structure), platform-style (rules for the target platform), and
   brand-voice (tone).
2. Ground the draft in the research notes when they exist; never invent facts.
3. Images cost real money to generate. If an image already exists above for
   this same post, REUSE it — report that same path/URL, do not call
   generate_image again. Only call generate_image if there is no existing
   image yet, or the user explicitly asks for a new/different image (a
   genuinely new, unrelated post idea also counts as needing its own new
   image — don't reuse an old post's image for a different topic). When you
   do call it, load the poster-style skill first, give a detailed visual
   description, and ACTUALLY CALL the tool — never write a placeholder like
   "(Image: ...)" or describe the image in the post text instead of calling
   it. The image is a separate attachment, not part of the post's
   words.{_GCS_UPLOAD_STEP}
4. Return ONLY the draft text (with no image caption or placeholder in it)
   and the image path/URL if any — no commentary about approvals or posting;
   the orchestrator handles that.""",
    tools=[skill_toolset, generate_image, check_text_length, *([upload_image] if use_gcs else [])],
    output_key="current_draft",
    before_model_callback=guardrails.guard_input,
    after_model_callback=[_catch_image_placeholder, guardrails.guard_output],
    after_tool_callback=_track_image_state,
)

# --- Specialist: memory (optional, via A2A) -----------------------------------
# The real thing is the Agent Garden RAG sample deployed to Agent Engine
# (docs/agent-engine-rag-setup.md); backend/mock_memory_agent.py is a local
# stand-in that serves the same A2A protocol on :8001. Either way the agent
# card URL comes from env, so this stays optional exactly like Buffer below.
MEMORY_AGENT_CARD_URL = os.environ.get("MEMORY_AGENT_CARD_URL", "")

memory_agent = None
if MEMORY_AGENT_CARD_URL:
    memory_agent = RemoteA2aAgent(
        name="memory_agent",
        description=(
            "Knows the user's past posts. Consult for their usual topics,"
            " tone, and phrasing before drafting."
        ),
        agent_card=MEMORY_AGENT_CARD_URL,
    )

# --- Posting toolsets -----------------------------------------------------
def _linkedin_connection() -> StdioConnectionParams:
    return StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,  # this venv's python
            args=[str(MCP_SERVER)],
            env={
                "DRY_RUN": os.environ.get("DRY_RUN", "true"),
                "LINKEDIN_ACCESS_TOKEN": os.environ.get("LINKEDIN_ACCESS_TOKEN", ""),
            },
        ),
    )


# Split into two toolsets so require_confirmation only gates the actual post,
# not read-only lookups like get_profile — McpTool's require_confirmation
# callable only sees a call's own args, not which tool it belongs to, so a
# single toolset can't apply it selectively (see @experimental note in ADK
# 2.4.0's McpTool.run_async). Two toolsets, split by tool_filter, can.
linkedin_toolset = McpToolset(
    connection_params=_linkedin_connection(),
    tool_filter=["get_profile"],
)
linkedin_post_toolset = McpToolset(
    connection_params=_linkedin_connection(),
    tool_filter=["create_post"],
    # Framework-level approval gate on the one tool that actually publishes.
    require_confirmation=True,
)

BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY", "")
# POST_VIA overrides the LLM's own routing (see _BUFFER_ROUTING below):
# "buffer" forces Buffer-only (drops the LinkedIn tool entirely), "linkedin"
# forces LinkedIn-only (drops Buffer even if a key is configured). Blank
# (default) exposes both and lets the agent route by what the user asks for.
POST_VIA = os.environ.get("POST_VIA", "").strip().lower()
if POST_VIA not in ("", "auto", "buffer", "linkedin"):
    raise RuntimeError(f"POST_VIA must be 'auto', 'buffer', or 'linkedin', got {POST_VIA!r}")

def _buffer_connection() -> StreamableHTTPConnectionParams:
    return StreamableHTTPConnectionParams(
        url="https://mcp.buffer.com/mcp",
        headers={"Authorization": f"Bearer {BUFFER_API_KEY}"},
    )


buffer_toolset = None
buffer_post_toolset = None
if BUFFER_API_KEY:
    # Same split as LinkedIn above: only create_post needs the confirmation
    # gate — buffer_post_toolset below still uses a ToolPredicate since
    # that's the one Buffer tool we DO want matched dynamically regardless
    # of what else Buffer ships.
    #
    # buffer_toolset itself is deliberately an explicit allowlist, not
    # "every other tool": Buffer's MCP server exposes 20 tools total
    # (idea/template management, a GraphQL escape hatch via
    # execute_query/execute_mutation, etc.), but the agent only ever needs
    # channel lookup + account/timezone info to build create_post's args.
    # Confirmed live: exposing all 19 non-create_post tools alongside
    # AgentTool(draft_agent) makes gemini-2.5-flash/pro emit malformed
    # pseudo-code function calls (`print(default_api.draft_agent(...))`)
    # instead of real ones — narrowing to just what's used fixes it
    # outright, and is better least-privilege practice regardless of the
    # model bug.
    buffer_toolset = McpToolset(
        connection_params=_buffer_connection(),
        tool_filter=["list_channels", "get_account"],
    )
    buffer_post_toolset = McpToolset(
        connection_params=_buffer_connection(),
        tool_filter=lambda tool, readonly_context=None: tool.name == "create_post",
        require_confirmation=True,
    )
elif POST_VIA == "buffer":
    raise RuntimeError("POST_VIA=buffer requires BUFFER_API_KEY to be set.")

use_linkedin = POST_VIA != "buffer"
use_buffer = bool(BUFFER_API_KEY) and POST_VIA != "linkedin"

# Buffer has no DRY_RUN equivalent (unlike the local LinkedIn MCP server) —
# its remote server posts for real the instant shareNow/addToQueue clears,
# and the model chooses `mode` itself, so nothing here stops it from picking
# an immediate one. Force every Buffer post at least this many minutes into
# the future (customScheduled + dueAt) so there's a real window to review or
# cancel it in Buffer's own UI, instead of trusting the model's choice of
# mode. Set to 0 to disable for a live demo where an immediate real post is
# the point.
BUFFER_REVIEW_DELAY_MINUTES = int(os.environ.get("BUFFER_REVIEW_DELAY_MINUTES", "60"))


def _delay_buffer_post(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    if (
        tool.name == "create_post"
        and args.get("channelId")  # Buffer-only field; LinkedIn's local server has none
        and BUFFER_REVIEW_DELAY_MINUTES > 0
    ):
        due = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=BUFFER_REVIEW_DELAY_MINUTES
        )
        args["mode"] = "customScheduled"
        args["dueAt"] = due.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        log.info(
            "Buffer post delayed to %s (BUFFER_REVIEW_DELAY_MINUTES=%d) for review",
            args["dueAt"],
            BUFFER_REVIEW_DELAY_MINUTES,
        )
    return None

# --- Pipeline stage, surfaced to the frontend via AG-UI shared state ----------
# idea -> consulting_memory -> researching -> drafting -> awaiting_approval -> posted
# (image generation now happens INSIDE draft_agent, so the orchestrator's
# after_tool_callback no longer sees generate_image — the old
# "generating_image" stage is folded into "drafting".)

_TOOL_STAGES = {
    research_agent.name: "researching",
    draft_agent.name: "drafting",
    "memory_agent": "consulting_memory",
}


def _init_stage(callback_context: CallbackContext) -> None:
    if "pipeline_stage" not in callback_context.state:
        callback_context.state["pipeline_stage"] = "idea"


def _unwrap_mcp_result(result: dict) -> dict:
    """Mirrors PostGallery.tsx's unwrapMcpContent: Buffer's remote MCP server
    wraps results as {"content": [{"type": "text", "text": "<json>"}]};
    LinkedIn's local FastMCP server returns a plain dict already."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                pass
    return result


def _track_stage(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict
) -> Optional[dict]:
    if tool.name in _TOOL_STAGES:
        tool_context.state["pipeline_stage"] = _TOOL_STAGES[tool.name]
    elif tool.name == "create_post" and tool_response.get("isError") is False:
        # Fires on BLOCKED attempts too (require_confirmation returns an
        # {"error": ...} without "isError") — only a completed MCP call
        # carries isError: False. See LEARNINGS (2026-07-14).
        tool_context.state["pipeline_stage"] = "posted"
        parsed = _unwrap_mcp_result(tool_response)
        db.save_post(
            platform="Buffer" if args.get("channelId") else "LinkedIn",
            text=args.get("text", ""),
            post_url=parsed.get("post_url") or parsed.get("url"),
            image_path=tool_context.state.get("current_image_path"),
            image_url=tool_context.state.get("current_image_url"),
        )
    return None  # never modify the tool result


def _stage_after_agent(callback_context: CallbackContext) -> None:
    # A turn that ends mid-pipeline is waiting on the user (draft approval).
    if callback_context.state.get("pipeline_stage") in ("researching", "drafting"):
        callback_context.state["pipeline_stage"] = "awaiting_approval"


# --- Orchestrator -------------------------------------------------------------
_MEMORY_ROUTING = """
0. FIRST, before researching or drafting, ask memory_agent what the user has
   posted about before and how they phrase things; weave that into the draft
   brief so the new post sounds like them and doesn't repeat old topics.
""" if memory_agent else ""

if use_buffer and use_gcs:
    _BUFFER_IMAGE_NOTE = """
  Buffer's create_post CAN include an image now: pass the public HTTPS URL
  that draft_agent got from upload_image — never a local file path, Buffer's
  servers cannot fetch those (confirmed live: "Invalid post: Image could not
  be read from its URL")."""
elif use_buffer:
    _BUFFER_IMAGE_NOTE = """
  IMPORTANT: Buffer's create_post must be TEXT ONLY — never pass an image
  asset to it. Buffer's servers cannot fetch a locally-generated image file
  (confirmed live: "Invalid post: Image could not be read from its URL"),
  and attaching one fails the whole post."""
else:
    _BUFFER_IMAGE_NOTE = ""

_IMAGE_REUSE_NOTE = (
    """
- If a post needs an image that was already generated earlier in this
  conversation (e.g. revising the draft for a different platform, or an
  earlier hosted URL may have expired), call draft_agent again and tell it
  to call upload_image on that exact existing local image_path — never ask
  the user to find or paste an image link themselves, and never accept a
  gs:// path from them (it isn't a fetchable URL). Only ask the user for a
  link if upload_image itself returns an error."""
    if use_gcs
    else ""
)

if use_linkedin and use_buffer:
    _BUFFER_ROUTING = f"""

Two ways to publish, choose based on what the user wants:
- LinkedIn tool (create_post): an immediate post to LinkedIn only, right now.
  Can include a generated image (pass the local image_path).
- Buffer tools: use these instead if the user wants to SCHEDULE a post for
  later, or wants to post to a platform OTHER than LinkedIn (X, Facebook, etc.)
  that they've connected in Buffer. If unsure which platforms are connected,
  ask Buffer to list channels first.{_BUFFER_IMAGE_NOTE}
"""
elif use_buffer:
    _BUFFER_ROUTING = f"""

Publish exclusively via Buffer's tools (POST_VIA=buffer) — there is no direct
LinkedIn tool in this configuration. For an immediate post, ask Buffer to
publish now rather than schedule. If unsure which platforms are connected,
ask Buffer to list channels first.{_BUFFER_IMAGE_NOTE}
"""
else:
    _BUFFER_ROUTING = ""

root_agent = Agent(
    name="social_poster",
    model=ORCHESTRATOR_MODEL,
    description="Orchestrates research, drafting, and publishing of social media posts.",
    instruction=f"""You orchestrate turning an idea into a social media post.
You do not research or draft yourself — you route work to specialist tools.

IMPORTANT — before ANY other step, including memory consultation: if the
user's message contains pasted or uploaded source text with real people's
personal details (names, emails, phone numbers, locations), run
redact_pii_text on that text immediately, show the user the cleaned version,
and only ever work from the cleaned version afterwards.

Workflow:{_MEMORY_ROUTING}
1. If the idea needs facts, dates, or context, call research_agent first.
2. Call draft_agent to write the post (and generate an image if wanted). It
   reads the research notes automatically; pass it the idea, target platform,
   and any user preferences.
3. Show the finished draft to the user and ask for approval. For revisions,
   call draft_agent again with the feedback.
4. ALWAYS get explicit approval before any posting action.
{_IMAGE_REUSE_NOTE}
{_BUFFER_ROUTING}
Posting rules (strict):
- Only post AFTER the user has explicitly approved the exact draft shown to
  them in this conversation. "Yes", "post it", "approved" counts; silence,
  topic changes, or enthusiasm about the idea does not.
- If the draft changes after approval, show it again and get fresh approval.
- After posting, share the returned post URL (or confirmation) with the user.
""",
    tools=[
        *([AgentTool(agent=memory_agent)] if memory_agent else []),
        AgentTool(agent=research_agent),
        AgentTool(agent=draft_agent),
        guardrails.redact_pii_text,
        *([linkedin_toolset, linkedin_post_toolset] if use_linkedin else []),
        *([buffer_toolset, buffer_post_toolset] if use_buffer else []),
    ],
    before_agent_callback=_init_stage,
    before_model_callback=guardrails.guard_input,
    after_model_callback=guardrails.guard_output,
    before_tool_callback=[_delay_buffer_post, guardrails.redact_before_post],
    after_tool_callback=_track_stage,
    after_agent_callback=_stage_after_agent,
)
