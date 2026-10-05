"""Authentication dependencies for admin (staff) and customer requests.

Admin auth reuses the web app's `Session` table and `bl_session` cookie, and
also accepts `Authorization: Bearer <token>` (same opaque token). Customer auth
uses the `CustomerSession` table with bearer tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db, now_utc
from .errors import AuthError, ForbiddenError
from .models import Business, CustomerAccount, CustomerSession, Membership, Role, Session, User
from .security import hash_token


def _bearer_or_cookie(request: Request, cookie_name: str | None) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if cookie_name:
        return request.cookies.get(cookie_name)
    return None


@dataclass
class BusinessInfo:
    id: str
    name: str
    currency: str
    timezone: str
    defaultTaxBps: int
    invoicePrefix: str
    suspended: bool


@dataclass
class AdminContext:
    user_id: str
    email: str
    role: Role
    business: BusinessInfo
    session_id: str

    @property
    def business_id(self) -> str:
        return self.business.id


@dataclass
class CustomerContext:
    customer_id: str
    email: str
    name: str
    client_id: str
    business: BusinessInfo


@dataclass
class PlatformContext:
    """A RelayDesk operator (platform admin). Independent of any workspace:
    identified solely by `User.isPlatformAdmin`, never by a business membership."""

    user_id: str
    email: str
    name: str
    session_id: str


async def get_platform_admin(request: Request, db: AsyncSession = Depends(get_db)) -> PlatformContext:
    settings = get_settings()
    token = _bearer_or_cookie(request, settings.admin_cookie_name)
    return await platform_from_token(db, token)


async def platform_from_token(db: AsyncSession, token: str | None) -> PlatformContext:
    if not token:
        raise AuthError("Sign in to continue.")
    session = (
        await db.execute(select(Session).where(Session.tokenHash == hash_token(token)))
    ).scalar_one_or_none()
    if not session or session.expiresAt < now_utc():
        raise AuthError("Your session has expired. Sign in again.")
    user = (await db.execute(select(User).where(User.id == session.userId))).scalar_one_or_none()
    if not user:
        raise AuthError("Account not found.")
    if not user.isPlatformAdmin:
        raise ForbiddenError("Platform administrator access required.")
    return PlatformContext(user_id=user.id, email=user.email, name=user.name, session_id=session.id)


async def get_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AdminContext:
    settings = get_settings()
    token = _bearer_or_cookie(request, settings.admin_cookie_name)
    return await admin_from_token(db, token)


async def admin_from_token(db: AsyncSession, token: str | None) -> AdminContext:
    if not token:
        raise AuthError("Sign in to continue.")
    session = (
        await db.execute(select(Session).where(Session.tokenHash == hash_token(token)))
    ).scalar_one_or_none()
    if not session or session.expiresAt < now_utc():
        raise AuthError("Your session has expired. Sign in again.")
    if not session.activeBusinessId:
        raise ForbiddenError("No active workspace selected.")
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.userId == session.userId,
                Membership.businessId == session.activeBusinessId,
            )
        )
    ).scalar_one_or_none()
    if not membership:
        raise ForbiddenError("You are not a member of this workspace.")
    business = (await db.execute(select(Business).where(Business.id == session.activeBusinessId))).scalar_one_or_none()
    user = (await db.execute(select(User).where(User.id == session.userId))).scalar_one_or_none()
    if not business or not user:
        raise AuthError("Account not found.")
    return AdminContext(
        user_id=user.id,
        email=user.email,
        role=membership.role,
        session_id=session.id,
        business=BusinessInfo(
            id=business.id,
            name=business.name,
            currency=business.currency,
            timezone=business.timezone,
            defaultTaxBps=business.defaultTaxBps,
            invoicePrefix=business.invoicePrefix,
            suspended=business.suspendedAt is not None,
        ),
    )


def require_roles(*roles: Role):
    """Dependency factory: admin context restricted to the given business roles."""

    async def _dep(ctx: AdminContext = Depends(get_admin)) -> AdminContext:
        if ctx.role not in roles:
            raise ForbiddenError("You do not have permission to do that.")
        return ctx

    return _dep


async def get_customer(request: Request, db: AsyncSession = Depends(get_db)) -> CustomerContext:
    token = _bearer_or_cookie(request, None)
    return await customer_from_token(db, token)


async def customer_from_token(db: AsyncSession, token: str | None) -> CustomerContext:
    if not token:
        raise AuthError("Sign in to continue.")
    session = (
        await db.execute(select(CustomerSession).where(CustomerSession.tokenHash == hash_token(token)))
    ).scalar_one_or_none()
    if not session or session.expiresAt < now_utc():
        raise AuthError("Your session has expired. Sign in again.")
    account = (
        await db.execute(select(CustomerAccount).where(CustomerAccount.id == session.customerId))
    ).scalar_one_or_none()
    if not account or account.archivedAt is not None:
        raise AuthError("Account not found.")
    business = (await db.execute(select(Business).where(Business.id == account.businessId))).scalar_one_or_none()
    if not business:
        raise AuthError("Workspace not found.")
    return CustomerContext(
        customer_id=account.id,
        email=account.email,
        name=account.name,
        client_id=account.clientId,
        business=BusinessInfo(
            id=business.id,
            name=business.name,
            currency=business.currency,
            timezone=business.timezone,
            defaultTaxBps=business.defaultTaxBps,
            invoicePrefix=business.invoicePrefix,
            suspended=business.suspendedAt is not None,
        ),
    )
