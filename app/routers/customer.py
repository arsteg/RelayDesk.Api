"""Customer endpoints: auth, menu, placing/viewing orders, and the real-time
status stream for the signed-in customer's own orders."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth_service, payments_service, queries
from ..db import get_db, get_sessionmaker
from ..deps import CustomerContext, customer_from_token, get_customer
from ..realtime import customer_channel
from ..schemas import CustomerLoginRequest, PlaceOrderRequest, VerifyPaymentRequest
from ..services import place_customer_order
from ..streaming import sse_response, websocket_stream

router = APIRouter(prefix="/customer", tags=["customer"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


@router.post("/auth/login")
async def login(body: CustomerLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip, ua = _client_meta(request)
    return await auth_service.customer_login(db, body.businessId, body.email, body.password, ip=ip, ua=ua)


@router.post("/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    from ..deps import _bearer_or_cookie

    await auth_service.customer_logout(db, _bearer_or_cookie(request, None))
    return {"ok": True}


@router.get("/me")
async def me(ctx: CustomerContext = Depends(get_customer)):
    return {
        "customer": {"id": ctx.customer_id, "email": ctx.email, "name": ctx.name, "clientId": ctx.client_id},
        "business": {
            "id": ctx.business.id,
            "name": ctx.business.name,
            "currency": ctx.business.currency,
            "timezone": ctx.business.timezone,
        },
    }


@router.get("/menu")
async def menu(ctx: CustomerContext = Depends(get_customer), db: AsyncSession = Depends(get_db)):
    return await queries.customer_menu(db, ctx)


@router.post("/orders", status_code=201)
async def place_order(
    body: PlaceOrderRequest, ctx: CustomerContext = Depends(get_customer), db: AsyncSession = Depends(get_db)
):
    return await place_customer_order(db, ctx, body)


@router.get("/orders")
async def list_orders(
    page: int | None = None,
    pageSize: int | None = None,
    updatedSince: str | None = None,
    ctx: CustomerContext = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    return await queries.customer_list_orders(db, ctx, page=page, page_size=pageSize, updated_since=updatedSince)


@router.get("/orders/{order_id}")
async def get_order(order_id: str, ctx: CustomerContext = Depends(get_customer), db: AsyncSession = Depends(get_db)):
    return await queries.customer_get_order(db, ctx, order_id)


@router.post("/orders/{order_id}/pay")
async def start_payment(order_id: str, ctx: CustomerContext = Depends(get_customer), db: AsyncSession = Depends(get_db)):
    """Create a Razorpay order for the outstanding balance (web/mobile Checkout)."""
    return await payments_service.start_order_payment(db, ctx, order_id)


@router.post("/orders/{order_id}/pay/verify")
async def verify_payment(
    order_id: str,
    body: VerifyPaymentRequest,
    ctx: CustomerContext = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    """Verify the Checkout handshake and record the payment against the order."""
    return await payments_service.confirm_order_payment(db, ctx, order_id, body)


@router.get("/realtime/stream")
async def realtime_stream(request: Request, token: str | None = None):
    from ..deps import _bearer_or_cookie

    resolved = token or _bearer_or_cookie(request, None)
    # Authenticate on a short-lived session; don't hold a connection while streaming.
    async with get_sessionmaker()() as db:
        ctx = await customer_from_token(db, resolved)
    return await sse_response(request, customer_channel(ctx.customer_id))


@router.websocket("/realtime/ws")
async def realtime_ws(websocket: WebSocket, token: str | None = None):
    sessionmaker = get_sessionmaker()
    resolved = token
    if not resolved:
        auth = websocket.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            resolved = auth[7:].strip()
    async with sessionmaker() as db:
        try:
            ctx = await customer_from_token(db, resolved)
        except Exception:
            await websocket.close(code=4401)
            return
    await websocket_stream(websocket, customer_channel(ctx.customer_id))
