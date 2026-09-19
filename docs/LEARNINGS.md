# LEARNINGS

Every gotcha hit while building this. Each entry should become either a lab
step, a deliberate-error callout, or a slide caveat. Format:
**date — pillar — what happened — what to tell attendees.**

(Continues the pattern from the prior POC's own `LEARNINGS.md`, using pillar
names — Build/Scale/Govern/Optimize — instead of phase numbers, per the
terminology sweep.)

---

<!-- Add entries below, newest first. -->

**2026-09-18 — Govern — the Govern pillar has a hard dependency chain, and it
starts in Scale: Agent Runtime deploy → Agent Registry → Governance Policy.**
Tried to attach a `SemanticGovernancePolicy` to a hand-registered agent (an
`A2A_AGENT_CARD` pushed into Agent Registry via `services.create`) and got
`FAILED_PRECONDITION` / `SEMANTIC_GOVERNANCE_POLICY_AGENT_NOT_CONFIGURED`:
*"Agent ... is not configured with a valid RuntimeIdentity."* Pointing the
policy straight at the `reasoningEngines/{id}` resource fails differently —
`SEMANTIC_GOVERNANCE_POLICY_AGENT_INVALID_NAME`, *"Expected format:
projects/*/locations/*/agents/*"*. The policy's `agent` field wants an **Agent
Registry** name, and that registry entry must carry a runtime identity. **What
actually works**: deploying to Agent Runtime with `identity_type=AGENT_IDENTITY`
**auto-registers the engine in Agent Registry** — no manual registration step at
all. The auto-created entry carries exactly what the policy API demands, in an
`attributes` block: `agentregistry.googleapis.com/system/RuntimeIdentity`
(the `principal://agents.global.proj-{NUM}.system.id.goog/...` principal),
`.../RuntimeReference` (the `//aiplatform.googleapis.com/...reasoningEngines/{id}`
URI) and `.../Framework` (`google-adk`). Confirmed end to end: deployed a
minimal engine, watched `spike-probe-engine` appear in
`agents.list` unprompted, then created a policy against that registry name
successfully. **Codelab material**: Lab 3 can't be a standalone Govern lab —
it needs Lab 2's deployed, identity-enabled engine as its literal
prerequisite. That's a feature, not a problem: it's the cleanest possible
demonstration of why the pillars are ordered the way Google orders them. Say
it out loud in the theory section rather than letting attendees discover it
as a 400.

**2026-09-18 — Govern — the Governance Policy Engine is a singleton you must
provision first, the provisioning method is undocumented, and it's
region-gated.** `projects.locations.semanticGovernancePolicies` is a real
**v1** (not beta) surface with full CRUD, and `list` returns `200 {}` in
every region tried — which is misleading, because `create` then fails with
`SEMANTIC_GOVERNANCE_POLICY_ENGINE_NOT_ENABLED_IN_REGION`. There is a
singleton `projects/{p}/locations/{l}/semanticGovernancePolicyEngine`
resource gating all of it, and it starts `INACTIVE`. **Region reality**
(checked directly): the engine service exists in `us-central1`, and is
flatly unavailable in **`global` and `europe-west2`** — *"The Semantic
Governance Policy Engine service is not available in location X."* A
London-based camp cannot do this in an EU region as things stand.
**The provisioning method is not in the discovery doc**: the API listing
shows only `semanticGovernancePolicyEngine.deprovision` — no `get`, no
`provision`. Both exist anyway. `GET` on the singleton works fine, and
`:provision` / `:activate` / `:enable` all 404. What actually provisions it
is a plain **`PATCH` with `?updateMask=gatewayConfigs`** (an undocumented
`UpdateSemanticGovernancePolicyEngineOperationMetadata` LRO); a body of
`{"gatewayConfigs":{"default":{}}}` took it `INACTIVE` → `PROVISIONING` →
`ACTIVE` in about **3 minutes**, standing up a PSC service attachment behind
the scenes. Once `ACTIVE`, policy creation works immediately. **Codelab
material**: budget a few minutes of dead time for this in Lab 3 and kick it
off before the theory segment, not during the hands-on. And don't trust a
discovery doc's method list as complete for these newer surfaces — probe the
verbs directly.

**2026-09-18 — Govern — Governance Policies are natural-language
constraints, and they bind to the agent's identity principal.** Once the
engine was `ACTIVE`, this created cleanly and came back resolved: a
`naturalLanguageConstraint` of *"The agent must never publish a social post
without explicit human approval"*, an `agent` pointing at the Agent Registry
name, and — populated by the server, not by me — an `agentIdentity` of
`principal://agents.global.proj-{NUM}.system.id.goog/resources/aiplatform/.../reasoningEngines/{id}`.
The policy resource also supports `mcpTools` (scoping a policy to specific
tools on a specific registered `mcpServers/{id}`) and
`agentResponseCustomization` (what the user sees when the policy denies).
**Codelab material**: this is the single best Govern demo in the whole
platform — the policy is a plain English sentence, versioned as a cloud
resource, bound to a cryptographic agent identity. It contrasts beautifully
with `guardrails.py`'s hand-rolled Model Armor/DLP REST calls: same intent,
completely different level of abstraction. Not verified in this spike:
whether the constraint is actually *enforced* at runtime on a live turn —
only that the policy resource creates and resolves correctly. Don't claim
enforcement on stage without testing a denied turn first.

**2026-09-18 — Govern — Agent Gateway provisions in seconds, not days — but
getting a usable serving endpoint is a separate, unsolved problem.** The
fear going in was that Agent Gateway would be a multi-day networking
project. It isn't, at the resource level: `gcloud network-services
agent-gateways` is real (though it has **no `create` subcommand** — only
`delete`/`describe`/`export`/`import`/`list`; the REST API *does* have
`create`), and a `googleManaged` gateway with **no VPC, subnet, or DNS
config at all** created in **3.4 seconds**. Two format gotchas cost more time
than the provisioning did: the `registries` field's own docstring shows a
trailing slash (`.../locations/{location}/`) which the validator **rejects**,
and `agentConnectivityTemplate` must use the project **number**, not the
project ID, or you get a confusing "incorrect resource name" error naming a
format that looks identical to what you sent. **Where it stops**: the
output-only `agentGatewayCard` (which carries the `mtlsEndpoint` and root
certificates clients would actually connect to) stayed empty. Creating an
`AgentConnectivityTemplate` (`CENTRALIZED` / `CLIENT_TO_AGENT` / `PUBLIC` /
`CLOUD_RUN`) succeeded, but PATCHing it onto the gateway returned a
successful LRO whose result **silently did not persist the field** — the
gateway read back without it, and without a card. Also worth knowing: the
template's `agentCompute` enum is `GKE` / `CLOUD_RUN` / `BORG` — there is
**no Agent Runtime option**, so a gateway fronts your Cloud Run or GKE
deployment, not your `reasoningEngine`. **Codelab material**: Lab 3 should
take v5's stated fallback for this item (console/CLI walkthrough, concept
explained) — but the honest version is better than the cautious one. Show
the real resource being created live in 3 seconds, show the empty card, and
name exactly what's missing. "Here is the integration boundary" is a more
useful lesson than either "it works" or "it's too hard."

**2026-09-18 — Optimise — Online Evaluation is real and in v1, but creating
an evaluator is gated in this project; multi-turn arrives as `sessionScope`,
not as a separately-named product.** Two findings here, one good and one
blocking. **Good**: there is no separate "Multi-Turn AutoRaters" API to hunt
for — multi-turn evaluation is a *mode* of `onlineEvaluators`. The
`cloudObservability` block takes either `traceScope` (evaluate single traces)
or **`sessionScope`** — explicitly *"a group of traces with the same
conversation ID"* — with an `inactivityTrigger.threshold` deciding when a
conversation counts as finished, and rich filter predicates (`userTurns`,
`toolCalls`, `toolCallErrors`, `modelCallErrors`, `totalTokenUsage`,
`duration`). The data source is Cloud Trace + Cloud Logging via OpenTelemetry
semconv ≥ `1.39.0`, which lines up directly with the tracing already
documented in `docs/observability.md`. It samples on a fixed 10-minute
interval. **Blocking**: every well-formed `onlineEvaluators.create` returns a
bare `400 INVALID_ARGUMENT` — *"Request contains an invalid argument."* with
no `details`, no field violation, nothing. Ruled out by bisection: it is not
the metric (six predefined names, plus inline LLM-based, all identical), not
the scope type, not the region (`us-central1`, `us-east4`, `europe-west4`,
`global` all identical), and **not the agent** — it fails the same way
against a real, live, identity-enabled engine as against a fabricated
resource ID. The tell: an **empty body `{}` returns the exact same error**,
while genuinely malformed payloads earlier produced a *different*, specific
failure (a `500` naming the missing `inactivityTrigger.threshold`, and a
detailed 400 for an unknown query param). So the request is parsed and then
refused wholesale — an allowlist/preview gate surfacing as
`INVALID_ARGUMENT` rather than `PERMISSION_DENIED`. Read paths work fine
(`list` is `200 {}`). One more regional note: `onlineEvaluators` is **not
supported in `europe-west2`** at all (`"Location europe-west2 is not
supported."`) while `evaluationRuns`/`evaluationSets`/`evaluationMetrics`
answer there. The Python SDK confirms the gap from the other direction:
`vertexai.Client().evals` has **no online-evaluator method whatsoever**.
**Codelab material**: take v5's stated fallback for this item and present it
honestly — the API surface is worth showing (it's a genuinely good design),
but don't plan a live demo on it without getting the project allowlisted
first.

**2026-09-18 — Optimise — multi-turn *simulation* works today and is the
right substitute for the gated Online Evaluation demo.**
`vertexai.Client().evals.generate_conversation_scenarios(...)` ran
successfully against this project on the first properly-formed call and
returned genuinely usable material for social_poster: two scenarios, each
with a `starting_prompt`, a `test_case_title`, and a multi-turn
`conversation_plan` — including one where the simulated user *corrects a
figure mid-conversation and asks for an addition*, then approves the revised
draft. That is exactly the approval-gate behaviour `evals/golden.json`
already tries to pin down, generated automatically. **API gotcha**: the
`UserScenarioGenerationConfig` field is **`count`**, not `user_scenario_count`
— setting the latter gets you a backend error insisting
`user_scenario_count cannot be None`, because the SDK never forwarded it.
Unlike `onlineEvaluators`, this service returns detailed, field-level
validation errors (a full `DataFoundryService.GenerateUserScenarios` message),
which is itself a useful signal that it's genuinely enabled rather than
gated. Also present and unexplored in the same SDK module:
`create_evaluation_run`, `batch_evaluate`, `generate_rubrics` (rubric-based
autoraters), `generate_loss_clusters`, and a separate `client.skills` module
(create/get/list/update/delete/retrieve/revisions) — the Agent Skills surface,
which matters for the `agents-cli` skills question in the plan. **Codelab
material**: Lab 4's hands-on can be *simulation → eval run*, which works
today, with Online Evaluation shown as the API it grows into. That's a
stronger lab than `adk eval` alone and needs no allowlist.

**2026-09-18 — Govern — deprovisioning the Governance Policy Engine failed
and left it stuck; treat provisioning as a one-way door for the day.**
Cleanup after the spike: the policy deleted fine, but
`semanticGovernancePolicyEngine:deprovision` returned an LRO that completed
with `error: {code: 13, message: "INTERNAL"}`, leaving the engine
`state=FAILED` with `gatewayConfigs: {default: {state: DEPROVISIONING}}` and
its PSC service attachment still present. Every subsequent write — a retry of
`:deprovision`, and a `PATCH` clearing `gatewayConfigs` to `{}` — is refused
with `FAILED_PRECONDITION` / *"Precondition check failed."* while the gateway
config sits in `DEPROVISIONING`. The `FAILED` enum's own description
anticipates this: *"The engine may have partial infrastructure that needs
explicit deprovision."* Likely relevant: the `default` gateway config was
`state: FAILED` from the moment provisioning finished — an empty
`{"default":{}}` gateway config on the project's default VPC apparently
can't actually come up, even though the engine as a whole reported `ACTIVE`
and policies worked fine. **Two lessons**: (1) provision the engine with
`gatewayConfigs` you actually intend to use, or expect a half-failed gateway
riding along; (2) don't provision this in a shared/live project casually
assuming you can back it out the same day. **Codelab material**: if Lab 3
has attendees provision this in their own projects, they need to be told
up front that it may not deprovision cleanly — and the teardown checklist
needs to say so rather than implying a tidy `delete`.
**Final state, checked ~90 minutes later**: still `FAILED` /
`DEPROVISIONING`, with `updateTime` frozen at the moment the deprovision
LRO failed — it is genuinely wedged, not slowly draining. **The reassuring
half**: nothing landed in the customer project. `gcloud compute
forwarding-rules list` and `network-attachments list` are both empty, Cloud
DNS was never even enabled on the project, and the surviving
`pscServiceAttachment` lives in a Google **tenant** project
(`...-tp`), not in the user's VPC. So the residue is entirely on the
Google-managed side — no stray billable networking resources to hunt down,
just a control-plane resource that won't return to `INACTIVE` without
Google's help.

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
