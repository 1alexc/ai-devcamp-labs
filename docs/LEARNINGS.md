# LEARNINGS

Every gotcha hit while building this. Each entry should become either a lab
step, a deliberate-error callout, or a slide caveat. Format:
**date — pillar — what happened — what to tell attendees.**

(Continues the pattern from the prior POC's own `LEARNINGS.md`, using pillar
names — Build/Scale/Govern/Optimize — instead of phase numbers, per the
terminology sweep.)

---

<!-- Add entries below, newest first. -->

**2026-09-18 — Scale — local `adk web` testing is completely unaffected by
everything below.** Worth stating explicitly, not just implying: every
issue in this file was hit while *deploying to a regional Agent Runtime
instance*. None of them occur during normal local development
(`uv run adk web`, `GOOGLE_CLOUD_LOCATION=global`, `gemini-flash-latest`) —
that path is exactly as simple as it's always been. **Codelab material**:
be explicit in Lab 2 that everything before the actual deploy step (writing
the agent, testing it locally, Sessions 1's whole workshop) carries none of
this risk — the friction is real but scoped to one specific step, not
pervasive. Attendees should not come away thinking ADK itself is fragile.

**2026-09-18 — Scale — `AgentTool` breaks with `MALFORMED_FUNCTION_CALL`
when paired with a broad MCP toolset, on pinned regional models.** Deploying
`social_poster` to Agent Runtime, the orchestrator's
`AgentTool(agent=draft_agent)` call came back
`print(default_api.draft_agent(request='...'))` instead of a real function
call — with both `gemini-2.5-flash` and `gemini-2.5-pro`. Root-caused by
bisection (isolate each real component one at a time, not guesswork):
a standalone `FunctionTool` was fine; `AgentTool` alone was fine; two
`AgentTool`s together were fine; the real `draft_agent` under a minimal
orchestrator was fine; the real `root_agent` trimmed to just
`AgentTool(draft_agent)` was fine. Adding `buffer_toolset` back broke it
immediately. Buffer's MCP server exposes **20 tools total**
(`get_account`, `list_channels`, `get_channel`, `list_posts`, `get_post`,
`list_ideas`, `list_idea_groups`, `create_idea`, `create_post`, `edit_post`,
`delete_post`, `get_aggregated_post_metrics`, `list_post_templates`,
`get_post_template`, `create_post_template`, `update_post_template`,
`delete_post_template`, `introspect_schema`, `execute_query`,
`execute_mutation`) — `buffer_toolset`'s filter (`tool.name != "create_post"`)
exposed 19 of them, including a full GraphQL escape hatch the agent never
uses. That much unrelated schema complexity alongside a compositional
`AgentTool` call is what broke the pinned models' function-calling. **Fix**
(shipped in `agent.py`): narrow `buffer_toolset` to an explicit allowlist —
`["list_channels", "get_account"]`, the only two tools the orchestrator's
instructions actually reference. Confirmed fixed end-to-end, both locally
(`AdkApp`) and against a real deployed engine: draft → approve →
`get_account` → `list_channels` → `create_post` →
`adk_request_confirmation`, no malformed calls. **Codelab material**: this
is a genuinely good teaching moment for "least-privilege tool exposure,"
separate from the Govern pillar's own guardrail content — an LLM having
`execute_mutation`/`delete_post` in its context when it never uses them is
a real reliability (and security) smell, not just a Scale-deploy quirk.
**Verified safe**: Lab 1's own code sample wires `buffer_toolset` with no
`tool_filter` at all (all 20 Buffer tools exposed) — tested that exact
setup with `gemini-flash-latest` locally (draft → approve →
`adk_request_confirmation`), clean, no malformed calls. Attendees following
Lab 1 as written won't hit this; the bug is specific to the pinned
`gemini-2.5-flash`/`gemini-2.5-pro` snapshots Agent Runtime forces
regionally, not to `gemini-flash-latest`. **No tool-count threshold either**
— it's model + schema-complexity specific, not "N tools breaks." Worth
saying explicitly in Lab 2, not implying a number: don't tell attendees
"keep it under N tools," tell them "match the tool's actual schema
complexity to what the model needs, and expect pinned/older models to be
less tolerant of large or generic (e.g. GraphQL-style) tool schemas than
whatever `-latest` currently resolves to."

**2026-09-18 — Scale — `gemini-flash-latest` only resolves via the
`global` endpoint; every region 404s.** Confirmed directly: a plain
`generate_content` call with `gemini-flash-latest` 404s
("Publisher model ... was not found") in both `europe-west2` and
`us-central1`, while `gemini-2.5-flash`/`gemini-2.5-flash-lite`/
`gemini-2.5-pro` all work fine regionally. `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION` are **reserved** `env_vars` names on Agent
Runtime — rejected with `FAILED_PRECONDITION` if set explicitly — so
there's no way to force `global` model resolution for a deployed agent;
a fully-qualified `.../locations/global/...` model resource path 404s too.
Pinned regional versions are the only option once deployed. **This exact
bug also existed in the attendee setup guide** —
`GOOGLE_CLOUD_LOCATION=europe-west1` in the `.env` template would have
404'd for every attendee in Session 1, nothing to do with Agent Runtime at
all. Fixed to `global` in the live file. **Codelab material**: Lab 2 needs
an explicit "pin your models before deploying" step; don't let attendees
discover this via a cryptic 404 mid-workshop the way I did.

**2026-09-18 — Scale — Agent Runtime packaging: `requirements` needs both
`google-cloud-aiplatform[...,adk]` and an explicit `google-adk[a2a,eval,mcp]`
pin, or the resolver silently picks a broken version.**
`google-cloud-aiplatform[agent_engines,adk]==X`'s own `adk` extra only
constrains `google-adk` to `<3.0.0,>=1.5.0` (checked via PyPI metadata) — a
wide range. Without also pinning `google-adk[a2a,eval,mcp]==2.4.0`
explicitly alongside it, the resolver picked a different ADK version whose
`google.adk.tools.mcp_tool` module doesn't export `McpToolset` — a
`try/except ImportError` in that module's `__init__.py` swallows the real
failure silently (just logs "MCP Tool is not installed" at debug level),
so it surfaces one confusing layer up, as an unguarded `ImportError` in
`agent.py` itself. The `[mcp]` extra specifically matters: `mcp` (the
actual protocol client library) is gated behind it, and is only present in
local dev by accident, via `fastmcp` (an unrelated top-level dependency
pulling it in transitively). Also needed explicitly: `cloudpickle`,
`pydantic`, matching locally-installed versions. **Codelab material**: give
attendees the exact working `requirements` list in Lab 2 rather than having
them rediscover this — four failed deploys before this worked.

**2026-09-18 — Scale — Secret Manager: the IAM grant needed is the platform
service agent, not the Agent Identity principal, and there's a real
chicken-and-egg problem.** `BUFFER_API_KEY` was initially passed as a
plain-text `env_vars` value — caught in review (don't do this; it's visible
in clear text in the console). Switching to a Secret Manager reference
(`{"secret": SECRET_ID, "version": "latest"}`) first failed with
"could not access one or more secrets ... Grant the **runtime service
account**" — not the Agent Identity principal granted `secretAccessor`
initially. The actual principal is
`service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`
(the platform-managed Agent Engine service agent, matching what
`adk-deploy-guide`'s own Secret Manager section already documents — worth
reading before assuming). Separately: granting the specific Agent Identity
*principal* doesn't work for a **new** deployment either, since that
principal only exists after a successful create — grant the project-wide
`principalSet://agents.global.proj-{NUM}.system.id.goog/attribute.platformContainer/aiplatform/projects/{NUM}`
form instead if the resource ID isn't known yet. **Codelab material**: give
attendees both grants (service agent + principalSet) up front in Lab 2,
don't make them hit the chicken-and-egg problem themselves.

**2026-09-18 — Scale — HITL confirmation pause/resume is confirmed working
against a real deployed Agent Runtime engine — this was the actual
open question, and it's a clean pass.** Built a minimal isolated test
agent (one `FunctionTool` named `send_message` with
`require_confirmation=True`) specifically to test this mechanism without
the `AgentTool`/Buffer noise above. Paused correctly on
`adk_request_confirmation`, and resumed correctly — the tool actually
executed — after relaying a `FunctionResponse` with
`{"confirmed": true}` into a **separate, later** `async_stream_query` call
on the same `session_id`. This is exactly the mechanism a CopilotKit bridge
would need. Confirmed again afterwards against the real, complete
`social_poster` agent once the `AgentTool` bug above was fixed. **Codelab
material**: this is the reassuring finding, not just the gotchas — Agent
Runtime's confirmation-gate mechanism genuinely works as documented, once
the unrelated packaging/model/tool-scope issues above are out of the way.

**2026-09-17 — General — an early test accidentally published a real post
via Buffer.** A test script assumed `DRY_RUN=true` covered all posting
paths; it doesn't — Buffer's remote MCP server has no dry-run concept at
all (only the local LinkedIn MCP server respects `DRY_RUN`), and the
script's `POST_VIA` override was a no-op because the loaded `.env` already
set it. **Fix, now shipped**: `BUFFER_REVIEW_DELAY_MINUTES` (default 60,
`agent.py`) forces every real Buffer post to `customScheduled` at least N
minutes out, regardless of what mode the model picks — a real safety net,
not just a documented caution. **Codelab material**: this is worth a
callout in whichever lab first wires up Buffer — dry-run assumptions don't
automatically extend to every posting path, check each one specifically.
