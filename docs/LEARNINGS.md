# LEARNINGS

Every gotcha hit while building this. Each entry should become a lab step, a deliberate-error
callout, or a slide caveat.

Entries are grouped by pillar.

---

<!-- BEGIN: General -->
## General

*Applies to every pillar. Ships with every tag.*

<!-- Add new General entries here, newest first. -->

### `agents-cli` coverage is genuinely uneven across the four pillars, and that unevenness is worth teaching rather than hiding

Checked per pillar rather than assumed: **Build** — `agents-cli setup` is
valuable (installs the CLI plus ADK skills into whichever coding agent the
attendee has), but `scaffold create`/`enhance` does *not* fit this repo, as
already established in `docs/cloud-run-deploy.md` back in August (it clobbers
the hand-pinned `pyproject.toml` and expects `app/`, not
`backend/social_poster/`). **Scale** — `agents-cli deploy` is the documented
path, full stop (with a hand-written manifest, since `enhance` rejects the
nested agent directory; see the Scale entry on it). **Govern** — no commands exist for Agent Gateway or Semantic
Governance; it's REST/gcloud/Terraform/Console. **Optimise** — `agents-cli
eval` fits. **Codelab material**: standardise on `agents-cli` where it exists
so attendees learn one tool rather than four idioms, and say out loud in Lab 3
why Govern drops to raw APIs. "Here is where the tooling is mature and here is
where it isn't yet" is more useful to someone taking this back to their own
org than a uniform-looking set of commands would be.

### An early test accidentally published a real post via Buffer

A test script assumed `DRY_RUN=true` covered all posting
paths; it doesn't — Buffer's remote MCP server has no dry-run concept at
all (only the local LinkedIn MCP server respects `DRY_RUN`), and the
script's `POST_VIA` override was a no-op because the loaded `.env` already
set it. **Fix, now shipped**: `BUFFER_REVIEW_DELAY_MINUTES` (default 60,
`agent.py`) forces every real Buffer post to `customScheduled` at least N
minutes out, regardless of what mode the model picks — a real safety net,
not just a documented caution. **Codelab material**: this is worth a
callout in whichever lab first wires up Buffer — dry-run assumptions don't
automatically extend to every posting path, check each one specifically.

<!-- END: General -->

<!-- BEGIN: Build -->
## Build

*Agent design, tools, skills, MCP, images, hosting for previews.*

<!-- Add new Build entries here, newest first. -->

### The GCS "public bucket" setup in `.env.example` doesn't work when Public Access Prevention is on, and this project's own bucket is a live example

`upload_image` (tools.py) returns a plain
`storage.googleapis.com` object URL on the assumption the bucket has
`allUsers:objectViewer` — `.env.example`'s own setup steps tell you to grant
it. Tried it live on the reference project's bucket:
`gcloud storage buckets add-iam-policy-binding ... --member=allUsers
--role=roles/storage.objectViewer` → `HTTPError 412: The member bindings
allUsers and allAuthenticatedUsers are not allowed since public access
prevention is enforced.` Checked the bucket's actual IAM policy directly
(`gcloud storage buckets get-iam-policy`) — no `allUsers` binding exists at
all, confirming every hosted URL this app has ever produced 403s to a plain
browser fetch; nobody had actually opened one until embedding it as a chat
image made that visible. **Public Access Prevention is a common default on
managed/shared GCP projects** (the reference project had it; a personal project without an organisation usually doesn't) — don't assume
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
idempotent script (`gcp-setup.sh`, repo root) once it became
clear a comment block isn't discoverable enough for attendees to actually
find and run correctly by hand. **This is NOT part of Lab 1's taught
curriculum** — Lab 1
deliberately keeps Buffer posts text-only and only attaches images via
LinkedIn's own tool (Step 7), specifically to avoid this exact complexity;
this fix is for the reference app's more advanced capability, so it's
documented as an explicitly optional step in
`docs/setup-guide.html` (Step 6), not folded into the
mandatory pre-camp steps everyone has to do.

<!-- END: Build -->

<!-- BEGIN: Scale -->
## Scale

*Agent Runtime, identity, sessions, memory, deploying the frontend.*

<!-- Add new Scale entries here, newest first. -->

### The intermittent 401s were Agent Identity's certificate-bound tokens

With Agent Identity, google-auth (2.55) waits up to 30s for the agent's mTLS
certificate (`/var/run/secrets/workload-spiffe-credentials/certificates.pem`)
and, once it's there, requests tokens with `bindCertificateFingerprint`. That's
the default unless `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false`
(`google/auth/_agent_identity_utils.py`). A bound token only works on a
connection that presents that certificate:

- gRPC clients (Gemini, Secret Manager, Sessions, Memory Bank) do, so they never failed.
- Plain-HTTP clients don't: `google-cloud-storage` in `upload_image` and the
  raw httpx call to DLP got `401 Invalid Credentials`.
- Tokens fetched before the certificate is mounted are unbound, which is why
  early calls worked (the 15:21 post write, DLP on a fresh engine) and later
  ones failed. That's what made it look random.

Tested side by side on fresh engines, after waiting for the certificate:
`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false` → 2/2 image
uploads landed; `GOOGLE_API_USE_CLIENT_CERTIFICATE=true` (mTLS for the storage
client) → 0/2. So `main.py` now defaults the opt-out on Agent Runtime
(`APP_URL` set). Trade-off: tokens are no longer tied to the agent's
certificate; set the variable to `true` on a deployment to keep binding.

With auth fixed, DLP then answered **400**: the URL used `GOOGLE_CLOUD_PROJECT`,
which is the project *number* inside the container, and DLP rejects numbers.
`guardrails.py` now uses `PROJECT_ID` (as Lab 3 already did), and Lab 2's
deploy command passes `PROJECT_ID=YOUR_PROJECT`.

### The `-latest` model aliases started returning 404 overnight; newer models hit 429 quickly

On 2026-10-02 `gemini-flash-latest` and `gemini-flash-lite-latest` returned
`404 Publisher model … was not found` in `global`, with no change on our side.
They had worked the day before. Every agent defaulting to them broke at once,
deployed and local alike. Pinned names worked: `gemini-2.5-flash`,
`gemini-2.5-flash-lite`, `gemini-2.5-pro`, `gemini-3.5-flash`,
`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`.
No official notice found; one third-party source says `-latest` aliases switch
over when a model retires, and that `gemini-2.5-flash` retires on 2026-10-16.

`gemini-3.5-flash` / `gemini-3.5-flash-lite` / `gemini-3.1-pro-preview` worked
end to end on a fresh engine (routing, research, drafting, the approval
request), but returned `429 RESOURCE_EXHAUSTED` on the second test
conversation. New and preview models get small quotas, and every attendee runs
in their own project, so a workshop would hit it.

**Decision for the workshop**: pin GA models everywhere, `gemini-2.5-flash`
(orchestrator, and the code defaults), `gemini-2.5-flash-lite` (research),
`gemini-2.5-pro` (draft). That alone wasn't enough: `gemini-2.5-pro` also
returned 429 on its fourth call in a burst inside `draft_agent`, which killed
the turn. So every agent's model is now wrapped by `_model()` in `agent.py`:
`Gemini(model=..., retry_options=HttpRetryOptions(attempts=5, initial_delay=2,
max_delay=30, exp_base=2, http_status_codes=[429, 503]))`. Verified on a fresh
engine: two back-to-back research + draft + image runs, three
`google_genai._api_client: Retrying …` log lines, no error reaching the user.
**Before 2026-10-16**: move to the 3.5 family (the retries matter even more
there).
**Codelab material**: never default to an alias in workshop code; a moving
alias is a dependency you didn't pin.

### Demo-day findings on a deployed engine: image links, plain-HTTP 401s, `DRY_RUN` with Buffer, stale approvals

All hit live on 2026-10-01 while demoing the Scale lab from a fresh engine.

- **Chat image previews were broken.** The draft agent builds the preview
  link from `BACKEND_PUBLIC_ORIGIN`, which defaulted to `http://localhost:8000`.
  Nothing listens there on a deployed engine. The frontend already has an
  `/outputs/[...path]` route that fetches the file from the engine, so on
  Agent Runtime (detected by `APP_URL`) the default is now a relative
  `/outputs/…` link, which works from `localhost:3000` and from the Cloud Run
  proxy alike. The images still live on the container's disk, so they're gone
  after a redeploy or scale-to-zero.
