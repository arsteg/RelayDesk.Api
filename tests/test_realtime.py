"""Real-time order events (the broadcaster) + polling fallback."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.realtime import (
    ORDER_CREATED,
    ORDER_STATUS_CHANGED,
    business_channel,
    broadcaster,
    customer_channel,
    order_event,
)
from app.streaming import sse_response

from conftest import auth


def _fulfillment_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


async def _place(client, seed, customer_session):
    return await client.post(
        "/customer/orders",
        headers=auth(customer_session["token"]),
        json={
            "items": [{"productId": seed["products"]["chocolate"].id, "quantity": "1"}],
            "fulfillmentType": "PICKUP",
            "fulfillmentAt": _fulfillment_at(),
        },
    )


async def test_placing_order_broadcasts_to_business(client, seed, customer_session):
    channel = business_channel(seed["business"].id)
    queue = broadcaster.subscribe(channel)
    try:
        resp = await _place(client, seed, customer_session)
        assert resp.status_code == 201
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        broadcaster.unsubscribe(channel, queue)
    assert event["type"] == ORDER_CREATED
    assert event["order"]["id"] == resp.json()["id"]
    assert event["order"]["status"] == "CONFIRMED"


async def test_status_change_broadcasts_to_customer(client, seed, customer_session, admin_token):
    order_id = (await _place(client, seed, customer_session)).json()["id"]
    channel = customer_channel(customer_session["customer"]["id"])
    queue = broadcaster.subscribe(channel)
    try:
        resp = await client.post(
            f"/admin/orders/{order_id}/status", headers=auth(admin_token), json={"status": "IN_PROGRESS"}
        )
        assert resp.status_code == 200
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        broadcaster.unsubscribe(channel, queue)
    assert event["type"] == ORDER_STATUS_CHANGED
    assert event["order"]["id"] == order_id
    assert event["order"]["status"] == "IN_PROGRESS"


async def test_polling_fallback_with_updated_since(client, seed, customer_session, admin_token):
    order_id = (await _place(client, seed, customer_session)).json()["id"]
    detail = await client.get(f"/admin/orders/{order_id}", headers=auth(admin_token))
    updated_at = detail.json()["updatedAt"]

    # Nothing changed since updatedAt -> empty page.
    empty = await client.get(f"/admin/orders?updatedSince={updated_at}", headers=auth(admin_token))
    assert empty.json()["total"] == 0

    # After a status change the order shows up in the polling window.
    await client.post(f"/admin/orders/{order_id}/status", headers=auth(admin_token), json={"status": "READY"})
    changed = await client.get(f"/admin/orders?updatedSince={updated_at}", headers=auth(admin_token))
    assert order_id in [r["id"] for r in changed.json()["rows"]]


async def test_sse_stream_requires_auth(client):
    # Auth happens before streaming begins, so a bad token fails fast with 401.
    resp = await client.get("/admin/realtime/stream?token=bogus")
    assert resp.status_code == 401


class _FakeRequest:
    """Minimal stand-in for Starlette's Request for the SSE generator."""

    async def is_disconnected(self) -> bool:
        return False


async def test_sse_generator_formats_and_delivers_events(seed):
    # Exercise the real SSE generator directly (httpx's ASGITransport cannot read
    # an endless event-stream reliably). It must emit the handshake, then the
    # published event as a formatted `event:`/`data:` frame.
    channel = business_channel(seed["business"].id)
    resp = await sse_response(_FakeRequest(), channel)
    agen = resp.body_iterator
    handshake = await asyncio.wait_for(agen.__anext__(), timeout=2)
    assert b"retry" in handshake

    await broadcaster.publish(channel, order_event(ORDER_CREATED, {"id": "abc", "status": "CONFIRMED"}))
    collected = b""
    for _ in range(6):
        collected += await asyncio.wait_for(agen.__anext__(), timeout=2)
        if b"order.created" in collected:
            break
    assert b"event: order.created" in collected
    assert b'"id":"abc"' in collected
    await agen.aclose()
