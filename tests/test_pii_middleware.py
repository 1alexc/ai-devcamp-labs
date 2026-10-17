"""Tests for backend/pii_middleware.py (pure ASGI RedactUserMessages middleware)."""

import asyncio
import json
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import pytest
import uvicorn

from backend.pii_middleware import RedactUserMessages, deidentify_text, _process_payload
from backend.social_poster import guardrails


# --- 1. Payload processing & message formats ----------------------------------


@pytest.mark.anyio
async def test_process_payload_string_content():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "Contact John Doe at john@example.com",
            },
            {
                "role": "assistant",
                "content": "Contact Alice Smith at alice@example.com",
            },
        ]
    }

    async def mock_deidentify(text: str) -> str:
        return text.replace("John Doe", "[PERSON_NAME]").replace(
            "john@example.com", "[EMAIL_ADDRESS]"
        )

    with patch("backend.pii_middleware.deidentify_text", side_effect=mock_deidentify):
        await _process_payload(payload)

    # User message redacted
    assert payload["messages"][0]["content"] == "Contact [PERSON_NAME] at [EMAIL_ADDRESS]"
    # Assistant message untouched
    assert payload["messages"][1]["content"] == "Contact Alice Smith at alice@example.com"


@pytest.mark.anyio
async def test_process_payload_list_of_parts_content():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Call Bob at +1-555-1234"},
                    {"type": "image", "url": "http://example.com/image.png"},
                    "Reach Charlie at charlie@example.com",
                ],
            }
        ]
    }

    async def mock_deidentify(text: str) -> str:
        return (
            text.replace("Bob", "[PERSON_NAME]")
            .replace("+1-555-1234", "[PHONE_NUMBER]")
            .replace("Charlie", "[PERSON_NAME]")
            .replace("charlie@example.com", "[EMAIL_ADDRESS]")
        )

    with patch("backend.pii_middleware.deidentify_text", side_effect=mock_deidentify):
        await _process_payload(payload)

    user_content = payload["messages"][0]["content"]
    assert user_content[0]["text"] == "Call [PERSON_NAME] at [PHONE_NUMBER]"
    assert user_content[1] == {"type": "image", "url": "http://example.com/image.png"}
    assert user_content[2] == "Reach [PERSON_NAME] at [EMAIL_ADDRESS]"


@pytest.mark.anyio
async def test_process_payload_parts_field():
    payload = {
        "messages": [
            {
                "role": "user",
                "parts": [
                    {"text": "Call Bob at +1-555-1234"},
                ],
            }
        ]
    }

    async def mock_deidentify(text: str) -> str:
        return text.replace("Bob", "[PERSON_NAME]").replace(
            "+1-555-1234", "[PHONE_NUMBER]"
        )

    with patch("backend.pii_middleware.deidentify_text", side_effect=mock_deidentify):
        await _process_payload(payload)

    assert payload["messages"][0]["parts"][0]["text"] == "Call [PERSON_NAME] at [PHONE_NUMBER]"


# --- 2. Middleware ASGI routing & fail-closed 503 ------------------------------


@pytest.mark.anyio
async def test_non_matching_path_passes_through():
    received_scope = {}

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = RedactUserMessages(mock_app)
    scope = {"type": "http", "method": "GET", "path": "/healthz", "headers": []}

    async def mock_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def mock_send(msg):
        sent.append(msg)

    await middleware(scope, mock_receive, mock_send)
    assert sent[0]["status"] == 200
    assert received_scope["path"] == "/healthz"


@pytest.mark.anyio
async def test_dlp_failure_returns_503_and_blocks_upstream():
    app_called = False

    async def mock_app(scope, receive, send):
        nonlocal app_called
        app_called = True

    middleware = RedactUserMessages(mock_app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/adk",
        "headers": [(b"content-type", b"application/json")],
    }

    body_bytes = json.dumps({
        "messages": [{"role": "user", "content": "Sensitive secret data"}]
    }).encode("utf-8")

    async def mock_receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    sent = []

    async def mock_send(msg):
        sent.append(msg)

    # Simulate DLP API error
    with patch(
        "backend.pii_middleware.deidentify_text",
        side_effect=RuntimeError("Cloud DLP 500 error"),
    ):
        await middleware(scope, mock_receive, mock_send)

    # The downstream app was NEVER called
    assert app_called is False
    # Middleware sent 503 response
    assert sent[0]["status"] == 503
    body_response = json.loads(sent[1]["body"].decode("utf-8"))
    assert "error" in body_response


# --- 3. Live Streaming Server Test (Verifies receive() trap) ------------------


def test_streaming_response_against_real_uvicorn_server():
    """Starts a real uvicorn server with an SSE-style streaming endpoint on /api/adk.

    Verifies that RedactUserMessages:
    1. Redacts PII in the user message
    2. Does NOT cause the streaming disconnect listener to spin or hang
    3. Successfully streams chunks to completion
    """
    app = FastAPI()
    app.add_middleware(RedactUserMessages)

    received_body = {}

    @app.post("/api/adk")
    async def stream_adk(request: Request):
        nonlocal received_body
        received_body = await request.json()

        async def event_generator():
            for i in range(3):
                # Check for disconnect, which calls receive()
                if await request.is_disconnected():
                    break
                yield f"data: event {i}\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    # Run uvicorn on an ephemeral port in a daemon thread
    server_port = 8765
    config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{server_port}/docs", timeout=0.5)
            break
        except Exception:
            time.sleep(0.05)

    try:
        # Mock deidentify_text to verify payload transformation
        async def mock_deidentify(text: str) -> str:
            return text.replace("Priya Sharma", "[PERSON_NAME]").replace(
                "priya@example.com", "[EMAIL_ADDRESS]"
            )

        with patch("backend.pii_middleware.deidentify_text", side_effect=mock_deidentify):
            payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": "Contact Priya Sharma at priya@example.com",
                    }
                ]
            }

            # Send streaming request with a strict timeout (e.g. 5s).
            # If the receive() trap was present, the disconnect listener would spin
            # and the request would time out or hang.
            with httpx.Client(timeout=5.0) as client:
                with client.stream(
                    "POST", f"http://127.0.0.1:{server_port}/api/adk", json=payload
                ) as response:
                    assert response.status_code == 200
                    lines = [line for line in response.iter_lines() if line]

            assert len(lines) == 3
            assert lines[0] == "data: event 0"
            assert lines[1] == "data: event 1"
            assert lines[2] == "data: event 2"

            # Check that the server endpoint received the redacted body
            assert (
                received_body["messages"][0]["content"]
                == "Contact [PERSON_NAME] at [EMAIL_ADDRESS]"
            )
    finally:
        server.should_exit = True
        thread.join(timeout=1.0)


# --- 4. Switch tests -----------------------------------------------------------


def test_redact_before_post_switch():
    class DummyTool:
        name = "create_post"

    args = {"text": "Call Alice at alice@example.com"}

    with patch.dict("os.environ", {"PII_REDACTION": "off"}):
        assert guardrails.is_pii_enabled() is False
        res = guardrails.redact_before_post(DummyTool(), args, None)
        assert res is None
        assert args["text"] == "Call Alice at alice@example.com"

    with patch.dict("os.environ", {"PII_REDACTION": "on"}):
        assert guardrails.is_pii_enabled() is True
        with patch("backend.social_poster.guardrails.redact_pii", return_value="Call [PERSON_NAME] at [EMAIL_ADDRESS]"):
            guardrails.redact_before_post(DummyTool(), args, None)
            assert args["text"] == "Call [PERSON_NAME] at [EMAIL_ADDRESS]"