- **Plain-HTTP Google API calls from the container get `401 Invalid
  Credentials`.** Seen on Cloud Storage uploads (`upload_image`, on both a
  Buffer and a LinkedIn engine) and on Cloud DLP (`redact_pii_text`, via
  httpx). It's the same family as the Secret Manager entry: gRPC clients work,
  raw bearer-token calls don't. Unexplained so far: `db.py`'s post writes to
  the same bucket with the same `storage.Client()` succeeded on the same engine
  earlier the same day, and on 2026-10-02 `redact_pii_text` reached DLP fine on a
  fresh engine. **Root-caused 2026-10-03, see the token-binding entry.**
- **`redact_pii_text` raising killed the whole turn.** After signing in, the
  orchestrator decided the user's name was personal data, called the tool, and
  the 401 surfaced as `DynamicNodeFailError`, before anything was posted.
  The tool now catches the failure and returns the text unredacted with an
  explicit error, so the agent carries on and says so.
- **`DRY_RUN=true` with `POST_VIA=buffer` is a trap.** Buffer has no dry run,
  so it doesn't protect anything, but `upload_image` honours `DRY_RUN` and
  returns a fake `dry-run-bucket` URL. An approved Buffer post would carry a
  broken image link. On the Buffer route, use `DRY_RUN=false` plus
  `BUFFER_REVIEW_DELAY_MINUTES` as the safety net.
- **An approval pending across a redeploy replays with the old tool's
  arguments.** The chat thread persists in the browser
  (`localStorage["social-spark-thread"]`), so after switching the engine from
  Buffer to LinkedIn, a Buffer `create_post` approved earlier ran against
  LinkedIn's `create_post` and failed validation (`channelId`, `dueAt`, …
  "Unexpected keyword argument"). Start a fresh chat after a redeploy (incognito
  window, or clear that key).
- **Google sign-in on the Cloud Run frontend needs the client ID at build
  time.** `NEXT_PUBLIC_*` is inlined by `next build`, and `gcloud run deploy
  --source` can't pass build args. What worked: client ID in Secret Manager
  (`google-oauth-client-id`), a one-off Cloud Build that passes it as
  `--build-arg`, then `gcloud run deploy --image`, plus the proxy origin
  (`http://localhost:8090`) added to the OAuth client's Authorized JavaScript
  origins. Sessions still record `devcamp-user`: the backend's `user_id` is
  fixed and the signed-in identity isn't passed through yet.
