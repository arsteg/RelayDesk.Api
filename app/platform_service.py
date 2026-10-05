"""Platform-operator (SaaS admin) logic: tenant businesses, subscriptions,
pricing plans, operator management and the platform audit log.

Ports the behaviour of the web app's ``src/server/platform.ts`` and the billing
simulator in ``src/server/billing.ts`` onto the shared Postgres schema. Every
mutation writes an ``AuditLog`` row with ``scope="platform"``. Cross-tenant by
design (operators see metadata/usage only — never tenant business records, and
there is no impersonation).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import now_utc
from .deps import PlatformContext
from .errors import NotFoundError, ValidationError
from .ids import cuid
from .models import (
    AuditLog,
    Business,
    Client,
    Membership,
    Order,
    Plan,
    PricingPlan,
    Role,
    Session,
    Subscription,
    SubscriptionStatus,
    User,
)

PAST_DUE_GRACE_DAYS = 7
TRIAL_WARN_DAYS = 5
_DAY = timedelta(days=1)
_SUB_STATUSES = {"TRIALING", "ACTIVE", "PAST_DUE", "CANCELED"}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _month_start_utc(now: datetime, tz: str) -> datetime:
    """Start of the current calendar month in the business timezone, as naive UTC."""
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = timezone.utc
    local = now.replace(tzinfo=timezone.utc).astimezone(zone)
    local_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc).replace(tzinfo=None)


async def _audit(
    db: AsyncSession,
    actor: PlatformContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    business_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            id=cuid(),
            businessId=business_id,
            actorUserId=actor.user_id,
            actorEmail=actor.email,
            scope="platform",
            action=action,
            entityType=entity_type,
            entityId=entity_id,
            metadata_=metadata,
        )
    )


def compute_access(sub: Subscription | None, business: Business, now: datetime) -> dict:
    """Access policy mirrored from src/lib/plans.ts (suspension + subscription
    state → full | readonly | suspended). Billing-enabled toggling is not
    consulted here: the operator view always reflects the real subscription."""
    if business.suspendedAt is not None:
        reason = "This workspace has been suspended by RelayDesk support."
        if business.suspendedReason:
            reason = f"Suspended: {business.suspendedReason}"
        return {"level": "suspended", "reason": reason, "warning": None}
    if sub is None:
        return {"level": "readonly", "reason": "No subscription found.", "warning": None}
    status = sub.status
    if status == SubscriptionStatus.TRIALING:
        if sub.trialEndsAt and sub.trialEndsAt > now:
            days = ceil((sub.trialEndsAt - now) / _DAY)
            warning = f"Free trial ends in {days} day{'' if days == 1 else 's'}." if days <= TRIAL_WARN_DAYS else None
            return {"level": "full", "reason": None, "warning": warning}
        return {"level": "readonly", "reason": "The free trial has ended.", "warning": None}
    if status == SubscriptionStatus.ACTIVE:
        return {"level": "full", "reason": None, "warning": None}
    if status == SubscriptionStatus.PAST_DUE:
        since = sub.pastDueSince or now
        grace_end = since + PAST_DUE_GRACE_DAYS * _DAY
        if grace_end > now:
            return {"level": "full", "reason": None, "warning": "A subscription payment failed; in grace period."}
        return {"level": "readonly", "reason": "Subscription payment is overdue.", "warning": None}
    if status == SubscriptionStatus.CANCELED:
        if sub.currentPeriodEnd and sub.currentPeriodEnd > now:
            return {"level": "full", "reason": None, "warning": "Subscription is cancelled and ends soon."}
        return {"level": "readonly", "reason": "Subscription has ended.", "warning": None}
    return {"level": "full", "reason": None, "warning": None}


def _sub_dict(sub: Subscription | None) -> dict | None:
    if not sub:
        return None
    return {
        "plan": sub.plan.value,
        "status": sub.status.value,
        "trialEndsAt": _iso(sub.trialEndsAt),
        "currentPeriodEnd": _iso(sub.currentPeriodEnd),
        "cancelAtPeriodEnd": sub.cancelAtPeriodEnd,
        "pastDueSince": _iso(sub.pastDueSince),
        "canceledAt": _iso(sub.canceledAt),
        "stripeCustomerId": sub.stripeCustomerId,
        "stripeSubscriptionId": sub.stripeSubscriptionId,
    }


async def _owner_of(db: AsyncSession, business_id: str) -> dict | None:
    row = (
        await db.execute(
            select(User)
            .join(Membership, Membership.userId == User.id)
            .where(Membership.businessId == business_id, Membership.role == Role.OWNER)
            .limit(1)
        )
    ).scalar_one_or_none()
    return {"id": row.id, "name": row.name, "email": row.email} if row else None


async def _subscription_of(db: AsyncSession, business_id: str) -> Subscription | None:
    return (
        await db.execute(select(Subscription).where(Subscription.businessId == business_id))
    ).scalar_one_or_none()


async def _usage_of(db: AsyncSession, business: Business, now: datetime) -> dict:
    members = (
        await db.execute(select(func.count()).select_from(Membership).where(Membership.businessId == business.id))
    ).scalar_one()
    clients = (
        await db.execute(select(func.count()).select_from(Client).where(Client.businessId == business.id))
    ).scalar_one()
    month_start = _month_start_utc(now, business.timezone)
    monthly_orders = (
        await db.execute(
            select(func.count()).select_from(Order).where(Order.businessId == business.id, Order.createdAt >= month_start)
        )
    ).scalar_one()
    total_orders = (
        await db.execute(select(func.count()).select_from(Order).where(Order.businessId == business.id))
    ).scalar_one()
    return {"members": members, "clients": clients, "monthlyOrders": monthly_orders, "totalOrders": total_orders}


async def _business_summary(db: AsyncSession, b: Business, now: datetime) -> dict:
    sub = await _subscription_of(db, b.id)
    return {
        "id": b.id,
        "name": b.name,
        "email": b.email,
        "createdAt": _iso(b.createdAt),
        "suspendedAt": _iso(b.suspendedAt),
        "suspendedReason": b.suspendedReason,
        "owner": await _owner_of(db, b.id),
        "subscription": _sub_dict(sub),
        "access": compute_access(sub, b, now),
        "usage": await _usage_of(db, b, now),
    }


async def list_businesses(db: AsyncSession, *, q: str | None, status: str | None, page: int, page_size: int) -> dict:
    page = max(1, page)
    size = min(100, max(1, page_size))
    stmt = select(Business)
    if q and q.strip():
        like = f"%{q.strip()}%"
        owner_ids = (
            select(Membership.businessId)
            .join(User, User.id == Membership.userId)
            .where(Membership.role == Role.OWNER, User.email.ilike(like))
        )
        stmt = stmt.where(or_(Business.name.ilike(like), Business.id.in_(owner_ids)))
    if status == "suspended":
        stmt = stmt.where(Business.suspendedAt.is_not(None))
    elif status in _SUB_STATUSES:
        sub_ids = select(Subscription.businessId).where(Subscription.status == SubscriptionStatus(status))
        stmt = stmt.where(Business.id.in_(sub_ids))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    businesses = (
        await db.execute(stmt.order_by(Business.createdAt.desc()).offset((page - 1) * size).limit(size))
    ).scalars().all()
    now = now_utc()
    rows = [await _business_summary(db, b, now) for b in businesses]
    return {"rows": rows, "total": total, "page": page, "pageSize": size}


async def get_business(db: AsyncSession, business_id: str) -> dict:
    b = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if not b:
        raise NotFoundError("Business")
    now = now_utc()
    members = (
        await db.execute(
            select(User, Membership.role)
            .join(Membership, Membership.userId == User.id)
            .where(Membership.businessId == business_id)
        )
    ).all()
    audit = (
        await db.execute(
            select(AuditLog).where(AuditLog.businessId == business_id).order_by(AuditLog.id.desc()).limit(20)
        )
    ).scalars().all()
    summary = await _business_summary(db, b, now)
    summary["members"] = [{"id": u.id, "name": u.name, "email": u.email, "role": role.value} for (u, role) in members]
    summary["audit"] = [
        {"action": a.action, "actorEmail": a.actorEmail, "entityType": a.entityType, "metadata": a.metadata_}
        for a in audit
    ]
    return summary


async def stats(db: AsyncSession) -> dict:
    businesses = (await db.execute(select(func.count()).select_from(Business))).scalar_one()
    users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    suspended = (
        await db.execute(select(func.count()).select_from(Business).where(Business.suspendedAt.is_not(None)))
    ).scalar_one()
    by_status_rows = (
        await db.execute(select(Subscription.status, func.count()).group_by(Subscription.status))
    ).all()
    by_plan_rows = (
        await db.execute(select(Subscription.plan, func.count()).group_by(Subscription.plan))
    ).all()
    by_status = {s.value: n for (s, n) in by_status_rows}
    by_plan = {p.value: n for (p, n) in by_plan_rows}
    recent = (
        await db.execute(select(Business).order_by(Business.createdAt.desc()).limit(5))
    ).scalars().all()
    return {
        "businesses": businesses,
        "users": users,
        "suspended": suspended,
        "byStatus": by_status,
        "byPlan": by_plan,
        "recentSignups": [{"id": b.id, "name": b.name, "createdAt": _iso(b.createdAt)} for b in recent],
    }


async def set_business_suspended(db: AsyncSession, actor: PlatformContext, business_id: str, suspend: bool, reason: str | None) -> dict:
    b = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if not b:
        raise NotFoundError("Business")
    if suspend:
        clean = (reason or "").strip()
        if len(clean) < 3:
            raise ValidationError("Give a reason (at least 3 characters).", fields={"reason": "Required"})
        b.suspendedAt = now_utc()
        b.suspendedReason = clean[:500]
    else:
        b.suspendedAt = None
        b.suspendedReason = None
    await _audit(
        db,
        actor,
        action="business.suspended" if suspend else "business.reactivated",
        entity_type="Business",
        entity_id=business_id,
        business_id=business_id,
        metadata={"name": b.name, **({"reason": b.suspendedReason} if suspend else {})},
    )
    await db.commit()
    return await get_business(db, business_id)


async def update_subscription(db: AsyncSession, actor: PlatformContext, business_id: str, action: str, plan: str | None, days: int | None) -> dict:
    b = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if not b:
        raise NotFoundError("Business")
    sub = await _subscription_of(db, business_id)
    if not sub:
        sub = Subscription(id=cuid(), businessId=business_id)
        db.add(sub)
    now = now_utc()
    if action == "set_plan":
        if plan is None:
            raise ValidationError("plan is required for set_plan.", fields={"plan": "Required"})
        sub.plan = Plan(plan)
    elif action == "start_trial":
        sub.status = SubscriptionStatus.TRIALING
        sub.trialEndsAt = now + timedelta(days=days or 14)
        sub.canceledAt = None
        sub.pastDueSince = None
    elif action == "expire_trial":
        sub.status = SubscriptionStatus.TRIALING
        sub.trialEndsAt = now - _DAY
    elif action == "activate":
        sub.status = SubscriptionStatus.ACTIVE
        sub.pastDueSince = None
        sub.canceledAt = None
        sub.cancelAtPeriodEnd = False
    elif action == "past_due":
        sub.status = SubscriptionStatus.PAST_DUE
        sub.pastDueSince = now
    elif action == "cancel":
        sub.status = SubscriptionStatus.CANCELED
        sub.canceledAt = now
    elif action == "reactivate":
        sub.status = SubscriptionStatus.ACTIVE
        sub.canceledAt = None
        sub.pastDueSince = None
        sub.cancelAtPeriodEnd = False
    await _audit(
        db,
        actor,
        action="subscription.changed",
        entity_type="Subscription",
        entity_id=business_id,
        business_id=business_id,
        metadata={"action": action, **({"plan": plan} if plan else {}), **({"days": days} if days else {})},
    )
    await db.commit()
    return await get_business(db, business_id)


async def list_plans(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(PricingPlan).order_by(PricingPlan.sortOrder))).scalars().all()
    return [_plan_dict(p) for p in rows]


def _plan_dict(p: PricingPlan) -> dict:
    return {
        "code": p.code,
        "name": p.name,
        "description": p.description,
        "priceLabel": p.priceLabel,
        "maxMembers": p.maxMembers,
        "maxMonthlyOrders": p.maxMonthlyOrders,
        "stripePriceId": p.stripePriceId,
        "razorpayPlanId": p.razorpayPlanId,
        "isPublic": p.isPublic,
        "isActive": p.isActive,
        "sortOrder": p.sortOrder,
    }


async def update_plan(db: AsyncSession, actor: PlatformContext, code: str, fields: dict) -> dict:
    p = (await db.execute(select(PricingPlan).where(PricingPlan.code == code))).scalar_one_or_none()
    if not p:
        raise NotFoundError("Plan")
    for key, value in fields.items():
        setattr(p, key, value)
    await _audit(
        db,
        actor,
        action="plan.updated",
        entity_type="PricingPlan",
        entity_id=code,
        metadata={"code": code, "fields": sorted(fields.keys())},
    )
    await db.commit()
    await db.refresh(p)
    return _plan_dict(p)


async def list_admins(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(select(User).where(User.isPlatformAdmin.is_(True)).order_by(User.email))
    ).scalars().all()
    return [{"id": u.id, "name": u.name, "email": u.email} for u in rows]


async def set_admin(db: AsyncSession, actor: PlatformContext, email: str, grant: bool) -> dict:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        raise NotFoundError("User")
    user.isPlatformAdmin = grant
    if not grant:
        # Force immediate logout of the revoked operator (mirror the CLI).
        sessions = (await db.execute(select(Session).where(Session.userId == user.id))).scalars().all()
        for s in sessions:
            await db.delete(s)
    await _audit(
        db,
        actor,
        action="platform_admin.granted" if grant else "platform_admin.revoked",
        entity_type="User",
        entity_id=user.id,
        metadata={"email": email, "via": "console"},
    )
    await db.commit()
    return {"id": user.id, "name": user.name, "email": user.email, "isPlatformAdmin": user.isPlatformAdmin}


async def list_audit(db: AsyncSession, take: int = 100) -> list[dict]:
    take = min(500, max(1, take))
    rows = (
        await db.execute(
            select(AuditLog, Business.name)
            .outerjoin(Business, Business.id == AuditLog.businessId)
            .where(
                or_(
                    AuditLog.scope.in_(("platform", "system")),
                    AuditLog.action.like("subscription.%"),
                    AuditLog.action.like("plan.%"),
                )
            )
            .order_by(AuditLog.id.desc())
            .limit(take)
        )
    ).all()
    return [
        {
            "action": a.action,
            "scope": a.scope,
            "actorEmail": a.actorEmail,
            "businessName": name,
            "entityType": a.entityType,
            "metadata": a.metadata_,
        }
        for (a, name) in rows
    ]
