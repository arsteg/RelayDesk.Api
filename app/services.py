"""Business operations for customer ordering.

These functions own the write-path transactions, commit them, and then publish
real-time events (so subscribers never see an event for an uncommitted row).
They are deliberately framework-free (take a session + context) so tests can
call them directly and HTTP routers stay thin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import now_utc
from .deps import AdminContext, CustomerContext
from .errors import ConflictError, NotFoundError, ValidationError
from .models import (
    STATUS_TRANSITIONS,
    AuditLog,
    Client,
    CustomerPrice,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    PaymentStatus,
    Product,
    FulfillmentType,
)
from .realtime import (
    ORDER_CREATED,
    ORDER_STATUS_CHANGED,
    business_channel,
    broadcaster,
    customer_channel,
    order_event,
)
from .schemas import PlaceOrderRequest, UpdateStatusRequest
from .serializers import order_detail, order_summary
from .totals import LineInput, MoneyError, compute_totals, derive_payment_status, parse_quantity_to_milli


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _today_in_tz(tz_name: str):
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date()


async def _audit(db: AsyncSession, *, business_id, actor_user_id, actor_email, action, entity_type, entity_id, metadata):
    db.add(
        AuditLog(
            businessId=business_id,
            actorUserId=actor_user_id,
            actorEmail=actor_email,
            scope="business",
            action=action,
            entityType=entity_type,
            entityId=entity_id,
            metadata_=metadata,
        )
    )


async def _allocate_order_number(db: AsyncSession, business_id: str) -> int:
    # Locks the business row and allocates the next tenant-scoped number, exactly
    # like createOrder in src/server/orders.ts.
    result = await db.execute(
        text('UPDATE "Business" SET "orderSequence" = "orderSequence" + 1 WHERE "id" = :id RETURNING "orderSequence"'),
        {"id": business_id},
    )
    row = result.first()
    if row is None:
        raise NotFoundError("Business")
    return int(row[0])


async def _load_order_detail(db: AsyncSession, order: Order, currency: str) -> dict:
    client = (await db.execute(select(Client).where(Client.id == order.clientId))).scalar_one_or_none()
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.orderId == order.id).order_by(OrderItem.position)))
        .scalars()
        .all()
    )
    history = list(
        (
            await db.execute(
                select(OrderStatusHistory)
                .where(OrderStatusHistory.orderId == order.id)
                .order_by(OrderStatusHistory.createdAt)
            )
        )
        .scalars()
        .all()
    )
    client_dict = (
        {"id": client.id, "name": client.name, "phone": client.phone, "email": client.email, "address": client.address}
        if client
        else None
    )
    return order_detail(order, currency=currency, client=client_dict, items=items, history=history)


async def place_customer_order(db: AsyncSession, ctx: CustomerContext, req: PlaceOrderRequest) -> dict:
    if ctx.business.suspended:
        raise ConflictError("This store is not accepting orders right now.")
    fulfillment_type = FulfillmentType(req.fulfillmentType)
    delivery_address = None
    if fulfillment_type == FulfillmentType.DELIVERY:
        if not (req.deliveryAddress and req.deliveryAddress.strip()):
            raise ValidationError(
                "Delivery address is required for deliveries.", fields={"deliveryAddress": "Required"}
            )
        delivery_address = req.deliveryAddress.strip()

    # Resolve products within the tenant and snapshot name/price/unit.
    product_ids = [i.productId for i in req.items]
    products = {
        p.id: p
        for p in (
            await db.execute(select(Product).where(Product.id.in_(product_ids), Product.businessId == ctx.business.id)))
        .scalars()
        .all()
    }
    # Per-customer price overrides for this client (in each product's pricing unit).
    custom_price = {
        cp.productId: cp.priceMinor
        for cp in (
            await db.execute(
                select(CustomerPrice).where(
                    CustomerPrice.businessId == ctx.business.id,
                    CustomerPrice.clientId == ctx.client_id,
                    CustomerPrice.productId.in_(product_ids),
                )
            )
        )
        .scalars()
        .all()
    }
    lines: list[LineInput] = []
    snapshots = []
    fields: dict[str, str] = {}
    for idx, item in enumerate(req.items):
        product = products.get(item.productId)
        if not product:
            fields[f"items.{idx}.productId"] = "Product not found"
            continue
        if product.archivedAt is not None or not product.isAvailable:
            fields[f"items.{idx}.productId"] = "Product is unavailable"
            continue
        qty_milli = parse_quantity_to_milli(item.quantity)
        if not qty_milli or qty_milli <= 0:
            fields[f"items.{idx}.quantity"] = "Enter a quantity greater than 0"
            continue
        # Order unit: default to the product's pricing unit; if given, it must be allowed.
        allowed_units = product.orderUnits or [product.unit]
        order_unit = (item.unit or product.unit).strip()
        if order_unit not in allowed_units and order_unit != product.unit:
            fields[f"items.{idx}.unit"] = "Unit not available for this product"
            continue
        unit_price = custom_price.get(product.id, product.priceMinor)
        lines.append(LineInput(quantity_milli=qty_milli, unit_price_minor=unit_price))
        snapshots.append((product, qty_milli, unit_price, order_unit))
    if fields:
        raise ValidationError("Please fix the highlighted items.", fields=fields)

    try:
        totals = compute_totals(lines, discount_minor=0, tax_bps=ctx.business.defaultTaxBps, delivery_charge_minor=0)
    except MoneyError as exc:
        raise ValidationError(str(exc), fields={exc.field: str(exc)} if exc.field else None)

    number = await _allocate_order_number(db, ctx.business.id)
    status = OrderStatus.CONFIRMED  # customer orders are live immediately
    order = Order(
        businessId=ctx.business.id,
        number=number,
        clientId=ctx.client_id,
        status=status,
        orderDate=_today_in_tz(ctx.business.timezone),
        fulfillmentAt=_to_naive_utc(req.fulfillmentAt),
        fulfillmentType=fulfillment_type,
        deliveryAddress=delivery_address,
        notes=(req.notes.strip() if req.notes else None),
        subtotalMinor=totals.subtotal_minor,
        discountMinor=totals.discount_minor,
        taxBps=ctx.business.defaultTaxBps,
        taxMinor=totals.tax_minor,
        deliveryChargeMinor=totals.delivery_charge_minor,
        totalMinor=totals.total_minor,
        paidMinor=0,
        paymentStatus=PaymentStatus(derive_payment_status(totals.total_minor, 0, status.value)),
        createdById=None,
        placedByCustomerId=ctx.customer_id,
    )
    db.add(order)
    await db.flush()  # assign order.id

    for position, ((product, qty_milli, unit_price, order_unit), line_total_minor) in enumerate(zip(snapshots, totals.line_totals)):
        db.add(
            OrderItem(
                businessId=ctx.business.id,
                orderId=order.id,
                productId=product.id,
                description=product.name,
                unit=product.unit,  # pricing/billing unit
                quantityMilli=qty_milli,  # provisional; variable-measure is re-set at capture
                unitPriceMinor=unit_price,
                lineTotalMinor=line_total_minor,
                isCustom=False,
                position=position,
                orderUnit=order_unit,
                orderedQtyMilli=qty_milli,
            )
        )
    db.add(
        OrderStatusHistory(
            businessId=ctx.business.id,
            orderId=order.id,
            fromStatus=None,
            toStatus=status,
            source="customer",
            changedByCustomerId=ctx.customer_id,
        )
    )
    await _audit(
        db,
        business_id=ctx.business.id,
        actor_user_id=None,
        actor_email=ctx.email,
        action="order.created",
        entity_type="Order",
        entity_id=order.id,
        metadata={"number": order.number, "totalMinor": order.totalMinor, "status": status.value, "via": "customer"},
    )
    await db.commit()

    detail = await _load_order_detail(db, order, ctx.business.currency)
    summary = order_summary(order, currency=ctx.business.currency, client_name=ctx.name)
    await broadcaster.publish(business_channel(ctx.business.id), order_event(ORDER_CREATED, summary))
    return detail


async def change_order_status(
    db: AsyncSession, ctx: AdminContext, order_id: str, req: UpdateStatusRequest
) -> dict:
    if ctx.business.suspended:
        raise ConflictError("This workspace is read-only.")
    next_status = OrderStatus(req.status)
    # Row-lock the order within the tenant.
    locked = await db.execute(
        text('SELECT "id" FROM "Order" WHERE "id" = :id AND "businessId" = :b FOR UPDATE'),
        {"id": order_id, "b": ctx.business.id},
    )
    if locked.first() is None:
        raise NotFoundError("Order")
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()

    if order.status == next_status:
        return await _load_order_detail(db, order, ctx.business.currency)
    if next_status not in STATUS_TRANSITIONS[order.status]:
        raise ValidationError(f"Cannot change an order from {order.status.value} to {next_status.value}.")

    previous = order.status
    order.status = next_status
    order.paymentStatus = PaymentStatus(derive_payment_status(order.totalMinor, order.paidMinor, next_status.value))
    order.updatedAt = now_utc()
    db.add(
        OrderStatusHistory(
            businessId=ctx.business.id,
            orderId=order.id,
            fromStatus=previous,
            toStatus=next_status,
            reason=(req.reason.strip() if req.reason else None),
            changedByUserId=ctx.user_id,
            source="admin",
        )
    )
    await _audit(
        db,
        business_id=ctx.business.id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.email,
        action="order.cancelled" if next_status == OrderStatus.CANCELLED else "order.status_changed",
        entity_type="Order",
        entity_id=order.id,
        metadata={"number": order.number, "from": previous.value, "to": next_status.value, **({"reason": req.reason} if req.reason else {})},
    )
    await db.commit()

    detail = await _load_order_detail(db, order, ctx.business.currency)
    summary = detail  # detail includes everything a listener needs
    event = order_event(ORDER_STATUS_CHANGED, summary)
    await broadcaster.publish(business_channel(ctx.business.id), event)
    if order.placedByCustomerId:
        await broadcaster.publish(customer_channel(order.placedByCustomerId), event)
    return detail