- **A Cloud Run service with traffic pinned to a revision stays pinned.**
  After `update-traffic --to-revisions=…=100`, a new `gcloud run deploy`
  builds a new revision that gets **0%**. Run `update-traffic --to-latest`.

### The LinkedIn MCP toolset intermittently vanished on cold starts: ADK's 5s MCP connect timeout vs. FastMCP's startup update check

Symptom: after some redeploys, approving a post failed with `ValueError:
Tool 'create_post' not found`, and a few seconds earlier the log said
`Failed to get tools from toolset McpToolset: Failed to create MCP session`
(a `WARNING`, so easy to miss). It came and went across cold starts: it failed
at 13:13, 13:37 and 13:39, but not at 13:19, which then published fine. It
started *before* any of that day's code changes, so it isn't a regression.

**Cause**: `StdioConnectionParams` defaults to a **5 second** connect timeout.
The stdio server is a fresh Python process, and FastMCP 3.x also does a
`GET https://pypi.org/pypi/fastmcp/json` update check on every start (visible
as `[linkedin-mcp] HTTP Request: GET …pypi…` in the logs). Loading
`create_post` locally, on a warm machine, already took **4.7s**. A cold
container goes over. ADK logs the failure as a warning and carries on with
the toolset's tools missing, so the agent only finds out when it tries to
call one.

**Fix** (`agent.py`, `_linkedin_connection`): `FASTMCP_CHECK_FOR_UPDATES=off`
in the server's env, and `timeout=30.0`, the same headroom the Buffer toolset
already had. The first cold start after deploying it loaded both LinkedIn
toolsets cleanly. **Codelab material**: a slow MCP startup surfaces as a
missing tool, not as an error. When a tool "isn't found", check for
`Failed to create MCP session` before you look at the prompt.

### `backend/main.py` never exported telemetry, so the console's "Reported by agent" tiles read 0 and Cloud Trace was empty

The engine's Dashboard → Overview showed **Sessions 0, Avg. turns per
session 0, Agent invocations 0** under *Reported by agent*, while the
*Reported by Agent Runtime* charts below had data. Those tiles are built from
the agent's own OpenTelemetry spans (`gen_ai.conversation.id`,
`gen_ai.agent.name`, …), not from the Sessions API.

**Cause**: `agents-cli deploy` sets `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`,
but that only does something when ADK's own server (`get_fast_api_app(otel_to_cloud=…)`)
or the agents-cli template reads it. This repo serves a hand-built FastAPI
app, so ADK created spans (hence the harmless OTel `detach` traceback below)
and nothing exported them. The exporter packages weren't even installed.

**Fix**: `google-adk[a2a,eval,gcp]==2.4.0` (same ADK pin; re-locking added 26
packages, none changed), plus a block at the top of `main.py` that calls ADK's
public helpers, `get_gcp_exporters(enable_cloud_tracing=True,
enable_cloud_logging=True)` + `maybe_set_otel_providers(...)`, when that env
var is true. It's wrapped so a failure logs and the app stays up. Metrics are
off, as in ADK's server. No extra IAM was needed: the Telemetry API was
already enabled and the deployed agent's spans arrived under its own identity.
The spans carry `gen_ai.request.model` per agent, so the per-agent models
(orchestrator `gemini-flash-latest`, research `gemini-flash-lite-latest`,
draft `gemini-3.1-pro-preview`) are now visible in traces.

Two things that wasted time verifying it: Cloud Trace's v1 `ListTraces` with a
`startTime` filter returned **0** while the traces existed (list with
`orderBy=start desc` and no time filter instead), and calls without
`x-goog-user-project` hit a shared free-read quota and 429. Also, the "Exporting
OpenTelemetry…" `log.info` never appears, because it runs before logging is
configured. Its absence isn't a failure.

### ag_ui_adk's session cleanup deletes *managed* sessions, so they disappear from the console

The console's Sessions tab lost conversations, and a session id from earlier
in the day returned 404. `ag_ui_adk`'s `SessionManager` sweeps every 300s and
expires sessions idle for 1200s, and with `delete_session_on_cleanup=True`
(the default) it deletes them **from the session service itself**. With
`VertexAiSessionService` that is the managed store the console reads. Seen
directly: session created 10:44, `Cleaned up 1 expired sessions` at 11:06,
then 404. Whether a session survived depended on timing: if the container
scaled to zero first, the timer died with it (the same race as the
memory-ingestion entry below). That made Lab 2 Step 4's "still there after
scale-to-zero" checkpoint a coin toss.

**Fix** (`main.py`): `delete_session_on_cleanup=False` when running on an
engine, `True` locally where sessions are in-memory anyway. Managed sessions
already expire on their own (`expireTime` is a year out). Memory ingestion is
explicit at publish, so nothing depended on the cleanup.

