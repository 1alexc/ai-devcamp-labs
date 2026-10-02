# Lab 2 (Scale) runbook

Every command for running the Scale lab end to end, in order. The step numbers
match `docs/lab-2-scale.html`. Commands use `adk-devcamp-poc`; swap in your own
project. All of them were run against a fresh test engine on 2026-10-01.

Run from the repo root, on code that includes the `agents-cli-manifest.yaml`
and the `backend/main.py` session/telemetry changes.

## 0. One-time local setup

Fill in `backend/social_poster/.env`. The keys that matter for Scale:

- `GOOGLE_CLOUD_PROJECT`
- `GCS_BUCKET_NAME`: bucket names are global, so include your project ID,
  e.g. `adk-devcamp-poc-social-spark`. The setup script creates it.
- `BUFFER_API_KEY` (optional): stored in Secret Manager by the setup script.

```bash
uv sync
gcloud auth login
gcloud config set project adk-devcamp-poc
```

## 1. Prerequisites

```bash
./gcp-setup.sh
```

This enables the APIs, creates the bucket and the frontend's service account,
stores the Buffer secret, and gives deployed agents access to both. It's safe
to re-run.

Check:

```bash
gcloud services list --enabled | grep cloudresourcemanager
```

## 2. Deploy the agent

Confirm the CLI reads the manifest (`social-spark-poster`, `agent_runtime`,
`us-central1`):

```bash
agents-cli info
```

Deploy. Your local `.env` never reaches the container; only these env vars do:

```bash
agents-cli deploy --project adk-devcamp-poc --agent-identity \
  --update-env-vars "POST_VIA=linkedin,DRY_RUN=true,GCS_BUCKET_NAME=YOUR_BUCKET,PROJECT_ID=YOUR_PROJECT,ORCHESTRATOR_MODEL=gemini-2.5-flash,RESEARCH_MODEL=gemini-2.5-flash-lite,DRAFT_MODEL=gemini-2.5-pro" \
  --no-confirm-project --no-wait
```

Poll until it prints `✅ Deployment successful!` (5–10 minutes):

```bash
agents-cli deploy --status --project adk-devcamp-poc --no-confirm-project
```

**Deliberate error**: run `--status` once more. You get "No pending
deployment operation found". `--status` only follows up on the last deploy
recorded in your local `deployment_metadata.json`, and clears it once it has
reported back. Ask what's actually deployed with:

```bash
agents-cli deploy --list --project adk-devcamp-poc --no-confirm-project
```

> In a project that already has an engine called `social-spark-poster`, this
> deploy **updates** it, keeping its existing env vars. For a clean demo,
> deploy under another name with `--service-name`. `deploy` targets whatever
> engine `deployment_metadata.json` points at, so move that file aside first.

## 3. Talk to it

Set the project number, the engine ID (looked up by name, so this works from
any folder) and the passthrough base URL:

```bash
# works from any folder; set ENGINE_NAME if you deployed with --service-name
export ENGINE_NAME=social-spark-poster
export PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project 2>/dev/null)" --format='value(projectNumber)')
TOK=$(gcloud auth print-access-token)
export ENGINE_ID=$(curl -s -H "Authorization: Bearer $TOK" "https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_NUMBER/locations/us-central1/reasoningEngines?pageSize=100" | python3 -c "import json,sys; print(next((e['name'].split('/')[-1] for e in json.load(sys.stdin).get('reasoningEngines',[]) if e.get('displayName')==sys.argv[1]), ''))" "$ENGINE_NAME")
echo "project $PROJECT_NUMBER, engine $ENGINE_ID"
export BASE="https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1/projects/$PROJECT_NUMBER/locations/us-central1/reasoningEngines/$ENGINE_ID/api"
```

