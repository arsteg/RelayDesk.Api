"""Read-side queries for orders and menu, with pagination and polling support."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .deps import AdminContext, CustomerContext
from .errors import NotFoundError
from .models import Category, Client, CustomerPrice, Order, OrderItem, OrderStatus, Product
from .serializers import menu_item, order_summary
from .services import _load_order_detail

OPEN_STATUSES = [OrderStatus.DRAFT, OrderStatus.CONFIRMED, OrderStatus.IN_PROGRESS, OrderStatus.READY]


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _clamp_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    return max(page or 1, 1), min(max(page_size or 25, 1), 100)


async def admin_list_orders(
    db: AsyncSession,
    ctx: AdminContext,
    *,
    status: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    updated_since: str | None = None,
) -> dict:
    page, page_size = _clamp_page(page, page_size)
    conditions = [Order.businessId == ctx.business.id]
    if status == "OPEN":
        conditions.append(Order.status.in_(OPEN_STATUSES))
    elif status and status != "ALL":
        conditions.append(Order.status == OrderStatus(status))
    since = _parse_since(updated_since)
    if since is not None:
        conditions.append(Order.updatedAt > since)

    total = (await db.execute(select(func.count()).select_from(Order).where(*conditions))).scalar_one()
    orders = list(
        (
            await db.execute(
                select(Order)
                .where(*conditions)
                .order_by(Order.createdAt.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    rows = await _summaries(db, ctx.business.currency, orders)
    return {"rows": rows, "total": total, "page": page, "pageSize": page_size}


async def _summaries(db: AsyncSession, currency: str, orders: list[Order]) -> list[dict]:
    if not orders:
        return []
    client_ids = {o.clientId for o in orders}
    order_ids = [o.id for o in orders]
    clients = {
        c.id: c.name
        for c in (await db.execute(select(Client).where(Client.id.in_(client_ids)))).scalars().all()
    }
    counts = dict(
        (
            await db.execute(
                select(OrderItem.orderId, func.count())
                .where(OrderItem.orderId.in_(order_ids))
                .group_by(OrderItem.orderId)
            )
        ).all()
    )
    return [
        order_summary(
            o,
            currency=currency,
            client_name=clients.get(o.clientId),
            item_count=int(counts.get(o.id, 0)),
        )
        for o in orders
    ]


async def admin_get_order(db: AsyncSession, ctx: AdminContext, order_id: str) -> dict:
    order = (
        await db.execute(select(Order).where(Order.id == order_id, Order.businessId == ctx.business.id))
    ).scalar_one_or_none()
    if not order:
        raise NotFoundError("Order")
    return await _load_order_detail(db, order, ctx.business.currency)


async def customer_menu(db: AsyncSession, ctx: CustomerContext) -> dict:
    categories = list(
        (
            await db.execute(
                select(Category)
                .where(Category.businessId == ctx.business.id, Category.archivedAt.is_(None))
                .order_by(Category.name)
            )
        )
        .scalars()
        .all()
    )
    products = list(
        (
            await db.execute(
                select(Product)
                .where(
                    Product.businessId == ctx.business.id,
                    Product.archivedAt.is_(None),
                    Product.isAvailable.is_(True),
                )
                .order_by(Product.name)
            )
        )
        .scalars()
        .all()
    )
    custom_price = {
        cp.productId: cp.priceMinor
        for cp in (
            await db.execute(
                select(CustomerPrice).where(CustomerPrice.businessId == ctx.business.id, CustomerPrice.clientId == ctx.client_id)
            )
        )
        .scalars()
        .all()
    }
    return {
        "business": {"id": ctx.business.id, "name": ctx.business.name, "currency": ctx.business.currency},
        "categories": [{"id": c.id, "name": c.name} for c in categories],
        "items": [menu_item(p, custom_price.get(p.id)) for p in products],
    }


async def customer_list_orders(
    db: AsyncSession, ctx: CustomerContext, *, page: int | None = None, page_size: int | None = None, updated_since: str | None = None
) -> dict:
    page, page_size = _clamp_page(page, page_size)
    conditions = [Order.businessId == ctx.business.id, Order.placedByCustomerId == ctx.customer_id]
    since = _parse_since(updated_since)
    if since is not None:
        conditions.append(Order.updatedAt > since)
    total = (await db.execute(select(func.count()).select_from(Order).where(*conditions))).scalar_one()
    orders = list(
        (
            await db.execute(
                select(Order)
                .where(*conditions)
                .order_by(Order.createdAt.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    rows = await _summaries(db, ctx.business.currency, orders)
    return {"rows": rows, "total": total, "page": page, "pageSize": page_size}


async def customer_get_order(db: AsyncSession, ctx: CustomerContext, order_id: str) -> dict:
    order = (
        await db.execute(
            select(Order).where(
                Order.id == order_id,
                Order.businessId == ctx.business.id,
                Order.placedByCustomerId == ctx.customer_id,
            )
        )
    ).scalar_one_or_none()
    if not order:
        raise NotFoundError("Order")
    return await _load_order_detail(db, order, ctx.business.currency)
