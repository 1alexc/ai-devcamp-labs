"""AG-UI bridge: exposes the social_poster agent to the CopilotKit frontend.

Run from the repo root:  uv run uvicorn backend.main:app --port 8000
"""

import os
import pathlib
import re

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.adk.memory import VertexAiMemoryBankService
from google.adk.sessions import VertexAiSessionService

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

# When running on Agent Runtime, APP_URL is injected and contains the engine's
# full resource name: projects/{project}/locations/{location}/reasoningEngines/{engine_id}.
# We can also configure session_service and memory_service for local development
# by setting AGENT_ENGINE_ID (and GOOGLE_CLOUD_PROJECT).
session_service = None
memory_service = None
app_url = os.environ.get("APP_URL", "").strip()
project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
location = os.environ.get("AGENT_ENGINE_LOCATION", "us-central1").strip()
engine_id = os.environ.get("AGENT_ENGINE_ID", "").strip()

if app_url:
    match = re.search(
        r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/([^/:]+)",
        app_url,
    )
    if match:
        project, location, engine_id = match.groups()

if project and engine_id:
    session_service = VertexAiSessionService(
        project=project,
        location=location,
        agent_engine_id=engine_id,
    )
    memory_service = VertexAiMemoryBankService(
        project=project,
        location=location,
        agent_engine_id=engine_id,
    )

adk_agent = ADKAgent(
    adk_agent=root_agent,
    app_name="social_poster",
    user_id="devcamp-user",  # single-user POC; extract from auth in real apps
    session_service=session_service,
    memory_service=memory_service,
    delete_session_on_cleanup=bool(session_service is None),
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
