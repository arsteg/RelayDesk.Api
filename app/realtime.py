"""In-process async pub/sub for real-time order events.

Events are published to per-business channels (``business:<id>``) and per-customer
channels (``customer:<id>``). SSE and WebSocket endpoints subscribe to a channel
and receive a bounded queue of events. This is correct for a single API
instance; a multi-instance deployment needs a shared bus (e.g. Redis pub/sub) —
see docs/manual-setup-required.md. The event contract is identical on both
transports so web (SSE) and mobile (WebSocket) behave the same.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

# Event types emitted on the wire (shared with packages/shared/realtime.ts).
ORDER_CREATED = "order.created"
ORDER_UPDATED = "order.updated"
ORDER_STATUS_CHANGED = "order.status_changed"


def business_channel(business_id: str) -> str:
    return f"business:{business_id}"


def customer_channel(customer_id: str) -> str:
    return f"customer:{customer_id}"


class Broadcaster:
    def __init__(self, max_queue: int = 100) -> None:
        self._channels: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._max_queue = max_queue

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._channels[channel].add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        subs = self._channels.get(channel)
        if subs:
            subs.discard(queue)
            if not subs:
                self._channels.pop(channel, None)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        for queue in list(self._channels.get(channel, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow consumer must not block producers; it will reconnect
                # and fall back to polling to catch up.
                pass

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))


# Single shared instance for the process.
broadcaster = Broadcaster()


def order_event(event_type: str, order: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "order": order}
