"""Events router — SSE streams for live updates and logs.

Two endpoints:

* GET /events/stream — broadcast bus. Other parts of the app call
  ``event_broker.publish({...})`` and every connected SSE client gets a
  copy. Used by the frontend's right-panel and any "live" surface.

* GET /events/logs — server-side ``tail -F`` of the mollo_brain log file
  emitted as parsed log events. Stand-alone — not coupled to the broker.

Both use plain SSE (``data: {json}\\n\\n``) so the frontend can read them
with ``EventSource`` or a manual ReadableStream parser.
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse


router = APIRouter(prefix="/events", tags=["Events"])

LOG_PATH = Path(os.environ.get("MOLLO_BRAIN_LOG", "/var/log/mollo_brain.log"))


# ── Broadcast bus ─────────────────────────────────────────────────────────────

class EventBroker:
    """Tiny in-process pub/sub. One asyncio.Queue per subscriber.

    We bound each queue so a slow client can't grow our memory. If we
    overflow, oldest events are dropped — better than blocking publishers.
    """

    def __init__(self, max_per_subscriber: int = 256):
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max = max_per_subscriber

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Fire-and-forget. Adds id+ts if missing. Drops oldest on overflow."""
        event.setdefault("id", f"evt-{int(time.time() * 1000)}-{id(event) & 0xffff:04x}")
        event.setdefault("ts", int(time.time() * 1000))
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drain one and retry; if still full, give up on this client.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


event_broker = EventBroker()


# ── /events/stream ────────────────────────────────────────────────────────────

async def _broadcast_generator(request: Request):
    q = event_broker.subscribe()
    try:
        # Send a hello so the client knows the stream is open.
        yield f"data: {json.dumps({'type': 'hello', 'ts': int(time.time() * 1000)})}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=20)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                # Heartbeat keeps proxies from idle-killing the connection.
                yield ": ping\n\n"
    finally:
        event_broker.unsubscribe(q)


@router.get("/stream")
async def stream_events(request: Request):
    return StreamingResponse(
        _broadcast_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /events/logs ──────────────────────────────────────────────────────────────

# Uvicorn writes lines like "INFO:     127.0.0.1:54930 - "GET /health HTTP/1.1" 200 OK"
# and our own logs use "LEVEL:    message". We normalize both.
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVEL_MAP = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "WARN": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}
_LEVEL_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*:\s*(.*)$")


def _parse_log_line(raw: str) -> dict[str, Any] | None:
    line = raw.rstrip("\n")
    if not line.strip():
        return None
    m = _LEVEL_RE.match(line)
    if m:
        level = _LEVEL_MAP.get(m.group(1), "info")
        message = m.group(2).strip()
    else:
        level = "info"
        message = line.strip()
    # Heuristic source: HTTP requests come from uvicorn, anything else 'app'
    source = "uvicorn" if "HTTP/1." in message else "mollo_brain"
    return {
        "id": f"log-{int(time.time() * 1000)}-{hash(line) & 0xffff:04x}",
        "ts": int(time.time() * 1000),
        "type": "log",
        "payload": {
            "level": level,
            "source": source,
            "message": message,
            "raw": line,
        },
    }


async def _tail_log(request: Request, tail_lines: int = 200):
    """Stream the last `tail_lines` of the log file then follow new writes."""
    if not LOG_PATH.exists():
        yield f"data: {json.dumps({'type': 'error', 'message': f'log file not found: {LOG_PATH}'})}\n\n"
        return

    # Send an initial backlog. Read from the tail using a simple
    # block-from-end approach so we don't load the whole file.
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            block = 4096
            data = b""
            while file_size > 0 and data.count(b"\n") <= tail_lines:
                step = min(block, file_size)
                file_size -= step
                f.seek(file_size)
                data = f.read(step) + data
            backlog = data.decode("utf-8", errors="replace").splitlines()[-tail_lines:]
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': f'tail failed: {e}'})}\n\n"
        return

    for line in backlog:
        evt = _parse_log_line(line)
        if evt:
            yield f"data: {json.dumps(evt)}\n\n"

    # Now follow appends.
    f = open(LOG_PATH, "r", encoding="utf-8", errors="replace")
    try:
        f.seek(0, os.SEEK_END)
        while True:
            if await request.is_disconnected():
                break
            line = f.readline()
            if line:
                evt = _parse_log_line(line)
                if evt:
                    yield f"data: {json.dumps(evt)}\n\n"
            else:
                await asyncio.sleep(0.5)
                # Heartbeat every ~20s
                if int(time.time()) % 20 == 0:
                    yield ": ping\n\n"
    finally:
        f.close()


@router.get("/logs")
async def stream_logs(request: Request, lines: int = 200):
    return StreamingResponse(
        _tail_log(request, tail_lines=max(1, min(lines, 1000))),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
