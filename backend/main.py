"""AG-UI bridge: exposes the social_poster agent to the CopilotKit frontend.

Run from the repo root:  uv run uvicorn backend.main:app --port 8000
"""

import logging
import pathlib

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.adk.memory import VertexAiMemoryBankService
from google.adk.sessions import VertexAiSessionService

log = logging.getLogger(__name__)

load_dotenv(pathlib.Path(__file__).parent / "social_poster" / ".env")

from backend.social_poster import config, db  # noqa: E402  (needs .env first)
from backend.social_poster.agent import root_agent  # noqa: E402  (needs .env first)

app = FastAPI(title="social-agent AG-UI backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Managed session + memory services ----------------------------------------
# ag_ui_adk defaults to InMemorySessionService and InMemoryMemoryService, which
# are fine locally but lose everything when the container goes away — and on
# Agent Runtime with min_instances=0 that happens on every scale-to-zero, not
# just a redeploy. Deployed, both are backed by the Agent Engine instead.
#
# The engine discovers its own identity from the APP_URL that Agent Runtime
# injects (see config.resolve_agent_engine), so nothing needs configuring by
# hand. Locally there is no APP_URL, so both fall back and `uv run uvicorn`
# behaves exactly as it did before.
_engine = config.resolve_agent_engine()

if _engine:
    # Passing agent_engine_id explicitly means app_name stays a readable name
    # rather than having to be the numeric engine id.
    session_service = VertexAiSessionService(
        project=_engine["project"],
        location=_engine["location"],
        agent_engine_id=_engine["engine"],
    )
    # Memory Bank: the managed alternative to this project's hand-rolled
    # memory_agent (a RemoteA2aAgent over a separate RAG service — see
    # docs/agent-engine-rag-setup.md). Passing a memory service also turns on
    # ag_ui_adk's automatic session ingestion, so finished conversations become
    # searchable memory without an explicit add_session_to_memory call.
    memory_service = VertexAiMemoryBankService(
        project=_engine["project"],
        location=_engine["location"],
        agent_engine_id=_engine["engine"],
    )
    log.info("Managed sessions + Memory Bank on engine %s", _engine["engine"])
else:
    session_service = None  # ag_ui_adk falls back to its in-memory services
    memory_service = None
    log.info("In-memory sessions and memory — neither survives a restart")

adk_agent = ADKAgent(
    adk_agent=root_agent,
    app_name="social_poster",
    user_id="devcamp-user",  # single-user POC; extract from auth in real apps
    session_service=session_service,
    memory_service=memory_service,
)

add_adk_fastapi_endpoint(app, adk_agent, path="/api/adk")

# Serve generate_image's output files so the frontend's image gallery
# (PostGallery.tsx) can render them — tools.py writes local file paths, which
# the browser has no way to read directly otherwise.
GALLERY_DIR = pathlib.Path(__file__).resolve().parents[1] / "gallery"
GALLERY_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=GALLERY_DIR), name="outputs")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/posts")
def posts() -> dict:
    """Published posts, with whatever image they used.

    Durable storage, unlike ADK session state or the browser's own chat
    history: SQLite locally, one object per post in GCS when deployed (a
    container filesystem does not survive a scale-to-zero). See db.py."""
    return {"posts": db.list_posts()}
