# Setup guide: deploy to Cloud Run + CI/CD (Week 4)

Phase 6 ships the governed agent to production on Cloud Run, with a Cloud Build
trigger that gates deploys on the eval set. All the app APIs were enabled in
Phase 0. ⏱ **note deploy time and total spend** — the spend number opens the
Week 4 deck.

## 1. Scaffold the deployment infrastructure

The build guide uses `google-agents-cli`:

```bash
uvx google-agents-cli agents-cli scaffold enhance --deployment-target cloud_run
```

Review what it generates before deploying — expect a `Dockerfile`, a Cloud Run
service config, and IaC. **Two things to check for this project specifically:**

- **Node.js in the image.** The LinkedIn MCP server is spawned as a Python
  subprocess (fine), but if you ever swap to an `npx`-based MCP server the
  Dockerfile needs Node — see the MCP gotchas in the ADK cheatsheet.
- **Secrets, not env literals.** `LINKEDIN_ACCESS_TOKEN`, `BUFFER_API_KEY`, and
  `MODEL_ARMOR_TEMPLATE_ID` must come from **Secret Manager**, not baked into
  the image. Map them in the Cloud Run service config.

## 2. Service account + IAM

The Cloud Run service account (not your user ADC — that was dev only) needs:

| Role | Why |
|---|---|
| `roles/aiplatform.user` | Gemini calls on Vertex |
| `roles/modelarmor.user` | guardrail sanitize calls |
| `roles/dlp.user` | PII redaction |
| `roles/secretmanager.secretAccessor` | read the tokens above |
| `roles/discoveryengine.viewer` | if the memory agent's datastore is used |

⏱ **note any IAM friction** — it's codelab material (attendees will hit the
same missing-role errors).

## 3. Deploy

```bash
uvx google-agents-cli agents-cli deploy
```

Then point the frontend at the Cloud Run URL:

```bash
# frontend runtime reads this (see app/api/copilotkit/route.ts):
ADK_BACKEND_URL=https://<your-cloud-run-url>/api/adk
```

Verify the full loop in prod exactly as local: idea → draft → approve →
dry-run post. **Keep `DRY_RUN=true`** in the deployed secrets.

## 4. CI/CD: eval-gated deploys

Wire a **Cloud Build trigger** on push to `main` that runs the eval set BEFORE
deploying:

```yaml
# cloudbuild.yaml (sketch — the scaffold generates a fuller one)
steps:
  - name: python:3.12
    entrypoint: bash
    args:
      - -c
      - |
        pip install uv && uv sync
        uv run adk eval backend/social_poster evals/golden.json \
          --config_file_path evals/eval_config.json
  - name: gcr.io/cloud-builders/gcloud
    args: ["run", "deploy", "social-poster", "--source", "."]
```

The eval step must **fail the build** on a regression (non-zero exit), so a
change that breaks approval-gating or PII redaction never reaches prod. Push a
trivial change and watch the trigger run eval → deploy in the Cloud Build
history. 📸 That screenshot is the Week 4 "quality gate" slide.

> **Eval in CI needs the memory case handled.** `consults_memory` needs the A2A
> memory agent reachable. Either run the mock as a sidecar in the build step,
> or split it into a separate suite that only runs where memory is deployed.

## 5. The one real post, then flip back

Once prod is verified on dry-run: flip `DRY_RUN=false` in Secret Manager, make
**one** real post from prod to prove the happy path, then flip it back to
`true`. (This is the same discipline as the local one-real-post rule.)

## 6. Tear-down note

Cloud Run scales to zero, so idle cost is ~nil — but the memory agent
(Agent Engine, if deployed) does NOT. Re-read
[agent-engine-rag-setup.md](agent-engine-rag-setup.md) §6 for that tear-down.
📊 **Record ACTUAL total POC spend** (billing report, whole project, whole
build) — that's the number for Week 4 slide 2.
