"""SSE and WebSocket plumbing over the in-process broadcaster.

Web clients use SSE (``EventSource``) because it reconnects automatically and
flows through the Next.js same-origin rewrite with cookies. Mobile uses the
WebSocket variant. Both carry the identical event payload. If either stream
drops, clients fall back to polling the list endpoints with ``updatedSince``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import Request, WebSocket
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from .realtime import broadcaster

_HEARTBEAT_SECONDS = 20


async def sse_response(request: Request, channel: str) -> StreamingResponse:
    queue = broadcaster.subscribe(channel)

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            # Prompt the client to set its reconnect delay and confirm the open.
            yield b"retry: 3000\n"
            yield b": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield b": heartbeat\n\n"
                    continue
                payload = json.dumps(event, separators=(",", ":"))
                yield f"event: {event.get('type', 'message')}\ndata: {payload}\n\n".encode("utf-8")
        finally:
            broadcaster.unsubscribe(channel, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def websocket_stream(websocket: WebSocket, channel: str) -> None:
    await websocket.accept()
    queue = broadcaster.subscribe(channel)
    await websocket.send_json({"type": "connected"})

    async def pump() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    async def drain() -> None:
        # Read (and ignore) client frames so a disconnect is detected promptly.
        while True:
            await websocket.receive_text()

    pump_task = asyncio.create_task(pump())
    drain_task = asyncio.create_task(drain())
    try:
        done, pending = await asyncio.wait({pump_task, drain_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        drain_task.cancel()
        broadcaster.unsubscribe(channel, queue)
