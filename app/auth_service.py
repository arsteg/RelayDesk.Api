"""Login/session creation for admins (reusing the web `Session` table) and
customers (`CustomerSession`)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import now_utc
from .errors import AuthError, ForbiddenError
from .models import Business, CustomerAccount, CustomerSession, Membership, Session, User
from .security import burn_password_check, generate_token, hash_token, verify_password


async def _workspaces(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(Membership, Business).join(Business, Business.id == Membership.businessId).where(Membership.userId == user_id)
        )
    ).all()
    return [
        {"businessId": b.id, "name": b.name, "role": m.role.value, "suspended": b.suspendedAt is not None}
        for (m, b) in rows
    ]


async def admin_login(db: AsyncSession, email: str, password: str, business_id: str | None, *, ip=None, ua=None) -> dict:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        burn_password_check(password)
        raise AuthError("Incorrect email or password.")
    if not verify_password(password, user.passwordHash):
        raise AuthError("Incorrect email or password.")

    workspaces = await _workspaces(db, user.id)
    if not workspaces:
        raise ForbiddenError("This account has no workspace. Ask an owner for an invitation.")
    chosen = next((w for w in workspaces if w["businessId"] == business_id), None) if business_id else None
    if business_id and not chosen:
        raise ForbiddenError("You are not a member of that workspace.")
    active = (chosen or workspaces[0])["businessId"]

    token = generate_token()
    settings = get_settings()
    db.add(
        Session(
            tokenHash=hash_token(token),
            userId=user.id,
            activeBusinessId=active,
            expiresAt=now_utc() + timedelta(days=settings.admin_session_ttl_days),
            ipAddress=ip,
            userAgent=(ua or "")[:255] or None,
        )
    )
    await db.commit()
    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "activeBusinessId": active,
        "workspaces": workspaces,
    }


async def admin_logout(db: AsyncSession, token: str | None) -> None:
    if not token:
        return
    session = (await db.execute(select(Session).where(Session.tokenHash == hash_token(token)))).scalar_one_or_none()
    if session:
        await db.delete(session)
        await db.commit()


async def platform_login(db: AsyncSession, email: str, password: str, *, ip=None, ua=None) -> dict:
    """Authenticate a RelayDesk operator. Requires `User.isPlatformAdmin`;
    issues a `Session` with no active workspace (platform access is global)."""
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        burn_password_check(password)
        raise AuthError("Incorrect email or password.")
    if not verify_password(password, user.passwordHash):
        raise AuthError("Incorrect email or password.")
    if not user.isPlatformAdmin:
        raise ForbiddenError("Platform administrator access required.")
    token = generate_token()
    settings = get_settings()
    db.add(
        Session(
            tokenHash=hash_token(token),
            userId=user.id,
            activeBusinessId=None,
            expiresAt=now_utc() + timedelta(days=settings.admin_session_ttl_days),
            ipAddress=ip,
            userAgent=(ua or "")[:255] or None,
        )
    )
    await db.commit()
    return {"token": token, "admin": {"id": user.id, "email": user.email, "name": user.name}}


async def platform_logout(db: AsyncSession, token: str | None) -> None:
    await admin_logout(db, token)


async def customer_login(db: AsyncSession, business_id: str, email: str, password: str, *, ip=None, ua=None) -> dict:
    account = (
        await db.execute(
            select(CustomerAccount).where(CustomerAccount.businessId == business_id, CustomerAccount.email == email)
        )
    ).scalar_one_or_none()
    if not account or account.archivedAt is not None:
        burn_password_check(password)
        raise AuthError("Incorrect email or password.")
    if not verify_password(password, account.passwordHash):
        raise AuthError("Incorrect email or password.")
    return await _issue_customer_session(db, account, ip=ip, ua=ua)


async def _issue_customer_session(db: AsyncSession, account: CustomerAccount, *, ip=None, ua=None) -> dict:
    token = generate_token()
    settings = get_settings()
    db.add(
        CustomerSession(
            tokenHash=hash_token(token),
            customerId=account.id,
            expiresAt=now_utc() + timedelta(days=settings.customer_session_ttl_days),
            ipAddress=ip,
            userAgent=(ua or "")[:255] or None,
        )
    )
    await db.commit()
    business = (await db.execute(select(Business).where(Business.id == account.businessId))).scalar_one()
    return {
        "token": token,
        "customer": {
            "id": account.id,
            "email": account.email,
            "name": account.name,
            "phone": account.phone,
            "businessId": account.businessId,
            "clientId": account.clientId,
        },
        "business": {"id": business.id, "name": business.name, "currency": business.currency},
    }


async def customer_logout(db: AsyncSession, token: str | None) -> None:
    if not token:
        return
    session = (
        await db.execute(select(CustomerSession).where(CustomerSession.tokenHash == hash_token(token)))
    ).scalar_one_or_none()
    if session:
        await db.delete(session)
        await db.commit()
