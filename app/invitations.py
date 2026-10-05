"""Customer invitation lifecycle: invite -> inspect -> accept (onboarding)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_service import _issue_customer_session
from .config import get_settings
from .db import now_utc
from .deps import AdminContext
from .errors import ConflictError, NotFoundError, ValidationError
from .models import AuditLog, Client, CustomerAccount, CustomerInvitation
from .schemas import AcceptInvitationRequest
from .security import generate_token, hash_password, hash_token


def invite_url(token: str) -> str:
    base = get_settings().app_url.rstrip("/")
    return f"{base}/order/accept/{token}"


async def invite_customer(db: AsyncSession, ctx: AdminContext, *, email: str, name=None, phone=None, client_id=None) -> dict:
    if ctx.business.suspended:
        raise ConflictError("This workspace is read-only.")
    # Already has an account?
    existing = (
        await db.execute(
            select(CustomerAccount).where(CustomerAccount.businessId == ctx.business.id, CustomerAccount.email == email)
        )
    ).scalar_one_or_none()
    if existing:
        raise ValidationError("This customer already has an account.", fields={"email": "Already registered"})
    if client_id:
        client = (
            await db.execute(select(Client).where(Client.id == client_id, Client.businessId == ctx.business.id))
        ).scalar_one_or_none()
        if not client:
            raise ValidationError("Linked client not found.", fields={"clientId": "Not found"})

    # Re-inviting replaces any earlier pending invitation for this email.
    pending = (
        await db.execute(
            select(CustomerInvitation).where(
                CustomerInvitation.businessId == ctx.business.id,
                CustomerInvitation.email == email,
                CustomerInvitation.acceptedAt.is_(None),
                CustomerInvitation.revokedAt.is_(None),
            )
        )
    ).scalars().all()
    for inv in pending:
        inv.revokedAt = now_utc()

    token = generate_token()
    settings = get_settings()
    invitation = CustomerInvitation(
        businessId=ctx.business.id,
        email=email,
        name=name,
        phone=phone,
        clientId=client_id,
        tokenHash=hash_token(token),
        invitedById=ctx.user_id,
        expiresAt=now_utc() + timedelta(days=settings.invite_ttl_days),
    )
    db.add(invitation)
    db.add(
        AuditLog(
            businessId=ctx.business.id,
            actorUserId=ctx.user_id,
            actorEmail=ctx.email,
            scope="business",
            action="customer_invitation.created",
            entityType="CustomerInvitation",
            entityId=None,
            metadata_={"email": email},
        )
    )
    await db.flush()
    await db.commit()
    url = invite_url(token)
    return {
        "invitation": _invitation_public(invitation, ctx.business.name),
        "token": token,
        "inviteUrl": url,
    }


def _invitation_public(inv: CustomerInvitation, business_name: str) -> dict:
    return {
        "id": inv.id,
        "email": inv.email,
        "name": inv.name,
        "businessName": business_name,
        "expiresAt": inv.expiresAt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{inv.expiresAt.microsecond // 1000:03d}Z",
    }


async def _find_usable(db: AsyncSession, token: str) -> CustomerInvitation | None:
    inv = (
        await db.execute(select(CustomerInvitation).where(CustomerInvitation.tokenHash == hash_token(token)))
    ).scalar_one_or_none()
    if not inv or inv.acceptedAt or inv.revokedAt or inv.expiresAt < now_utc():
        return None
    return inv


async def inspect_invitation(db: AsyncSession, token: str) -> dict:
    inv = await _find_usable(db, token)
    if not inv:
        raise NotFoundError("Invitation")
    from .models import Business

    business = (await db.execute(select(Business).where(Business.id == inv.businessId))).scalar_one_or_none()
    return _invitation_public(inv, business.name if business else "")


async def accept_invitation(db: AsyncSession, token: str, req: AcceptInvitationRequest, *, ip=None, ua=None) -> dict:
    inv = await _find_usable(db, token)
    if not inv:
        raise ValidationError("This invitation is invalid or has expired.")
    # Guard against a race / replay.
    existing = (
        await db.execute(
            select(CustomerAccount).where(CustomerAccount.businessId == inv.businessId, CustomerAccount.email == inv.email)
        )
    ).scalar_one_or_none()
    if existing:
        raise ConflictError("An account already exists for this email. Sign in instead.")

    # Mark accepted conditionally (single-use).
    inv.acceptedAt = now_utc()

    # Link to the pre-selected client or create one for this customer.
    client_id = inv.clientId
    if not client_id:
        client = Client(businessId=inv.businessId, name=req.name.strip(), email=inv.email, phone=req.phone)
        db.add(client)
        await db.flush()
        client_id = client.id

    account = CustomerAccount(
        businessId=inv.businessId,
        clientId=client_id,
        email=inv.email,
        name=req.name.strip(),
        phone=req.phone,
        passwordHash=hash_password(req.password),
        emailVerifiedAt=now_utc(),  # the invite link proves control of the mailbox
    )
    db.add(account)
    db.add(
        AuditLog(
            businessId=inv.businessId,
            actorUserId=None,
            actorEmail=inv.email,
            scope="business",
            action="customer_invitation.accepted",
            entityType="CustomerInvitation",
            entityId=inv.id,
            metadata_={"email": inv.email},
        )
    )
    await db.flush()
    return await _issue_customer_session(db, account, ip=ip, ua=ua)


async def list_invitations(db: AsyncSession, ctx: AdminContext) -> dict:
    pending = (
        await db.execute(
            select(CustomerInvitation)
            .where(
                CustomerInvitation.businessId == ctx.business.id,
                CustomerInvitation.acceptedAt.is_(None),
                CustomerInvitation.revokedAt.is_(None),
                CustomerInvitation.expiresAt > now_utc(),
            )
            .order_by(CustomerInvitation.createdAt.desc())
        )
    ).scalars().all()
    accounts = (
        await db.execute(
            select(CustomerAccount)
            .where(CustomerAccount.businessId == ctx.business.id, CustomerAccount.archivedAt.is_(None))
            .order_by(CustomerAccount.createdAt.desc())
        )
    ).scalars().all()
    return {
        "pending": [_invitation_public(i, ctx.business.name) for i in pending],
        "customers": [
            {"id": a.id, "email": a.email, "name": a.name, "phone": a.phone, "clientId": a.clientId}
            for a in accounts
        ],
    }


async def revoke_invitation(db: AsyncSession, ctx: AdminContext, invitation_id: str) -> None:
    inv = (
        await db.execute(
            select(CustomerInvitation).where(
                CustomerInvitation.id == invitation_id, CustomerInvitation.businessId == ctx.business.id
            )
        )
    ).scalar_one_or_none()
    if not inv:
        raise NotFoundError("Invitation")
    if inv.acceptedAt is None and inv.revokedAt is None:
        inv.revokedAt = now_utc()
    await db.commit()