**Verified**, with the container kept up by an `openapi.json` request every
90s (which doesn't touch any session): session created 14:27, `Cleaned up 1
expired sessions` at 14:47, and the session still returned 200 afterwards.
Testing this needs the keep-alive. Left idle, the container shut down after
about 7 minutes, before any sweep, so a surviving session proved nothing.
Note that `Cleaned up N` is logged even when nothing is deleted. It counts
sessions dropped from `ag_ui_adk`'s own tracking, not deletions.

### A hand-written `agents-cli-manifest.yaml` works for deploy; `scaffold enhance` can't write one for this repo

`agents-cli scaffold enhance . --deployment-target agent_runtime
--agent-directory backend/social_poster` fails (acli 1.7.0) with "Agent
directory 'backend/social_poster' is not a valid Python identifier". The
directory becomes a module name, so it must be one bare identifier, which means
a nested path can never pass. It failed before writing anything, though it
does make a backup under `~/.agents-cli/backups/` first.

Writing the manifest by hand works. The field names come from a throwaway
`scaffold create`, and the minimum that matters is `name`, `agent_directory`,
`region` and `create_params.deployment_target` (plus `root_agent_name`, which
`agents-cli info` would otherwise derive from `name`). Read from the CLI
source rather than assumed:

- **Deploy never validates `agent_directory`.** Only `playground`, `run` and
  the `eval` commands call `require_agent_directory`. Deploy records the value
  in `deployment_metadata.json` and uses it for the A2A card path, which we
  don't serve. `agents-cli playground` doesn't fit this layout anyway (it runs
  `adk web .` from the root), so local dev stays `uv run adk web backend`.
- **`name` becomes the service name** (`--service-name` > `name` > `agent`),
  and `region` replaces the CLI default of `us-east1`. Both stop being things
  an attendee can mistype.
- **`-d` still overrides per run.** `deploy -d cloud_run --dry-run` prints a
  correct `gcloud run deploy social-spark-poster --source . --region
  us-central1`. GKE is *not* unlocked by the manifest: it needs the
  `deployment/terraform/` tree only `enhance` generates, and `--service-name`
  is rejected for GKE.

Verified with `agents-cli info`, `deploy --dry-run`, `deploy --status` and
`deploy --list`, all without `-d`. Not verified: a real (non-dry-run) deploy
from the manifest.

**Codelab material**: the manifest ships from `pillar-build` onward, and Lab 2
presents it as already there (attendees are assumed to be on `agents-cli`
projects of their own). That drops `-d agent_runtime`, `--region` and
`--service-name` from every command. That retires the old `--status`
deliberate error (see the "Three real gotchas" entry below), so it's replaced
by the next entry's.

### `agents-cli deploy --status` is a one-shot follow-up on a local file, not a query of what's deployed

`--status` reads the pending operation from `deployment_metadata.json`
(gitignored, written by `deploy --no-wait`), polls it, and on success rewrites
the file with the engine id and **clears the pending operation**. Run it again
and you get "No pending deployment operation found. Run 'agents-cli deploy' or
'agents-cli deploy --no-wait' first.", which reads like the agent has been
deleted. It hasn't; `deploy --list` (which queries the project) still shows
it. A buddy running `--status` from their own clone hits the same error,
because their clone has no record of the operation. Reproduced on
`social-spark-poster` in `adk-devcamp-poc`.

**Codelab material**: this is Lab 2's deliberate error now. It's the same
lesson the old one taught (read what the failing command actually checks
before undoing work), it still happens with a manifest in place, and the fix
is to use `--list`.

### Memory Bank was running on defaults; configuring it is a PATCH, not a redeploy, and the docs page does not say how

Lab 2's Memory
Bank worked but was never configured: the engine's `contextSpec.memoryBankConfig`
was just `{"generationConfig": {}}`. Google's setup page
(`.../scale/memory-bank/setup`) documents topics, TTL, extraction model and
few-shot examples, but only for **creating** a bank with the new `agentplatform`
SDK; for an already-deployed engine it says nothing, and this repo has
`google-cloud-aiplatform`, not `agentplatform`. What works: the engine resource
carries `contextSpec.memoryBankConfig.customizationConfigs[].memoryTopics`
(managed enums `USER_PREFERENCES`, `EXPLICIT_INSTRUCTIONS`, ... or a custom
`{label, description}`) and `ttlConfig.defaultTtl`; `PATCH ...?updateMask=contextSpec.memoryBankConfig`
applies it live, the container keeps serving (checked `/api/openapi.json` 200),
and it read back intact. Verified by sending the bank a conversation through
`client.agent_engines.memories.generate(...)`: it kept the style rule (question
opener, no emojis, sign-off) as one fact and did not keep the unrelated cat and
travel remarks. Scripted as `backend/memory_bank_config.py` (`show`/`apply`).
**Unverified**: whether an `agents-cli deploy` resets the config (run `show`
after deploys); whether a custom service account needs
`aiplatform.memories.generate/retrieve` (the page says the default service agent
needs nothing, and Agent Identity worked without extra grants).
**Also from that page, worth telling attendees**: deleting an Agent Runtime
engine deletes its memories, so Lab 2's teardown wipes the bank. Now in the lab.
Stale wording fixed: `main.py` and `agent.py` comments still said memory was
"populated automatically", contradicting the finding two entries below.

### Memory Bank works, but ADK's "automatic session memory" does not fire on a scale-to-zero deployment, and the fix has an async trap in it

Wiring `VertexAiMemoryBankService` is the easy part: same constructor
shape as `VertexAiSessionService` (project/location/agent_engine_id, all
derivable from the `APP_URL` Agent Runtime injects), passed to `ADKAgent`
alongside the session service, plus ADK's `load_memory` tool on the
orchestrator so the model can actually search it. Two non-obvious things then
cost an hour.

**1. "Automatic session memory" means "on session cleanup", not "as you go".**
`ag_ui_adk`'s docstring says passing a memory service "also enables automatic
session memory", which reads as continuous. It isn't: `add_session_to_memory`
is called in exactly one place — `session_manager.py`, immediately before a
session is *deleted* during cleanup. Cleanup runs on a timer:
`session_timeout_seconds=1200` (20 minutes idle) swept every
`cleanup_interval_seconds=300`. **On Agent Runtime with `min_instances=0` that
is close to useless**, because the same idleness that would eventually trigger
cleanup scales the container to zero first, killing the in-process timer that
was going to do the ingesting. Nothing errors; memories simply never appear.
**Fix**: ingest explicitly at a meaningful boundary instead of waiting for a
reaper. Here that boundary is publishing — the conversation is finished, and
what the user asked for and approved is exactly what is worth recalling. A
`memory_ingested` state flag keeps it to once per conversation rather than once
per turn.

**2. `CallbackContext.add_session_to_memory()` is `async`, and calling it
without `await` fails completely silently.** A sync `after_agent_callback` that
calls it creates a coroutine, drops it, and returns cleanly — no exception, no
warning in the logs, and the `try/except` around it never fires because nothing
raised. The only symptom is an empty Memory Bank. ADK awaits callbacks that
return awaitables, so the fix is just making the callback `async def` and
awaiting the call; the method's own docstring shows exactly that pattern
(`async def my_after_agent_callback(ctx): await ctx.add_session_to_memory()`)
and is worth reading before assuming, as I did, that a `-> None` signature
means synchronous.

**Confirmed working end to end**: stated a preference ("I always sign off with
'Building in public.' and never use more than two hashtags") in a conversation
that published a post; Memory Bank distilled it into a stored fact on its own
(`"I always sign off my posts with 'Building in public.' and never use more
than two hashtags."`); then in a **brand new session** that never mentioned it,
the orchestrator called `load_memory` and the draft came back ending "Building
in public. #CoffeeLover #MorningRituals" — sign-off applied, exactly two
hashtags. **Codelab material**: this is the Scale lab's Memory Bank segment
almost verbatim, and the contrast with this repo's hand-rolled `memory_agent`
(a RemoteA2aAgent over a separately deployed RAG service, with its own
ingestion pipeline to build) makes the managed version's value obvious without
having to argue for it. Both can be enabled at once, which is the honest way to
show the difference. Teach the async trap too — a silent no-op is a much more
instructive bug than a stack trace.

### Getting a Secret Manager secret into a deployed agent: `secretEnv` does not work, runtime retrieval does, and an import-time `raise` hid the whole thing

Switching the deployed agent to Buffer (the only
route to X) took six failed deploys, and there were **two independent causes**
— worth separating, because chasing them as one wasted most of that time.

**Cause 1 — `secretEnv` is rejected on a container-based deployment.** Whether
set via `agents-cli deploy --secrets BUFFER_API_KEY=buffer-api-key:latest` or
by PATCHing `spec.deploymentSpec.secretEnv` over raw REST, the update fails with
`code: 3, "The Reasoning Engine failed to be updated."` — no field, no cause, no
container build, no logs. **Isolated with a control**: the same deploy differing
*only* by `--secrets`, with `POST_VIA` left on its working value, still failed.
**Ruled out**: the documented prerequisite ("add roles/secretmanager.secretAccessor
to the AI Platform Reasoning Engine Service Agent") was verified in place on
`service-{NUM}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`, which exists and
holds `roles/aiplatform.reasoningEngineServiceAgent`; a 150s propagation wait;
the secret itself (automatic replication, one enabled version, non-empty). Note
the earlier Scale spike *did* get Secret Manager working — on the **cloudpickle**
path, via `env_vars={"secret": ..., "version": ...}`. The likely conclusion is
that `secretEnv` does not apply to container-based deployments, but that is
inference, not a documented statement.

**Cause 2 — and this is the one that made everything undiagnosable.**
`agent.py` had `raise RuntimeError("POST_VIA=buffer requires BUFFER_API_KEY")`
at **module import**. On Agent Runtime, an import that raises kills the
container before it logs anything, and the platform reports exactly the same
opaque "failed to be updated" as Cause 1 — so a perfectly ordinary application
error was indistinguishable from a platform rejection. **Replacing the raise
with a logged fallback to LinkedIn made the next deploy succeed immediately**,
and the container's own logs then said precisely what was wrong. **General
lesson worth more than the specific bug**: never `raise` at import time in a
deployed agent. Degrade, log, and stay up — otherwise every failure is a black
box.

**The fix that works**: don't put the secret in the deployment at all. Carry
only the secret's *name* as a plain env var (`BUFFER_API_KEY_SECRET`, which is
not sensitive) and have the agent fetch the value at runtime with its own Agent
Identity — which is exactly what Google's own docs prescribe
(`secret-manager/docs/integrate-secret-manager-with-adk`: grant the agent
identity `roles/secretmanager.secretAccessor`, read at runtime). It is better
practice regardless: the secret never appears in the deployment spec, and
rotating it needs no redeploy.

**But use ADK's client, not a hand-rolled REST call.** A first attempt used
`google.auth.default()` + a bearer token against
`secretmanager.googleapis.com/v1/...:access` to avoid adding a dependency. It
works locally and returns **HTTP 401 Unauthorized** inside the Agent Runtime
container — 401, not 403, so credentials rather than permissions, with the
grant in place. Switching to
`google.adk.integrations.secret_manager.secret_client.SecretManagerClient`
(needs `google-cloud-secret-manager`, which ADK does not pull in by default)
worked first try: the gRPC client handles the Agent Identity credential
correctly where a raw `Authorization` header does not. Confirmed live — the
deployed agent now lists Buffer's channels (Instagram, Facebook, Twitter,
LinkedIn, Pinterest, YouTube, Mastodon, TikTok, Threads, Bluesky) instead of
"I can post to LinkedIn."

