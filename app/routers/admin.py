"""Admin (business staff) endpoints: auth, incoming orders, status updates,
customer invitations, and the real-time order stream."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth_service, invitations, queries
from ..db import get_db, get_sessionmaker
from ..deps import AdminContext, Role, admin_from_token, get_admin, require_roles
from ..realtime import business_channel
from ..schemas import AdminLoginRequest, InviteCustomerRequest, UpdateStatusRequest
from ..services import change_order_status
from ..streaming import sse_response, websocket_stream

router = APIRouter(prefix="/admin", tags=["admin"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


@router.post("/auth/login")
async def login(body: AdminLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip, ua = _client_meta(request)
    return await auth_service.admin_login(db, body.email, body.password, body.businessId, ip=ip, ua=ua)


@router.post("/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    from ..config import get_settings
    from ..deps import _bearer_or_cookie

    token = _bearer_or_cookie(request, get_settings().admin_cookie_name)
    await auth_service.admin_logout(db, token)
    return {"ok": True}


@router.get("/me")
async def me(ctx: AdminContext = Depends(get_admin)):
    return {
        "user": {"id": ctx.user_id, "email": ctx.email},
        "role": ctx.role.value,
        "business": {
            "id": ctx.business.id,
            "name": ctx.business.name,
            "currency": ctx.business.currency,
            "timezone": ctx.business.timezone,
        },
    }


@router.get("/orders")
async def list_orders(
    status: str | None = None,
    page: int | None = None,
    pageSize: int | None = None,
    updatedSince: str | None = None,
    ctx: AdminContext = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
):
    return await queries.admin_list_orders(
        db, ctx, status=status, page=page, page_size=pageSize, updated_since=updatedSince
    )


@router.get("/orders/{order_id}")
async def get_order(order_id: str, ctx: AdminContext = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    return await queries.admin_get_order(db, ctx, order_id)


@router.post("/orders/{order_id}/status")
async def update_status(
    order_id: str,
    body: UpdateStatusRequest,
    ctx: AdminContext = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
):
    return await change_order_status(db, ctx, order_id, body)


@router.post("/customers/invite")
async def invite_customer(
    body: InviteCustomerRequest,
    ctx: AdminContext = Depends(require_roles(Role.OWNER, Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await invitations.invite_customer(
        db, ctx, email=body.email, name=body.name, phone=body.phone, client_id=body.clientId
    )


@router.get("/customers")
async def list_customers(
    ctx: AdminContext = Depends(require_roles(Role.OWNER, Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    return await invitations.list_invitations(db, ctx)


@router.post("/customers/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    invitation_id: str,
    ctx: AdminContext = Depends(require_roles(Role.OWNER, Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    await invitations.revoke_invitation(db, ctx, invitation_id)
    return {"ok": True}


@router.get("/realtime/stream")
async def realtime_stream(request: Request, token: str | None = None):
    # EventSource cannot send Authorization headers; accept a cookie or ?token=.
    # Authenticate with a short-lived session and release it BEFORE streaming so
    # a long-lived SSE connection never holds a database connection.
    from ..config import get_settings
    from ..deps import _bearer_or_cookie

    resolved = token or _bearer_or_cookie(request, get_settings().admin_cookie_name)
    async with get_sessionmaker()() as db:
        ctx = await admin_from_token(db, resolved)
    return await sse_response(request, business_channel(ctx.business.id))


@router.websocket("/realtime/ws")
async def realtime_ws(websocket: WebSocket, token: str | None = None):
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        resolved = token or _ws_token(websocket)
        try:
            ctx = await admin_from_token(db, resolved)
        except Exception:
            await websocket.close(code=4401)
            return
    await websocket_stream(websocket, business_channel(ctx.business.id))


def _ws_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.cookies.get("bl_session") or websocket.cookies.get("__Host-bl_session")
