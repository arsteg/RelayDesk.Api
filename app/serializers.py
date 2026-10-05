"""Plain-dict serializers shared by HTTP responses and realtime events.

Money stays in integer minor units; `currency` is included so clients format it
with their own locale. Datetimes are stored naive-UTC and emitted as ISO 8601
with a trailing ``Z``.
"""

from __future__ import annotations

from datetime import date, datetime

from .models import Order, OrderItem, OrderStatusHistory, Product


def ev(value) -> str | None:
    """Enum value, tolerant of a value that is already a plain string or None."""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else value


def iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return value.isoformat()


def menu_item(p: Product, price_minor: int | None = None) -> dict:
    # `price_minor` overrides the default when the customer has a negotiated price.
    return {
        "id": p.id,
        "name": p.name,
        "categoryId": p.categoryId,
        "description": p.description,
        "unit": p.unit,  # pricing/billing unit
        "priceMinor": p.priceMinor if price_minor is None else price_minor,
        "isAvailable": p.isAvailable,
        "isVariableMeasure": p.isVariableMeasure,
        "orderUnits": list(p.orderUnits) if p.orderUnits else [p.unit],
    }


def order_item(i: OrderItem) -> dict:
    return {
        "id": i.id,
        "productId": i.productId,
        "description": i.description,
        "unit": i.unit,
        "quantityMilli": i.quantityMilli,
        "unitPriceMinor": i.unitPriceMinor,
        "lineTotalMinor": i.lineTotalMinor,
        "position": i.position,
    }


def status_event(h: OrderStatusHistory) -> dict:
    return {
        "id": h.id,
        "fromStatus": ev(h.fromStatus),
        "toStatus": ev(h.toStatus),
        "reason": h.reason,
        "source": h.source,
        "createdAt": iso(h.createdAt),
    }


def order_summary(o: Order, *, currency: str, client_name: str | None = None, item_count: int | None = None) -> dict:
    return {
        "id": o.id,
        "number": o.number,
        "status": ev(o.status),
        "paymentStatus": ev(o.paymentStatus),
        "clientId": o.clientId,
        "clientName": client_name,
        "fulfillmentType": ev(o.fulfillmentType),
        "fulfillmentAt": iso(o.fulfillmentAt),
        "orderDate": iso(o.orderDate),
        "totalMinor": o.totalMinor,
        "currency": currency,
        "placedByCustomer": o.placedByCustomerId is not None,
        "itemCount": item_count,
        "createdAt": iso(o.createdAt),
        "updatedAt": iso(o.updatedAt),
    }


def order_detail(
    o: Order,
    *,
    currency: str,
    client: dict | None = None,
    items: list[OrderItem] | None = None,
    history: list[OrderStatusHistory] | None = None,
) -> dict:
    data = order_summary(o, currency=currency, client_name=(client or {}).get("name"))
    data.update(
        {
            "client": client,
            "deliveryAddress": o.deliveryAddress,
            "notes": o.notes,
            "subtotalMinor": o.subtotalMinor,
            "discountMinor": o.discountMinor,
            "taxBps": o.taxBps,
            "taxMinor": o.taxMinor,
            "deliveryChargeMinor": o.deliveryChargeMinor,
            "paidMinor": o.paidMinor,
            "items": [order_item(i) for i in (items or [])],
            "statusHistory": [status_event(h) for h in (history or [])],
        }
    )
    return data
