"""
Starlette proxy for vLLM that logs conversations.
Normally started by run.py; can also be run directly for testing.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

import config

VLLM_URL = config.VLLM_URL
LOG_PATH  = Path(config.LOG_FILE)

# Session tracking: ip -> (session_id, last_seen_monotonic)
SESSION_TIMEOUT = 30 * 60
_sessions: dict[str, tuple[str, float]] = {}

# Last logged entry per session_id, for supersession detection
_last_entry: dict[str, dict] = {}


def _get_client_host(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-host")
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else "unknown")
    )


def _get_or_create_session(host: str) -> str:
    now = time.monotonic()
    if host in _sessions:
        session_id, last_seen = _sessions[host]
        if now - last_seen < SESSION_TIMEOUT:
            _sessions[host] = (session_id, now)
            return session_id
    session_id = str(uuid.uuid4())
    _sessions[host] = (session_id, now)
    return session_id


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _is_internal_call(request_body: dict) -> bool:
    tools = request_body.get("tools", [])
    tool_names = {t.get("function", {}).get("name") for t in tools if isinstance(t, dict)}
    if "respond_in_schema" in tool_names:
        return True
    for msg in request_body.get("messages", []):
        if msg.get("role") == "system":
            if "selecting memories" in _extract_text(msg.get("content", "")):
                return True
    return False


def _extract_conversation(request_body: dict) -> list[dict]:
    turns = []
    for msg in request_body.get("messages", []):
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg.get("content", "")).strip()
        if text:
            turns.append({"role": role, "text": text})
    return turns


def _extract_assistant_reply(response_body: dict) -> str:
    parts = []
    for choice in response_body.get("choices", []):
        text = _extract_text(choice.get("message", {}).get("content") or "")
        if text:
            parts.append(text)
    return "".join(parts)


def _supersedes(new_conv: list[dict], old_entry: dict) -> bool:
    old_conv = old_entry.get("conversation", [])
    if len(new_conv) <= len(old_conv):
        return False
    return all(new_conv[i] == old_conv[i] for i in range(len(old_conv)))


def _log_entry(session_id: str, conversation: list[dict], reply: str, host: str, latency: float):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_host": host,
        "conversation": conversation,
        "reply": reply,
        "_latency_s": round(latency, 3),
    }

    old = _last_entry.get(session_id)
    if old and _supersedes(conversation, old):
        old_line = json.dumps(old)
        try:
            text = LOG_PATH.read_text()
            LOG_PATH.write_text(text.replace(old_line + "\n", "", 1))
        except Exception:
            pass

    _last_entry[session_id] = entry
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _parse_sse_reply(raw: bytes) -> str:
    parts: list[str] = []
    for line in raw.decode(errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    parts.append(delta["content"])
        except Exception:
            continue
    return "".join(parts)


async def proxy(request: Request) -> Response:
    path = request.path_params["path"]
    host = _get_client_host(request)

    body_bytes = await request.body()
    request_body: dict = {}
    if body_bytes:
        try:
            request_body = json.loads(body_bytes)
        except Exception:
            request_body = {"raw": body_bytes.decode(errors="replace")}

    should_log = (
        path == "v1/chat/completions"
        and isinstance(request_body, dict)
        and not _is_internal_call(request_body)
    )
    session_id = _get_or_create_session(host) if should_log else None
    stream_requested = request_body.get("stream", False) if isinstance(request_body, dict) else False

    url = f"{VLLM_URL}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}

    async with httpx.AsyncClient(timeout=300) as client:
        if stream_requested:
            chunks: list[bytes] = []
            start = time.monotonic()

            async with client.stream(
                request.method, url, headers=headers,
                params=dict(request.query_params), content=body_bytes,
            ) as upstream:
                status_code = upstream.status_code
                resp_headers = dict(upstream.headers)
                async for chunk in upstream.aiter_bytes():
                    chunks.append(chunk)

            elapsed = time.monotonic() - start
            full_body = b"".join(chunks)

            if should_log:
                _log_entry(session_id, _extract_conversation(request_body),
                           _parse_sse_reply(full_body), host, elapsed)

            safe_headers = {k: v for k, v in resp_headers.items() if k.lower() != "content-encoding"}
            return Response(content=full_body, status_code=status_code,
                            headers=safe_headers,
                            media_type=resp_headers.get("content-type", "text/event-stream"))

        else:
            start = time.monotonic()
            resp = await client.request(
                request.method, url, headers=headers,
                params=dict(request.query_params), content=body_bytes,
            )
            elapsed = time.monotonic() - start

            if should_log:
                try:
                    response_body = resp.json()
                except Exception:
                    response_body = {}
                _log_entry(session_id, _extract_conversation(request_body),
                           _extract_assistant_reply(response_body), host, elapsed)

            return Response(content=resp.content, status_code=resp.status_code,
                            headers=dict(resp.headers),
                            media_type=resp.headers.get("content-type"))


app = Starlette(routes=[
    Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
])

if __name__ == "__main__":
    print(f"Proxy listening on :{config.PROXY_PORT}")
    print(f"  → forwarding to {VLLM_URL}")
    print(f"  → logging conversations to {LOG_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=config.PROXY_PORT)