**Codelab material**: three separate teaching points, all earned. (1) Never
raise at import in a deployed agent — it converts a readable error into an
opaque platform failure. (2) Runtime secret retrieval beats injected secret env
vars anyway, and is what Google documents. (3) When a Google API returns 401
rather than 403 from inside a managed runtime, suspect the credential *type*,
not the IAM grant — and reach for the official client before hand-rolling HTTP.

### Deploying the frontend too: the whole problem is auth, and one piece of it only breaks in a production build

Running the CopilotKit
frontend on Cloud Run against an Agent Runtime backend works, but three things
have to be right and only the first is obvious.
1. **The browser can never call the backend directly.** Agent Runtime's `/api`
   passthrough is a Google API endpoint needing a bearer token, and a browser
   has no safe way to hold one. `PostGallery.tsx` fetched `/api/posts` and
   `/outputs/<file>` **client-side**, so the gallery breaks the moment the
   backend is deployed. Fix: same-origin Next route handlers
   (`app/api/posts`, `app/outputs/[...path]`) that proxy server-side and add
   the token, with `PostGallery` defaulting to same-origin.
2. **The service account needs `roles/aiplatform.user`** (it contains
   `aiplatform.reasoningEngines.query`). Easy to miss because project owners
   have it implicitly — the local frontend works for the person who built it
   and 403s for everyone else.
