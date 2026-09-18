# LEARNINGS

Every gotcha hit while building this. Each entry should become either a lab
step, a deliberate-error callout, or a slide caveat. Format:
**date — pillar — what happened — what to tell attendees.**

(Continues the pattern from the prior POC's own `LEARNINGS.md`, using pillar
names — Build/Scale/Govern/Optimize — instead of phase numbers, per the
terminology sweep.)

---

<!-- Add entries below, newest first. -->

**2026-09-18 — Build — the GCS "public bucket" setup in `.env.example`
doesn't work when Public Access Prevention is on, and this project's own
bucket is a live example.** `upload_image` (tools.py) returns a plain
`storage.googleapis.com` object URL on the assumption the bucket has
`allUsers:objectViewer` — `.env.example`'s own setup steps tell you to grant
it. Tried it live on this project's `social-spark-creatives` bucket:
`gcloud storage buckets add-iam-policy-binding ... --member=allUsers
--role=roles/storage.objectViewer` → `HTTPError 412: The member bindings
allUsers and allAuthenticatedUsers are not allowed since public access
prevention is enforced.` Checked the bucket's actual IAM policy directly
(`gcloud storage buckets get-iam-policy`) — no `allUsers` binding exists at
all, confirming every hosted URL this app has ever produced 403s to a plain
browser fetch; nobody had actually opened one until embedding it as a chat
image made that visible. **Public Access Prevention is a common default on
managed/shared GCP projects** (this DevCamp project has it) — don't assume
`.env.example`'s bucket instructions will work as written; check first with
`gcloud storage buckets describe gs://YOUR_BUCKET
--format="value(iamConfiguration.publicAccessPrevention)"`. **Fix (shipped,
`agent.py`)**: don't depend on the hosted URL for the in-chat image
*preview* at all — a new `current_image_display_url` state key
(`BACKEND_PUBLIC_ORIGIN` + `/outputs/<filename>`, the same static route
`backend/main.py` already serves for `PostGallery.tsx`) is computed
server-side in `_track_image_state` and the orchestrator's instruction
embeds that exact pre-resolved URL verbatim as Markdown
(`![post image]({{current_image_display_url?}})`) — deterministic, no
reliance on the model correctly picking/copying a URL out of draft_agent's
free-text result. The GCS hosted URL is untouched and still what
`upload_image`/Buffer's `create_post` use for actual posting — this fix is
preview-only. **Codelab material**: worth a callout in whichever lab
introduces GCS image hosting — "this bucket step may 412 on managed
projects, and the in-chat preview doesn't need it to work" saves attendees
from concluding the whole feature is broken.
**Follow-up (shipped, `tools.py`)**: fixed the actual posting path too, not
just the preview — `upload_image` now returns a V4 *signed* URL instead of
a plain public one, signed via service-account impersonation
(`google.auth.impersonated_credentials`, a new `GCS_SIGNING_SERVICE_ACCOUNT`
env var) rather than a downloaded key file, which this project's
credentials policy doesn't allow. Confirmed the impersonation path is
required, not optional, for local dev specifically: this project's ADC is
an `authorized_user` (a human Google account via `gcloud auth
application-default login`), and only service-account credentials carry a
signable identity — a human identity can't self-sign at all, it has to
borrow a service account's identity for exactly that. Needs a one-time,
human-run IAM setup (create the uploader SA, grant it
`storage.objectAdmin` on the bucket, grant the calling identity
`iam.serviceAccountTokenCreator` on that SA) — first shipped as raw gcloud
commands in `.env.example`'s comments, then promoted to an actual
idempotent script (`gcs-setup.sh`, repo root) once it became
clear a comment block isn't discoverable enough for attendees to actually
find and run correctly by hand. **This is NOT part of Lab 1's taught
curriculum** — Lab 1
deliberately keeps Buffer posts text-only and only attaches images via
LinkedIn's own tool (Step 7), specifically to avoid this exact complexity;
this fix is for the reference app's more advanced capability, so it's
documented as an explicitly optional step in
`docs/devcamp-attendee-setup-guide.md` (Step 6), not folded into the
mandatory pre-camp steps everyone has to do.

**2026-09-18 — Scale — `useCopilotAction`/`useRenderTool`-style rendering
cannot see tool calls nested inside an `AgentTool`.** Tried rendering
`generate_image`'s result inline in chat by registering a
`useCopilotAction({ name: "generate_image", available: "disabled", render:
... })` (the same pattern `ConfirmAction.tsx` uses for ADK's synthetic
`adk_request_confirmation` call). It never fired. Root-caused by reading the
raw AG-UI SSE stream directly (`read_network_requests` on the
`/api/copilotkit` POST): the orchestrator's stream only carries
`TOOL_CALL_START`/`ARGS`/`END`/`RESULT` for the top-level `draft_agent`
`AgentTool` call — `generate_image` and `upload_image`, called *inside*
`draft_agent`'s own sub-agent execution, never get their own top-level
AG-UI tool-call events at all; they're absorbed into `draft_agent`'s single
result. Frontend tool-call-name-matching renderers fundamentally cannot see
these — there is no event to match against, at any name. **What DOES
survive nested calls**: `STATE_DELTA`/`STATE_SNAPSHOT` events — the
`current_image_path`/`current_image_url` writes from `_track_image_state`
(an `after_tool_callback`, running inside the nested call) showed up
correctly in the top-level stream every time, confirmed from the same raw
SSE capture. **Codelab material**: session state (`output_key`,
`tool_context.state`) is the only reliable channel for surfacing anything
that happens inside a sub-agent's `AgentTool` call to the frontend — don't
reach for tool-call rendering hooks for that, they're scoped to the
orchestrator's own direct tool calls only.

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
