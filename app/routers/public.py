"""Public (unauthenticated) endpoints: health + customer invitation onboarding."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import invitations
from ..db import get_db
from ..schemas import AcceptInvitationRequest

router = APIRouter(tags=["public"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/invitations/{token}")
async def inspect_invitation(token: str, db: AsyncSession = Depends(get_db)):
    return await invitations.inspect_invitation(db, token)


@router.post("/invitations/{token}/accept")
async def accept_invitation(token: str, body: AcceptInvitationRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return await invitations.accept_invitation(db, token, body, ip=ip, ua=ua)
