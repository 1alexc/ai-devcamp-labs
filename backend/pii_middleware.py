"""Pure ASGI middleware for PII redaction on POST /api/adk.

Intercepts inbound user messages on /api/adk and de-identifies personal data
(EMAIL_ADDRESS and PHONE_NUMBER at POSSIBLE, PERSON_NAME at LIKELY, no LOCATION)
via Google Cloud DLP before the agent, orchestrator, session service, or Memory Bank
can see it.

Fail-closed: If Cloud DLP fails for any reason, the middleware returns HTTP 503
and terminates the request immediately, never passing raw text upstream.

ASGI receive protocol note:
When replaying the body into the downstream ASGI app, the replacement `receive()`
must return the body once (more_body=False) and then delegate subsequent calls to
the real ASGI `receive()` channel. Streaming endpoints (such as AG-UI's SSE stream)
listen on `receive()` for client disconnects (`http.disconnect`). Replaying the body
repeatedly or returning empty request events causes the disconnect loop to spin at
100% CPU, hanging all chat requests.
"""

import json
import logging
import os
import threading
from typing import Any, Callable

import google.auth
import google.auth.transport.requests
import httpx

log = logging.getLogger(__name__)

# Cloud DLP passes:
# 1. EMAIL_ADDRESS and PHONE_NUMBER at POSSIBLE (structured data)
# 2. PERSON_NAME at LIKELY (avoid false positives on capitalized nouns)
# Explicitly NO locations.
DLP_PASSES = [
    (["EMAIL_ADDRESS", "PHONE_NUMBER"], "POSSIBLE"),
    (["PERSON_NAME"], "LIKELY"),
]

_creds = None
_creds_lock = threading.Lock()


def _get_project_id() -> str:
    """Returns the GCP alphanumeric project ID (not the numeric project number)."""
    return os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")


def _get_auth_token() -> str:
    """Returns a valid Google Cloud auth token with cloud-platform scope."""
    global _creds
    with _creds_lock:
        if _creds is None:
            _creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())
        return _creds.token


def _get_dlp_headers() -> dict[str, str]:
    project_id = _get_project_id()
    return {
        "Authorization": f"Bearer {_get_auth_token()}",
        "x-goog-user-project": project_id,
        "Content-Type": "application/json",
    }


async def deidentify_text(text: str) -> str:
    """De-identifies text using Cloud DLP.

    Runs EMAIL_ADDRESS and PHONE_NUMBER at POSSIBLE, PERSON_NAME at LIKELY.
    Raises an exception if DLP returns an error or is unreachable.
    """
    if not text or not text.strip():
        return text

    project_id = _get_project_id()
    url = f"https://dlp.googleapis.com/v2/projects/{project_id}/locations/global/content:deidentify"
    headers = _get_dlp_headers()

    current_text = text
    async with httpx.AsyncClient(timeout=15.0) as client:
        for info_types, min_likelihood in DLP_PASSES:
            payload = {
                "inspectConfig": {
                    "infoTypes": [{"name": t} for t in info_types],
                    "minLikelihood": min_likelihood,
                },
                "deidentifyConfig": {
                    "infoTypeTransformations": {
                        "transformations": [
                            {"primitiveTransformation": {"replaceWithInfoTypeConfig": {}}}
                        ]
                    }
                },
                "item": {"value": current_text},
            }
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            current_text = resp.json().get("item", {}).get("value", current_text)

    return current_text


async def _redact_user_message(msg: dict[str, Any]) -> None:
    """Redacts PII in a message dictionary where role == 'user'.

    Handles both string content and list-of-parts content.
    """
    # 1. Handle string content: {"role": "user", "content": "..."}
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = await deidentify_text(content)
    # 2. Handle list-of-parts content: {"role": "user", "content": [{"text": "..."}, ...]}
    elif isinstance(content, list):
        for idx, part in enumerate(content):
            if isinstance(part, str):
                content[idx] = await deidentify_text(part)
            elif isinstance(part, dict):
                if "text" in part and isinstance(part["text"], str):
                    part["text"] = await deidentify_text(part["text"])

    # 3. Handle list of parts under "parts": {"role": "user", "parts": [{"text": "..."}, ...]}
    parts = msg.get("parts")
    if isinstance(parts, list):
        for idx, part in enumerate(parts):
            if isinstance(part, str):
                parts[idx] = await deidentify_text(part)
            elif isinstance(part, dict):
                if "text" in part and isinstance(part["text"], str):
                    part["text"] = await deidentify_text(part["text"])

    # 4. Handle direct text field if present: {"role": "user", "text": "..."}
    text = msg.get("text")
    if isinstance(text, str):
        msg["text"] = await deidentify_text(text)


async def _process_payload(obj: Any) -> None:
    """Recursively walks arbitrary JSON structures to find all role == 'user' messages."""
    if isinstance(obj, dict):
        if obj.get("role") == "user":
            await _redact_user_message(obj)
        for val in obj.values():
            if isinstance(val, (dict, list)):
                await _process_payload(val)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                await _process_payload(item)


class RedactUserMessages:
    """Pure ASGI middleware that redacts PII from user messages on POST /api/adk."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") not in ("/api/adk", "/api/api/adk")
        ):
            await self.app(scope, receive, send)
            return

        # 1. Buffer the full request body
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                return

        # 2. Parse JSON and redact user messages
        rewritten_body = bytes(body)
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                # Not valid JSON; leave body untouched for app/framework validation
                data = None

            if data is not None:
                try:
                    await _process_payload(data)
                    rewritten_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                except Exception as e:
                    # Fail closed: DLP error returns 503 and never forwards raw text
                    log.error("DLP redaction failed on inbound user message: %s", e)
                    await self._send_503(send)
                    return

        # 3. Update Content-Length header to match rewritten body
        new_headers = [
            (name, val)
            for name, val in scope.get("headers", [])
            if name.lower() != b"content-length"
        ]
        new_headers.append((b"content-length", str(len(rewritten_body)).encode("ascii")))
        scope_copy = dict(scope)
        scope_copy["headers"] = new_headers

        # 4. Replay rewritten body once, then delegate to real receive channel
        # This prevents streaming endpoints (SSE) from spinning in a disconnect loop.
        replayed = False

        async def custom_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": rewritten_body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope_copy, custom_receive, send)

    async def _send_503(self, send: Callable) -> None:
        payload = json.dumps(
            {"error": "PII redaction service unavailable"}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": payload,
                "more_body": False,
            }
        )

