"""Request/response contracts (Pydantic v2).

Mirrors packages/shared/*.ts so web and mobile share one shape. Business maths
is never done here — see app/totals.py.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    v = (value or "").strip().lower()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address")
    return v


class AdminLoginRequest(BaseModel):
    email: str
    password: str
    businessId: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalize_email(v)


class CustomerLoginRequest(BaseModel):
    email: str
    password: str
    businessId: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalize_email(v)


class InviteCustomerRequest(BaseModel):
    email: str
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    clientId: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalize_email(v)


class AcceptInvitationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Use at most 72 bytes (bcrypt limit)")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Required")
        return v


class OrderItemInput(BaseModel):
    productId: str = Field(min_length=1)
    quantity: str = Field(description="Decimal quantity, e.g. '2' or '1.5'")
    # Optional order unit (must be one the product allows); defaults to the pricing unit.
    unit: str | None = Field(default=None, max_length=30)

    @field_validator("quantity", mode="before")
    @classmethod
    def _coerce(cls, v):
        return str(v)


class PlaceOrderRequest(BaseModel):
    items: list[OrderItemInput] = Field(min_length=1, max_length=200)
    fulfillmentType: str = Field(default="PICKUP")
    fulfillmentAt: datetime
    deliveryAddress: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("fulfillmentType")
    @classmethod
    def _ftype(cls, v: str) -> str:
        if v not in ("PICKUP", "DELIVERY"):
            raise ValueError("fulfillmentType must be PICKUP or DELIVERY")
        return v


class UpdateStatusRequest(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        allowed = {"DRAFT", "CONFIRMED", "IN_PROGRESS", "READY", "COMPLETED", "CANCELLED"}
        if v not in allowed:
            raise ValueError("Invalid status")
        return v


class VerifyPaymentRequest(BaseModel):
    """Razorpay Checkout handshake returned to the client on success."""

    razorpayOrderId: str = Field(min_length=1)
    razorpayPaymentId: str = Field(min_length=1)
    razorpaySignature: str = Field(min_length=1)


# --- platform (operator console) request bodies ---


class PlatformLoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalize_email(v)


class SuspendRequest(BaseModel):
    suspend: bool
    reason: str | None = Field(default=None, max_length=500)


_SUBSCRIPTION_ACTIONS = {
    "set_plan",      # set tier (plan required)
    "start_trial",   # start/extend a trial (days optional, default 14)
    "expire_trial",  # end the trial now
    "activate",      # mark ACTIVE
    "past_due",      # mark PAST_DUE (grace starts now)
    "cancel",        # mark CANCELED
    "reactivate",    # CANCELED/PAST_DUE -> ACTIVE
}


class SubscriptionUpdateRequest(BaseModel):
    action: str
    plan: str | None = None
    days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v not in _SUBSCRIPTION_ACTIONS:
            raise ValueError(f"action must be one of {sorted(_SUBSCRIPTION_ACTIONS)}")
        return v

    @field_validator("plan")
    @classmethod
    def _plan(cls, v: str | None) -> str | None:
        if v is not None and v not in ("STARTER", "PRO"):
            raise ValueError("plan must be STARTER or PRO")
        return v


class PlanUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    priceLabel: str | None = Field(default=None, max_length=80)
    maxMembers: int | None = Field(default=None, ge=1)
    maxMonthlyOrders: int | None = Field(default=None, ge=1)
    stripePriceId: str | None = Field(default=None, max_length=255)
    razorpayPlanId: str | None = Field(default=None, max_length=255)
    isPublic: bool | None = None
    isActive: bool | None = None
    sortOrder: int | None = Field(default=None, ge=0)


class AdminGrantRequest(BaseModel):
    email: str
    grant: bool

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalize_email(v)


# --- response models (documented contract; routers return plain dicts) ---


class Page(BaseModel):
    rows: list
    total: int
    page: int
    pageSize: int
