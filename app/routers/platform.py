"""Platform-operator (SaaS admin) endpoints: operator auth, tenant businesses,
subscription controls, pricing plans, operator management and the audit log.

All endpoints except login/logout require a platform admin (``User.isPlatformAdmin``)
via the ``get_platform_admin`` dependency. Reachable from RelayDesk.Admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth_service, platform_service
from ..db import get_db
from ..deps import PlatformContext, get_platform_admin
from ..schemas import (
    AdminGrantRequest,
    PlanUpdateRequest,
    PlatformLoginRequest,
    SubscriptionUpdateRequest,
    SuspendRequest,
)

router = APIRouter(prefix="/platform", tags=["platform"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


@router.post("/auth/login")
async def login(body: PlatformLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip, ua = _client_meta(request)
    return await auth_service.platform_login(db, body.email, body.password, ip=ip, ua=ua)


@router.post("/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    from ..config import get_settings
    from ..deps import _bearer_or_cookie

    token = _bearer_or_cookie(request, get_settings().admin_cookie_name)
    await auth_service.platform_logout(db, token)
    return {"ok": True}


@router.get("/me")
async def me(ctx: PlatformContext = Depends(get_platform_admin)):
    return {"id": ctx.user_id, "email": ctx.email, "name": ctx.name}


@router.get("/stats")
async def stats(ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.stats(db)


@router.get("/businesses")
async def list_businesses(
    q: str | None = None,
    status: str | None = None,
    page: int = 1,
    pageSize: int = 25,
    ctx: PlatformContext = Depends(get_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    return await platform_service.list_businesses(db, q=q, status=status, page=page, page_size=pageSize)


@router.get("/businesses/{business_id}")
async def get_business(business_id: str, ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.get_business(db, business_id)


@router.post("/businesses/{business_id}/suspend")
async def suspend_business(
    business_id: str,
    body: SuspendRequest,
    ctx: PlatformContext = Depends(get_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    return await platform_service.set_business_suspended(db, ctx, business_id, body.suspend, body.reason)


@router.post("/businesses/{business_id}/subscription")
async def update_subscription(
    business_id: str,
    body: SubscriptionUpdateRequest,
    ctx: PlatformContext = Depends(get_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    return await platform_service.update_subscription(db, ctx, business_id, body.action, body.plan, body.days)


@router.get("/plans")
async def list_plans(ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.list_plans(db)


@router.patch("/plans/{code}")
async def update_plan(
    code: str,
    body: PlanUpdateRequest,
    ctx: PlatformContext = Depends(get_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    return await platform_service.update_plan(db, ctx, code, fields)


@router.get("/admins")
async def list_admins(ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.list_admins(db)


@router.post("/admins")
async def set_admin(body: AdminGrantRequest, ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.set_admin(db, ctx, body.email, body.grant)


@router.get("/audit")
async def list_audit(take: int = 100, ctx: PlatformContext = Depends(get_platform_admin), db: AsyncSession = Depends(get_db)):
    return await platform_service.list_audit(db, take)
