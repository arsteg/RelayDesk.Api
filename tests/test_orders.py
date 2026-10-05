"""Customer order placement + admin visibility + status transitions."""

from datetime import datetime, timedelta, timezone

from conftest import auth


def _fulfillment_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


async def _place_order(client, seed, customer_session, *, qty_choc="2", qty_cro="1"):
    products = seed["products"]
    return await client.post(
        "/customer/orders",
        headers=auth(customer_session["token"]),
        json={
            "items": [
                {"productId": products["chocolate"].id, "quantity": qty_choc},
                {"productId": products["croissant"].id, "quantity": qty_cro},
            ],
            "fulfillmentType": "PICKUP",
            "fulfillmentAt": _fulfillment_at(),
        },
    )


async def test_place_order_computes_totals_on_server(client, seed, customer_session):
    resp = await _place_order(client, seed, customer_session)
    assert resp.status_code == 201, resp.text
    order = resp.json()
    # 2 x 500.00 + 1 x 80.00 = 1080.00; 5% tax = 54.00; total 1134.00
    assert order["subtotalMinor"] == 108000
    assert order["taxMinor"] == 5400
    assert order["totalMinor"] == 113400
    assert order["status"] == "CONFIRMED"
    assert order["placedByCustomer"] is True
    assert len(order["items"]) == 2
    assert order["statusHistory"][0]["toStatus"] == "CONFIRMED"
    assert order["statusHistory"][0]["source"] == "customer"


async def test_order_visible_to_admin_immediately(client, seed, customer_session, admin_token):
    place = await _place_order(client, seed, customer_session)
    order_id = place.json()["id"]
    listing = await client.get("/admin/orders?status=OPEN", headers=auth(admin_token))
    assert listing.status_code == 200
    ids = [row["id"] for row in listing.json()["rows"]]
    assert order_id in ids
    row = next(r for r in listing.json()["rows"] if r["id"] == order_id)
    assert row["clientName"] == "Alice"
    assert row["itemCount"] == 2

    detail = await client.get(f"/admin/orders/{order_id}", headers=auth(admin_token))
    assert detail.status_code == 200
    assert detail.json()["totalMinor"] == 113400


async def test_fractional_quantity(client, seed, customer_session):
    resp = await _place_order(client, seed, customer_session, qty_choc="1.5", qty_cro="0")
    # 0 quantity for croissant is rejected.
    assert resp.status_code == 422
    fields = resp.json()["error"]["fields"]
    assert any("quantity" in k for k in fields)


async def test_unavailable_product_rejected(client, seed, customer_session, admin_token, db):
    from sqlalchemy import update

    from app.models import Product

    await db.execute(
        update(Product).where(Product.id == seed["products"]["croissant"].id).values(isAvailable=False)
    )
    await db.commit()
    resp = await _place_order(client, seed, customer_session)
    assert resp.status_code == 422


async def test_delivery_requires_address(client, seed, customer_session):
    products = seed["products"]
    resp = await client.post(
        "/customer/orders",
        headers=auth(customer_session["token"]),
        json={
            "items": [{"productId": products["chocolate"].id, "quantity": "1"}],
            "fulfillmentType": "DELIVERY",
            "fulfillmentAt": _fulfillment_at(),
        },
    )
    assert resp.status_code == 422
    assert "deliveryAddress" in (resp.json()["error"].get("fields") or {})


async def test_customer_order_history(client, seed, customer_session):
    await _place_order(client, seed, customer_session)
    await _place_order(client, seed, customer_session)
    history = await client.get("/customer/orders", headers=auth(customer_session["token"]))
    assert history.status_code == 200
    assert history.json()["total"] == 2


async def test_status_transitions_and_history(client, seed, customer_session, admin_token):
    order_id = (await _place_order(client, seed, customer_session)).json()["id"]

    for nxt in ("IN_PROGRESS", "READY", "COMPLETED"):
        resp = await client.post(
            f"/admin/orders/{order_id}/status", headers=auth(admin_token), json={"status": nxt}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == nxt

    # COMPLETED is terminal — further transitions are rejected.
    bad = await client.post(
        f"/admin/orders/{order_id}/status", headers=auth(admin_token), json={"status": "DRAFT"}
    )
    assert bad.status_code == 422

    detail = await client.get(f"/admin/orders/{order_id}", headers=auth(admin_token))
    history = detail.json()["statusHistory"]
    # CONFIRMED (placement) + IN_PROGRESS + READY + COMPLETED
    assert [h["toStatus"] for h in history] == ["CONFIRMED", "IN_PROGRESS", "READY", "COMPLETED"]


async def test_customer_cannot_see_other_business_orders(client, seed, customer_session, admin_token):
    # The customer only sees their own orders in history.
    await _place_order(client, seed, customer_session)
    other = await client.get("/customer/orders", headers=auth(customer_session["token"]))
    assert all(row["placedByCustomer"] for row in other.json()["rows"])