3. **The one that cost real time**: pointing `HttpAgent` at the passthrough and
   passing a custom `fetch` to attach the token **works in dev and silently
   fails in a production build** — the engine answers `401 UNAUTHENTICATED`.
   The tell was that `/api/posts` (our own handler, same credentials, same
   container) returned `200` at the same moment, which rules out IAM, the
   token, and the URL. Don't rely on the `fetch` override surviving the
   CopilotKit runtime's internals. **Fix**: give the app its own
   `app/api/adk` route that proxies to the backend with the token, and point
   `HttpAgent` at `http://127.0.0.1:${PORT}/api/adk` — server-to-self over
   loopback. Everything then goes through one proven code path, and SSE still
   streams because the upstream body is passed straight through.
**Protecting the deployed frontend**: `--no-allow-unauthenticated` plus
`gcloud run services proxy` is the right workshop answer — the service is
genuinely private (verified: 403 unauthenticated, 200 with an identity token),
attendees need zero OAuth setup, and the app's own Google sign-in is optional
anyway because it falls back to "Continue as guest". IAP is the upgrade for
real sharing. **Codelab material**: this is a strong Lab 2 segment precisely
because the failure is asymmetric — one route works and another doesn't, in
the same container, with the same credentials. Teaching attendees to compare a
working path against a broken one is more useful than handing them the answer.

### A red `ERROR` traceback in the deployed agent's logs that means nothing is wrong: OpenTelemetry `detach()` across an async-generator boundary

Seen in Cloud Logging right after a successful
run: `ValueError: <Token var=<ContextVar name='current_context' ...>> was
created in a different Context`, logged at **severity ERROR** with a full
traceback. The chain, read from the actual log payload rather than guessed:
`google/adk/runners.py:579 _run_node_async → yield event` →
`google/adk/telemetry/_instrumentation.py:85 record_invocation → yield` →
`google/adk/telemetry/node_tracing.py:215 _use_invoke_workflow_span → yield
span` → `opentelemetry/trace/__init__.py start_as_current_span` →
`opentelemetry/context/__init__.py:143 detach(token)`. **Mechanism**: ADK's
`_run_node_async` is an async *generator* wrapped in an OTel span context
manager. `attach()` returns a token bound to the `contextvars.Context` it was
created in; the generator then suspends at `yield event`, and with SSE
Starlette resumes the streaming body in a **different task**, so by the time
the span closes, `detach()` is running in a context that never saw the
`attach()`. **Confirmed harmless**: it fires during span *cleanup*, after the
work completes; the timestamps line up exactly with the HITL confirmation test
that succeeded end-to-end (`create_post` → `adk_request_confirmation`, full
event stream, correct gating); and all four ERROR entries in the engine's
entire log are this one issue (two tracebacks, logged as two entries each) —
there are no other errors. **Why it never shows up locally**: `agents-cli
deploy` sets `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY: true`, so the span
machinery only runs once deployed. It is an upstream ADK↔OTel interaction, not
project code, and not specific to the container deploy path. Silencing it means
setting that var to `false`, which costs you Cloud Trace — a bad trade given
the Optimise pillar depends on tracing. **Codelab material**: every attendee
who deploys and then opens their logs will see this, at ERROR, on a request
that worked — a textbook way to lose twenty minutes of a workshop. Name it up
front in Lab 2's teardown/observability step: "you will see this, here is why,
ignore it." **Open question for Lab 4, flagged not answered**: if the spans are
mis-parented, does that affect anything that *reads* traces — specifically
Online Evaluation's `sessionScope`, which groups traces by conversation ID?
Worth testing before Lab 4 leans on trace-based evaluation.

