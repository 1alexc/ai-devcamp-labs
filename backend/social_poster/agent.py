"""Phase 5: the Phase 4 multi-agent system, now governed.

Same architecture as Phase 4 (orchestrator over research_agent, draft_agent,
optional memory_agent + posting toolsets), plus three guardrail layers from
guardrails.py wired onto EVERY agent:

- Model Armor screens each model call's input (before_model_callback) and
  output (after_model_callback); MEDIUM_AND_ABOVE findings get a friendly
  refusal instead of a model call.
- Cloud DLP redacts PII (person names, emails, phone numbers, locations) from
  final drafts on their way into create_post (before_tool_callback).

All three no-op when MODEL_ARMOR_TEMPLATE_ID is unset, so Phase 4 behaviour
is one env var away for A/B demos.
"""

import os
import pathlib
import sys
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
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
from mcp import StdioServerParameters

from . import guardrails
from .tools import generate_image

PHASE_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = PHASE_ROOT / "skills"
MCP_SERVER = PHASE_ROOT / "mcp" / "linkedin_server.py"  # McpToolset needs absolute paths

# --- Per-agent model pinning (Phase 6) ----------------------------------------
# Each agent's model comes from env so cost/latency can be tuned per role
# without touching code, and re-run through evals to confirm parity. Defaults
# match Phase 4/5 (all flash). The Week 4 experiment: move the two agents that
# don't need deep reasoning (research summarisation, the routing orchestrator)
# to flash-lite, keep draft_agent on flash where quality shows most.
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

draft_agent = Agent(
    name="draft_agent",
    model=DRAFT_MODEL,
    description="Writes and revises the social media post draft, and generates images.",
    instruction="""You write social media post drafts.

Research notes from an earlier step (may be empty):
{research_notes?}

Style guidance from the user's past posts (may be empty):
{memory_notes?}

Rules:
1. Load and follow the relevant skills before drafting: post-formatter
   (structure), platform-style (rules for the target platform), and
   brand-voice (tone).
2. Ground the draft in the research notes when they exist; never invent facts.
3. If asked for an image, load and follow the poster-style skill, then call
   generate_image with a detailed visual description and report the saved
   file path.
4. Return ONLY the draft (and image path if any) — no commentary about
   approvals or posting; the orchestrator handles that.""",
    tools=[skill_toolset, generate_image],
    output_key="current_draft",
    before_model_callback=guardrails.guard_input,
    after_model_callback=guardrails.guard_output,
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

# --- Posting toolsets (unchanged from phase 2/3) ------------------------------
linkedin_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,  # this venv's python
            args=[str(MCP_SERVER)],
            env={
                "DRY_RUN": os.environ.get("DRY_RUN", "true"),
                "LINKEDIN_ACCESS_TOKEN": os.environ.get("LINKEDIN_ACCESS_TOKEN", ""),
            },
        ),
    ),
    tool_filter=["get_profile", "create_post"],
    # Framework-level approval gate on anything that posts (see LEARNINGS,
    # @experimental in ADK 2.4.0).
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

buffer_toolset = None
if BUFFER_API_KEY:
    buffer_toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://mcp.buffer.com/mcp",
            headers={"Authorization": f"Bearer {BUFFER_API_KEY}"},
        ),
        require_confirmation=True,
    )
elif POST_VIA == "buffer":
    raise RuntimeError("POST_VIA=buffer requires BUFFER_API_KEY to be set.")

use_linkedin = POST_VIA != "buffer"
use_buffer = buffer_toolset is not None and POST_VIA != "linkedin"

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

if use_linkedin and use_buffer:
    _BUFFER_ROUTING = """

Two ways to publish, choose based on what the user wants:
- LinkedIn tool (create_post): an immediate post to LinkedIn only, right now.
  Can include a generated image.
- Buffer tools: use these instead if the user wants to SCHEDULE a post for
  later, or wants to post to a platform OTHER than LinkedIn (X, Facebook, etc.)
  that they've connected in Buffer. If unsure which platforms are connected,
  ask Buffer to list channels first.
  IMPORTANT: Buffer's create_post must be TEXT ONLY — never pass an image
  asset to it. Buffer's servers cannot fetch a locally-generated image file
  (confirmed live: "Invalid post: Image could not be read from its URL"),
  and attaching one fails the whole post. If the user wants an image
  included, use LinkedIn's own tool for that post instead, or tell them
  Buffer posting here is text-only for now.
"""
elif use_buffer:
    _BUFFER_ROUTING = """

Publish exclusively via Buffer's tools (POST_VIA=buffer) — there is no direct
LinkedIn tool in this configuration. For an immediate post, ask Buffer to
publish now rather than schedule. If unsure which platforms are connected,
ask Buffer to list channels first.
IMPORTANT: Buffer's create_post must be TEXT ONLY — never pass an image
asset to it. Buffer's servers cannot fetch a locally-generated image file
(confirmed live: "Invalid post: Image could not be read from its URL"), and
attaching one fails the whole post. If the user asks for an image, generate
it for the local gallery only and tell them Buffer posting is text-only.
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
        *([linkedin_toolset] if use_linkedin else []),
        *([buffer_toolset] if use_buffer else []),
    ],
    before_agent_callback=_init_stage,
    before_model_callback=guardrails.guard_input,
    after_model_callback=guardrails.guard_output,
    before_tool_callback=guardrails.redact_before_post,
    after_tool_callback=_track_stage,
    after_agent_callback=_stage_after_agent,
)
