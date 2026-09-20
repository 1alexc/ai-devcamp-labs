"""AG-UI bridge: exposes the social_poster agent to the CopilotKit frontend.

Run from the repo root:  uv run uvicorn backend.main:app --port 8000
"""

import logging
import os
import pathlib
import re

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.adk.sessions import VertexAiSessionService

log = logging.getLogger(__name__)

load_dotenv(pathlib.Path(__file__).parent / "social_poster" / ".env")

from backend.social_poster import db  # noqa: E402  (needs .env first)
from backend.social_poster.agent import root_agent  # noqa: E402  (needs .env first)

app = FastAPI(title="social-agent AG-UI backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Session durability ------------------------------------------------------
# ag_ui_adk defaults to InMemorySessionService, which is fine locally but loses
# every conversation when the container goes away — and on Agent Runtime with
# min_instances=0 that happens on every scale-to-zero, not just a redeploy.
# Deployed, we use Agent Engine's managed sessions instead.
#
# Agent Runtime injects APP_URL with the engine's own resource name in it, so
# the engine can discover its own id without being told. Everything is
# overridable by env var, and locally none of it is set — so `uv run uvicorn`
# keeps the in-memory default and behaves exactly as before.
_APP_URL_RE = re.compile(
    r"/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)"
    r"/reasoningEngines/(?P<engine>\d+)"
)


def _agent_engine_context() -> dict[str, str] | None:
    """Project, location and engine id when running on Agent Runtime."""
    engine = os.environ.get("AGENT_ENGINE_ID", "").strip()
    match = _APP_URL_RE.search(os.environ.get("APP_URL", ""))
    if not engine and not match:
        return None  # not deployed — local dev
    return {
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT")
        or (match.group("project") if match else ""),
        # Deliberately NOT GOOGLE_CLOUD_LOCATION: agents-cli sets that to
        # `global` for model resolution, but sessions live in the engine's own
        # region, which APP_URL is the reliable source for.
        "location": os.environ.get("AGENT_ENGINE_LOCATION")
        or (match.group("location") if match else ""),
        "engine": engine or (match.group("engine") if match else ""),
    }


_engine = _agent_engine_context()
if _engine and all(_engine.values()):
    session_service = VertexAiSessionService(
        project=_engine["project"],
        location=_engine["location"],
        # Passing this explicitly means app_name stays a readable name rather
        # than having to be the numeric engine id.
        agent_engine_id=_engine["engine"],
    )
    log.info("Using managed Agent Engine sessions (engine %s)", _engine["engine"])
else:
    session_service = None  # ag_ui_adk falls back to InMemorySessionService
    log.info("Using in-memory sessions — conversations will not survive a restart")

adk_agent = ADKAgent(
    adk_agent=root_agent,
    app_name="social_poster",
    user_id="devcamp-user",  # single-user POC; extract from auth in real apps
    session_service=session_service,
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
    """Published posts (with whatever image they used), read from the local
    SQLite store — durable across reloads/restarts, unlike ADK session state
    or the browser's own chat history."""
    return {"posts": db.list_posts()}