Should start `{"openapi":"3.1.0","info":{"title":"social-agent AG-UI backend"`:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" "$BASE/openapi.json" | head -c 200
```

## 4. Sessions

After chatting in step 6, list the managed sessions. Each shows up with
`userId: devcamp-user`:

```bash
# reuses PROJECT_NUMBER and ENGINE_ID from Step 3 (in a new terminal, run that block first)
: "${ENGINE_ID:?run the Step 3 block first}"
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_NUMBER/locations/us-central1/reasoningEngines/$ENGINE_ID/sessions?pageSize=100" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); s=d.get('sessions'); print(d if s is None else '\n'.join(f\"{x['name'].split('/')[-1]}  {x.get('userId','')}  {x.get('updateTime','')[:19]}\" for x in s))"
```

They also appear in the console: the engine's **Sessions** tab.

## 5. Memory Bank

Configure what the bank keeps (two managed topics, a custom `posting_style`
topic, a 90-day expiry). It's a PATCH on the engine, so no redeploy:

```bash
GOOGLE_CLOUD_PROJECT=adk-devcamp-poc AGENT_ENGINE_ID=$ENGINE_ID uv run python backend/memory_bank_config.py apply
GOOGLE_CLOUD_PROJECT=adk-devcamp-poc AGENT_ENGINE_ID=$ENGINE_ID uv run python backend/memory_bank_config.py show
```

Demo: in the UI, say *"I always sign off with "Building in public." and never
use more than two hashtags"*, ask for a post, then publish and **Approve**.
Memory is ingested at publish time. Then:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/$PROJECT_NUMBER/locations/us-central1/reasoningEngines/$ENGINE_ID/memories" \
  | python3 -m json.tool
```

Expect one distilled fact tagged `USER_PREFERENCES` / `posting_style`. Start a
**new** chat and ask for a post on any topic: it calls `load_memory` and
applies the sign-off and hashtag limit without being told.

## 6. Local UI against the deployed agent

In `frontend/.env.local`:

```
ADK_BACKEND_ORIGIN=https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1/projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/ENGINE_ID/api
```

```bash
./run-frontend.sh
```

Open http://localhost:3000, choose **Continue as guest**, and run a post. The
pipeline goes Idea → Consulting memory → Researching → Drafting → Approval,
and the Approve button appears under the chat once you ask it to publish.
Comment the variable out to go back to the local backend.

**Fresh chat**: the app reopens the last conversation on every reload (the
thread id is kept in the browser; signing out doesn't clear it). After a
redeploy, or to show Memory Bank in a *new* conversation, click **New chat**
in the header (on older checkouts without the button: incognito window, or
`localStorage.removeItem("social-spark-thread"); location.reload();` in the
browser console).

Otherwise an old conversation can replay a stale approval against the new
deployment.

Generated images preview in the chat through the frontend's own `/outputs`
route. On Agent Runtime they live on the container's disk, so they're gone
after a redeploy or scale-to-zero.

## 7. Deploy the frontend

```bash
gcloud run deploy social-spark-frontend --source frontend \
  --region=us-central1 --project=adk-devcamp-poc \
  --service-account=social-spark-frontend@adk-devcamp-poc.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --set-env-vars="ADK_BACKEND_ORIGIN=$BASE" \
  --port=8080 --memory=1Gi --min-instances=0 --max-instances=3
```

The service URL returns **403** directly, which is deliberate. Open it through
the proxy, which signs requests in with your own `gcloud` login:

```bash
gcloud run services proxy social-spark-frontend --region us-central1 --project adk-devcamp-poc --port 8090
```

Then open http://localhost:8090. To let other people in, see
`docs/frontend-public-access.md`.

## 8. Logs and traces

```bash
gcloud logging read "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND resource.labels.reasoning_engine_id=\"$ENGINE_ID\"" \
  --project=adk-devcamp-poc --limit=30 --format="value(severity,textPayload)"
```

Point out:

- The red OTel `ValueError: … was created in a different Context` traceback is
  harmless.
- `Sending out request, model:` lines show each agent's model.
- In the console: **Trace** shows per-agent spans with model and token usage,
  and the engine's **Dashboard → Overview** "Reported by agent" tiles count
  sessions and invocations.

## 9. Teardown

```bash
gcloud run services delete social-spark-frontend --region us-central1 --project adk-devcamp-poc
```

Delete the engine from the console (Agent Platform → Deployments → the engine
→ Delete), or via the API. `force=true` also deletes its sessions and memories.
It returns an operation that
was already `"done": true` when tested:

```bash
curl -s -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_NUMBER/locations/us-central1/reasoningEngines/$ENGINE_ID?force=true"
```

Check nothing is left:

```bash
agents-cli deploy --list --project adk-devcamp-poc --no-confirm-project
gcloud run services list --project adk-devcamp-poc --region us-central1
```

Deleting the engine deletes its Memory Bank. The Secret Manager secret,
service accounts and bucket can stay: they cost nothing (the bucket holds
only small post JSONs) and next session needs them.
