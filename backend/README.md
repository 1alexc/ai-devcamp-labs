# Backend — social_poster ADK agent

FastAPI + [Google ADK](https://google.github.io/adk-docs/) multi-agent app:
an orchestrator (`social_poster`) that routes to `research_agent`,
`draft_agent`, an optional `memory_agent` (via A2A), and posting tools for
LinkedIn/Buffer. Exposed to the frontend over [AG-UI](https://github.com/ag-ui-protocol/ag-ui)
at `/api/adk`.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A GCP project with the Vertex AI API enabled, and
  `GOOGLE_APPLICATION_CREDENTIALS` pointing at a service-account key file
  (some optional features — see below — need a real key, not bare
  `gcloud auth application-default login` user creds, to sign URLs/tokens).

## Setup

From the repo root:

```bash
cp .env.example backend/social_poster/.env
```

Fill in `backend/social_poster/.env` — at minimum `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_APPLICATION_CREDENTIALS`. Everything else in the file is optional and
commented with what it unlocks.

```bash
uv sync   # from the repo root, pyproject.toml lives there
```

## Run

From the repo root:

```bash
./run-backend.sh
```

(if you get `permission denied`, the script lost its executable bit — fix
once with `chmod +x run-backend.sh run-frontend.sh` from the repo root)

which is equivalent to:

```bash
uv run uvicorn backend.main:app --port 8000 --reload
```

This serves:

- `POST /api/adk` — the AG-UI endpoint the frontend talks to
- `GET /healthz` — health check
- `GET /outputs/...` — generated images (the `gallery/` dir at the repo root)

### Try it without the frontend

```bash
cd backend && uv run adk web
```

Opens ADK's own dev UI (chat + a trace inspector) so you can exercise the
agent — including watching tool calls fire — without running the Next.js app.

## Optional pieces

Each of these is off by default; the agent still works without any of them.

| Feature | Env var | Notes |
|---|---|---|
| Publish to LinkedIn | `LINKEDIN_ACCESS_TOKEN` | 3-legged OAuth token; see [mcp/README.md](../mcp/README.md) for the developer-app setup |
| Publish/schedule via Buffer | `BUFFER_API_KEY` | free Buffer account, hosted MCP server, nothing to run locally |
| Force one posting route | `POST_VIA=buffer` or `linkedin` | blank (default) lets the agent route by what the user asks for |
| Let Buffer attach generated images | `GCS_BUCKET_NAME` | bucket the `GOOGLE_APPLICATION_CREDENTIALS` service account can write to (`roles/storage.objectAdmin`) — see the comment above it in `.env.example` |
| Recall the user's past posts | `MEMORY_AGENT_CARD_URL` | local mock: `uv run python backend/mock_memory_agent.py`, then point at `http://localhost:8001/.well-known/agent.json`; real deploy: [docs/agent-engine-rag-setup.md](../docs/agent-engine-rag-setup.md) |
| Guardrails (Model Armor + DLP) | `MODEL_ARMOR_TEMPLATE_ID` | see [docs/model-armor-setup.md](../docs/model-armor-setup.md) |

`DRY_RUN=true` (the default) makes LinkedIn/Buffer posting log the payload
and return a fake URL instead of actually publishing. Keep it `true` except
for one deliberate happy-path check, then flip it back.

## Evals

```bash
uv run adk eval backend/social_poster evals/golden.json \
  --config_file_path evals/eval_config.json
```

See [docs/cloud-run-deploy.md](../docs/cloud-run-deploy.md) for why CI wraps
this instead of running it bare (`adk eval` always exits 0, even on failure).

## Deploying

See [docs/cloud-run-deploy.md](../docs/cloud-run-deploy.md) for the full
Dockerfile → Cloud Build → Cloud Run path, and
[docs/observability.md](../docs/observability.md) for tracing once it's live.