### There are TWO Agent Runtime deployment models, and picking the container one removes most of the pain documented below

Every
earlier Scale entry (packaging, model pinning, Secret Manager) was learned against the **cloudpickle model**:
`vertexai.Client().agent_engines.create(agent=AdkApp(...))`, which pickles the
agent object, ships `extra_packages`, and re-imports your module remotely. That
is a real, documented path and it works. But `agents-cli deploy -d
agent_runtime` uses a **completely different model**: it packages the project,
and Agent Engine builds a container image **from your project's own
`Dockerfile`** — the same image that would serve Cloud Run or GKE. The current
docs are blunt about which is preferred: *"No `gcloud` CLI exists for Agent
Runtime. Deploy via `agents-cli deploy`."* **Why this matters here**: this repo
already had a hand-written `Dockerfile` (built for Cloud Run, `CMD` =
`uvicorn backend.main:app`, already `COPY`ing `skills/` and `mcp/` after the
Prep-phase fix), so the container path needed *no new packaging code at all* —
where the cloudpickle path needed a 230-line deploy script to stage a clean
copy, pin requirements exactly, and pin models before import. File selection
honours `.gcloudignore`/`.gitignore`, so `backend/social_poster/.env` is
excluded automatically — the secret-leak hazard that forced the staged-copy
dance simply doesn't arise. **Codelab material**: Lab 2 should teach
`agents-cli deploy` as one command, and mention the SDK path only as "the
lower-level API underneath". Don't teach the packaging gymnastics — they're an
artifact of the model, not of Agent Runtime.

### The `/api` passthrough serves your container's own FastAPI app, which means the CopilotKit bridge does not need to be built

This was the plan's riskiest item — a custom FastAPI service that would call
`async_stream_query` and feed each ADK `Event` through `EventTranslator`,
**reimplementing `ADKAgent`'s HITL resume loop**, with a documented fallback
for when it failed. It turns out to be unnecessary. Agent Engine exposes the
container's HTTP routes externally under an `/api` prefix:
`https://{loc}-aiplatform.googleapis.com/reasoningEngines/v1/{resource}/api/{container_path}`,
and the platform injects that base as `APP_URL` into the container's own env.
Since our Dockerfile serves `backend/main.py`, and `main.py` mounts AG-UI at
`/api/adk`, the deployed engine answers AG-UI at `{APP_URL}/api/adk`.
**Verified live against a real deployment, not inferred**: `GET /docs` returns
the FastAPI Swagger UI; `GET /openapi.json` returns `"title": "social-agent
AG-UI backend"` with `/api/adk` in `paths` — proof it is our app, not ADK's
generic surface. Then a real `POST` with a `RunAgentInput` body streamed back
a complete AG-UI SSE run: `RUN_STARTED` → `STATE_DELTA`
(`{"op":"add","path":"/pipeline_stage","value":"idea"}`) →
`TEXT_MESSAGE_START/CONTENT/END` → `STATE_SNAPSHOT` → `RUN_FINISHED`.
**Everything the bridge would have had to reimplement works natively.**
**Codelab material**: this turns Lab 2's "advanced UI step or boundary
explanation" from a hedge into a real, live demo — and it's a great lesson in
its own right: before building an adapter, check whether the platform already
speaks your protocol.

### `STATE_DELTA`, nested `AgentTool` calls, multi-turn sessions and the HITL confirmation gate all survive the passthrough

Taken
one at a time, because each was separately uncertain:
* **`STATE_DELTA`/`STATE_SNAPSHOT`** — previously "untested at all", and the
  one that mattered most, since the entry on nested `AgentTool` calls (below) established that
  session state is the *only* channel that can surface anything happening
  inside a sub-agent's `AgentTool` call. Confirmed: `pipeline_stage` arrives
  and transitions (`idea` → `drafting`), which is what `StagePanel.tsx` reads.
* **Nested `AgentTool` calls** — the orchestrator's `draft_agent` call streams
  `TOOL_CALL_START`/`ARGS`/`END`/`RESULT` as normal.
* **Sessions across separate client calls** — three separate `curl` invocations
  sharing one `threadId` continued one conversation, with the draft from call
  two still present in call three. That is the Scale phase's Step B "Sessions"
  requirement, satisfied without extra work: `ag_ui_adk` maps `threadId` onto
  the ADK session, and the deployed container keeps it.
* **HITL confirmation** — the decisive one. Turn one drafted and (correctly,
  per the `refuses_post_without_approval` golden case) stopped to ask for
  approval. Turn two, "Approved. Post it now.", produced
  `TOOL_CALL_START` with `"toolCallName":"create_post"` followed immediately
  by `"toolCallName":"adk_request_confirmation"` — the exact synthetic call
  `ConfirmAction.tsx` renders. Gated, not executed; `DRY_RUN=true` throughout
  and the confirmation was never answered, so nothing was posted.
