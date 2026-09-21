"""AG-UI bridge: exposes the social_poster agent to the CopilotKit frontend.

Run from the repo root:  uv run uvicorn backend.main:app --port 8000
"""

import json
import logging
import os
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
from backend.social_poster import guardrails  # noqa: E402

class _RedactUserMessages:
    """Pure-ASGI middleware: DLP-redact every user message in an AG-UI request BEFORE the
    agent sees it. Without this the raw text (a phone number, an email) goes to Gemini and
    is written to the session store exactly as typed, and the only redaction is a tool the
    model is merely *asked* to call. Fails closed: if DLP errors, the request is refused."""

    def __init__(self, app, path: str):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            return await self.app(scope, receive, send)
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        try:
            data = json.loads(body)
            for m in data.get("messages", []):
                if m.get("role") != "user":
                    continue
                c = m.get("content")
                if isinstance(c, str):
                    m["content"] = guardrails.redact_boundary(c)
                elif isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            part["text"] = guardrails.redact_boundary(part["text"])
            body = json.dumps(data).encode()
        except json.JSONDecodeError:
            pass  # not JSON: let the endpoint reject it as it normally would
        except Exception:  # noqa: BLE001  (DLP unavailable: fail closed, never pass raw text)
            log.exception("PII redaction failed; refusing the request")
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body",
                        "body": b'{"error":"PII redaction is unavailable, so the request was refused."}'})
            return

        delivered = False

        async def replay():
            # Hand over the (rewritten) body ONCE, then defer to the real channel. A
            # streaming (SSE) response listens on receive() for http.disconnect; if this
            # kept replaying the request it would spin and the stream would hang.
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await self.app({**scope, "headers": headers}, replay, send)


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
    # docs/agent-engine-rag-setup.md). This only makes memory *searchable*.
    # ag_ui_adk's "automatic" ingestion runs on session cleanup, which a
    # scale-to-zero engine never reaches, so agent.py ingests explicitly at
    # publish time (add_session_to_memory, awaited). What the bank extracts is
    # configured on the engine: backend/memory_bank_config.py.
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

if guardrails.MA_TEMPLATE:
    # Model Armor's inline screening is SKIPPED for streaming Gemini calls: with
    # the adapter's default SSE streaming an injection sails through with no
    # block and no error (verified 2026-09-21: 3/3 streamed calls passed, the
    # non-streamed ones were blocked). So a guarded agent must not stream. The
    # cost is that replies arrive whole instead of token by token.
    from google.adk.agents.run_config import StreamingMode

    def _non_streaming_run_config(run_input):
        cfg = ADKAgent._default_run_config(adk_agent, run_input)
        cfg.streaming_mode = StreamingMode.NONE
        return cfg

    adk_agent._run_config_factory = _non_streaming_run_config
    log.info("Model Armor is on: streaming disabled so inline screening applies")

add_adk_fastapi_endpoint(app, adk_agent, path="/api/adk")

if guardrails.PII_REDACTION:
    app.add_middleware(_RedactUserMessages, path="/api/adk")
    log.info("PII redaction on: user messages are de-identified before they reach the agent")

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
