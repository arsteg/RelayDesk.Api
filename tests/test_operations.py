"""Order intake with per-customer pricing and order units."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import now_utc
from app.ids import cuid
from app.models import CustomerPrice, OrderItem

from .conftest import auth

pytestmark = pytest.mark.asyncio


async def test_order_bills_at_customer_price_and_records_unit(client, db, seed, customer_session):
    business_id = seed["business"].id
    client_id = customer_session["customer"]["clientId"]
    product = seed["products"]["chocolate"]  # default priceMinor 50000

    # Negotiated price for this customer: 450.00 instead of 500.00.
    db.add(CustomerPrice(id=cuid(), businessId=business_id, clientId=client_id, productId=product.id, priceMinor=45000, updatedAt=now_utc()))
    await db.commit()

    resp = await client.post(
        "/customer/orders",
        headers=auth(customer_session["token"]),
        json={
            "items": [{"productId": product.id, "quantity": "2", "unit": "piece"}],
            "fulfillmentType": "PICKUP",
            "fulfillmentAt": "2030-01-01T10:00:00",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # 2 x 450.00 = 900.00 (the customer price, not the 500.00 default)
    assert data["subtotalMinor"] == 90000

    row = (await db.execute(select(OrderItem).where(OrderItem.businessId == business_id))).scalars().first()
    assert row is not None
    assert row.unitPriceMinor == 45000
    assert row.orderUnit == "piece"
    assert row.orderedQtyMilli == 2000


async def test_order_rejects_disallowed_unit(client, seed, customer_session):
    product = seed["products"]["chocolate"]  # unit "piece", no alternate order units
    resp = await client.post(
        "/customer/orders",
        headers=auth(customer_session["token"]),
        json={
            "items": [{"productId": product.id, "quantity": "2", "unit": "kg"}],
            "fulfillmentType": "PICKUP",
            "fulfillmentAt": "2030-01-01T10:00:00",
        },
    )
    assert resp.status_code == 422, resp.text