**Codelab material**: the remaining work for a live CopilotKit demo is auth
(the Next.js layer proxying with a bearer token), not protocol. Worth saying
plainly in Lab 2 that the hard part turned out to be the boring part.

### `GOOGLE_CLOUD_LOCATION` is NOT reserved on the container path, so the model-pinning constraint below doesn't apply there

The `gemini-flash-latest` entry established that `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION` are reserved `env_vars` names rejected with
`FAILED_PRECONDITION`, and concluded that pinned regional models were therefore
the *only* option since `global` could not be forced. On the container path
that reasoning collapses: `agents-cli deploy` sets `GOOGLE_CLOUD_LOCATION:
global` itself, printing it in the deploy's env summary. With `global`
resolution available, `gemini-flash-latest` would resolve normally. (Models
were still pinned to `gemini-2.5-flash` in this test, so the `-latest`-on-
container combination is *not* separately confirmed — but the constraint that
forced pinning is demonstrably absent.) **Consequence for Lab 2**: the
"pin your models before deploying" step, and the deliberate error around
missing `extra_packages`, are both **cloudpickle-path artifacts**. Neither
exists on the path the lab will actually teach. Replacement deliberate-error
candidates from today's run are in the next entry.

### Three real gotchas hit deploying via `agents-cli`, any of which would stall an attendee

1. **`cloudresourcemanager.googleapis.com` must be enabled**, or the deploy
   dies with a `403 PermissionDenied ... SERVICE_DISABLED` from deep inside a
   gRPC stack trace — not an obvious "enable this API" message at the top.
   Worth noting that this plan's own notes claimed the API had already been
   enabled during the earlier spike; `gcloud services list --enabled` showed
   it was not. Verify, don't trust the note.
2. **`agents-cli deploy --status` needs `-d agent_runtime` too** when the
   project has no `agents-cli-manifest.yaml`. Without it you get "No
   agents-cli-manifest.yaml found in the current directory or its parents",
   which reads like the deploy itself failed — it hasn't; only the status
   lookup has. Polled six times before spotting it.
3. **No manifest means defaults**, announced as a warning: service name
   `agent`, *agent directory `app`*, building from the repo root. The `app`
   default is harmless for this repo — our own `Dockerfile`'s `CMD` decides
   what runs, and the passthrough addresses container paths directly — but it
   looks alarming given the agent actually lives at `backend/social_poster/`.
   `--service-name` overrides the first; the second can be ignored here.
**Codelab material**: (1) belongs in the setup guide's API list. (2) is a
strong candidate for Lab 2's deliberate error, replacing the now-obsolete
`extra_packages` one — it's real, it's confusing, and the fix is one flag.
*Update:* (2) and (3) only apply without a manifest. The repo now ships
`agents-cli-manifest.yaml` from `pillar-build` onward, so neither is hit any more, and the deliberate
error moved to `--status` being one-shot (see the hand-written manifest entry
above).

### `useCopilotAction`/`useRenderTool`-style rendering cannot see tool calls nested inside an `AgentTool`

Tried rendering
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

### Local `adk web` testing is completely unaffected by everything below

Worth stating explicitly, not just implying: every
issue in this file was hit while *deploying to a regional Agent Runtime
instance*. None of them occur during normal local development
(`uv run adk web`, `GOOGLE_CLOUD_LOCATION=global`, `gemini-flash-latest`) —
that path is exactly as simple as it's always been. **Codelab material**:
be explicit in Lab 2 that everything before the actual deploy step (writing
the agent, testing it locally, Sessions 1's whole workshop) carries none of
this risk — the friction is real but scoped to one specific step, not
pervasive. Attendees should not come away thinking ADK itself is fragile.

> **Scope note (added 2026-09-19):** hit on the **cloudpickle** deploy path,
> where regional model pinning was forced. On the container path
> (`agents-cli deploy`) `GOOGLE_CLOUD_LOCATION` is settable and set to
> `global`, so the pinned-model precondition may not apply. The
> least-privilege lesson stands regardless.

### `AgentTool` breaks with `MALFORMED_FUNCTION_CALL` when paired with a broad MCP toolset, on pinned regional models

Deploying
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

> **Superseded in part (2026-09-19):** true on the **cloudpickle** path,
> where `GOOGLE_CLOUD_LOCATION` is a reserved, rejected env var. On the
> container path `agents-cli deploy` sets it to `global` itself, so the
> "pinned versions are the only option" conclusion does not carry over. The
> attendee-setup-guide half of this entry still stands.

### `gemini-flash-latest` only resolves via the `global` endpoint; every region 404s

Confirmed directly: a plain
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

> **Superseded for the taught path (2026-09-19):** entirely a **cloudpickle**
> concern. The container path builds from the project's own `Dockerfile` and
> `uv.lock`, so there is no `requirements` list to get wrong and no
> `extra_packages` to forget.

### Agent Runtime packaging: `requirements` needs both `google-cloud-aiplatform[...,adk]` and an explicit `google-adk[a2a,eval,mcp]` pin, or the resolver silently picks a broken version

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

### Secret Manager: the IAM grant needed is the platform service agent, not the Agent Identity principal, and there's a real chicken-and-egg problem

`BUFFER_API_KEY` was initially passed as a
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

### HITL confirmation pause/resume is confirmed working against a real deployed Agent Runtime engine — this was the actual open question, and it's a clean pass

Built a minimal isolated test
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

<!-- END: Scale -->


