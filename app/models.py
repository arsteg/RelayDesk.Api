"""SQLAlchemy ORM mapped onto the Prisma-managed tables.

Only the columns this service reads or writes are mapped; unmapped columns keep
their database defaults on insert. Columns Prisma fills in the client rather
than the database (`id` via cuid, `updatedAt` via @updatedAt) are given
Python-side defaults here so inserts satisfy the NOT NULL constraints.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now_utc
from .ids import cuid


class OrderStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class FulfillmentType(str, enum.Enum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"


class PaymentStatus(str, enum.Enum):
    UNPAID = "UNPAID"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    OVERPAID = "OVERPAID"


class PaymentKind(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    RAZORPAY = "RAZORPAY"
    OTHER = "OTHER"


class Role(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    STAFF = "STAFF"


class Plan(str, enum.Enum):
    STARTER = "STARTER"
    PRO = "PRO"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"


def _pg_enum(py_enum, name: str) -> Enum:
    # Reference the existing Postgres enum type; never emit DDL for it.
    return Enum(
        py_enum,
        name=name,
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def _id_col() -> Mapped[str]:
    return mapped_column(String, primary_key=True, default=cuid)


class User(Base):
    __tablename__ = "User"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    passwordHash: Mapped[str] = mapped_column(String)
    emailVerifiedAt: Mapped[datetime | None] = mapped_column(DateTime)
    isPlatformAdmin: Mapped[bool] = mapped_column(Boolean, default=False)
    # `updatedAt` is NOT NULL with no DB default (Prisma @updatedAt).
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Session(Base):
    __tablename__ = "Session"
    id: Mapped[str] = _id_col()
    tokenHash: Mapped[str] = mapped_column(String)
    userId: Mapped[str] = mapped_column(String, ForeignKey("User.id"))
    activeBusinessId: Mapped[str | None] = mapped_column(String)
    expiresAt: Mapped[datetime] = mapped_column(DateTime)
    ipAddress: Mapped[str | None] = mapped_column(String)
    userAgent: Mapped[str | None] = mapped_column(String)


class Membership(Base):
    __tablename__ = "Membership"
    id: Mapped[str] = _id_col()
    userId: Mapped[str] = mapped_column(String)
    businessId: Mapped[str] = mapped_column(String)
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "Role"))
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Business(Base):
    __tablename__ = "Business"
    id: Mapped[str] = _id_col()
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String)
    defaultTaxBps: Mapped[int] = mapped_column(Integer)
    invoicePrefix: Mapped[str] = mapped_column(String)
    orderSequence: Mapped[int] = mapped_column(Integer, default=0)
    suspendedAt: Mapped[datetime | None] = mapped_column(DateTime)
    suspendedReason: Mapped[str | None] = mapped_column(String)
    razorpayKeyId: Mapped[str | None] = mapped_column(String)
    razorpayKeySecret: Mapped[str | None] = mapped_column(String)
    razorpayWebhookSecret: Mapped[str | None] = mapped_column(String)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Subscription(Base):
    __tablename__ = "Subscription"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    plan: Mapped[Plan] = mapped_column(_pg_enum(Plan, "Plan"), default=Plan.STARTER)
    status: Mapped[SubscriptionStatus] = mapped_column(_pg_enum(SubscriptionStatus, "SubscriptionStatus"), default=SubscriptionStatus.TRIALING)
    trialEndsAt: Mapped[datetime | None] = mapped_column(DateTime)
    currentPeriodEnd: Mapped[datetime | None] = mapped_column(DateTime)
    cancelAtPeriodEnd: Mapped[bool] = mapped_column(Boolean, default=False)
    pastDueSince: Mapped[datetime | None] = mapped_column(DateTime)
    canceledAt: Mapped[datetime | None] = mapped_column(DateTime)
    stripeCustomerId: Mapped[str | None] = mapped_column(String)
    stripeSubscriptionId: Mapped[str | None] = mapped_column(String)
    lastEventAt: Mapped[datetime | None] = mapped_column(DateTime)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class BillingEvent(Base):
    __tablename__ = "BillingEvent"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    businessId: Mapped[str | None] = mapped_column(String)
    processedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class PricingPlan(Base):
    __tablename__ = "PricingPlan"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    priceLabel: Mapped[str] = mapped_column(String)
    maxMembers: Mapped[int | None] = mapped_column(Integer)
    maxMonthlyOrders: Mapped[int | None] = mapped_column(Integer)
    stripePriceId: Mapped[str | None] = mapped_column(String)
    razorpayPlanId: Mapped[str | None] = mapped_column(String)
    isPublic: Mapped[bool] = mapped_column(Boolean, default=True)
    isActive: Mapped[bool] = mapped_column(Boolean, default=True)
    sortOrder: Mapped[int] = mapped_column(Integer, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Client(Base):
    __tablename__ = "Client"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    archivedAt: Mapped[datetime | None] = mapped_column(DateTime)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Category(Base):
    __tablename__ = "Category"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    archivedAt: Mapped[datetime | None] = mapped_column(DateTime)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Product(Base):
    __tablename__ = "Product"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    categoryId: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    priceMinor: Mapped[int] = mapped_column(Integer)
    isAvailable: Mapped[bool] = mapped_column(Boolean, default=True)
    isVariableMeasure: Mapped[bool] = mapped_column(Boolean, default=False)
    orderUnits: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    estConversionPerOrderUnit: Mapped[int | None] = mapped_column(Integer)
    archivedAt: Mapped[datetime | None] = mapped_column(DateTime)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class CustomerPrice(Base):
    __tablename__ = "CustomerPrice"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    clientId: Mapped[str] = mapped_column(String)
    productId: Mapped[str] = mapped_column(String)
    priceMinor: Mapped[int] = mapped_column(Integer)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Order(Base):
    __tablename__ = "Order"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    number: Mapped[int] = mapped_column(Integer)
    clientId: Mapped[str] = mapped_column(String)
    status: Mapped[OrderStatus] = mapped_column(_pg_enum(OrderStatus, "OrderStatus"))
    orderDate: Mapped[date] = mapped_column(Date)
    fulfillmentAt: Mapped[datetime] = mapped_column(DateTime)
    fulfillmentType: Mapped[FulfillmentType] = mapped_column(_pg_enum(FulfillmentType, "FulfillmentType"))
    deliveryAddress: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    subtotalMinor: Mapped[int] = mapped_column(Integer)
    discountMinor: Mapped[int] = mapped_column(Integer)
    taxBps: Mapped[int] = mapped_column(Integer)
    taxMinor: Mapped[int] = mapped_column(Integer)
    deliveryChargeMinor: Mapped[int] = mapped_column(Integer)
    totalMinor: Mapped[int] = mapped_column(Integer)
    paidMinor: Mapped[int] = mapped_column(Integer)
    paymentStatus: Mapped[PaymentStatus] = mapped_column(_pg_enum(PaymentStatus, "PaymentStatus"))
    createdById: Mapped[str | None] = mapped_column(String)
    placedByCustomerId: Mapped[str | None] = mapped_column(String)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class OrderItem(Base):
    __tablename__ = "OrderItem"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    orderId: Mapped[str] = mapped_column(String)
    productId: Mapped[str | None] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    unit: Mapped[str | None] = mapped_column(String)
    quantityMilli: Mapped[int] = mapped_column(Integer)
    unitPriceMinor: Mapped[int] = mapped_column(Integer)
    lineTotalMinor: Mapped[int] = mapped_column(Integer)
    isCustom: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    orderUnit: Mapped[str | None] = mapped_column(String)
    orderedQtyMilli: Mapped[int | None] = mapped_column(Integer)
    capturedQtyMilli: Mapped[int | None] = mapped_column(Integer)


class Payment(Base):
    __tablename__ = "Payment"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    orderId: Mapped[str] = mapped_column(String)
    kind: Mapped[PaymentKind] = mapped_column(_pg_enum(PaymentKind, "PaymentKind"))
    amountMinor: Mapped[int] = mapped_column(Integer)
    paidOn: Mapped[date] = mapped_column(Date)
    method: Mapped[PaymentMethod] = mapped_column(_pg_enum(PaymentMethod, "PaymentMethod"))
    reference: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    recordedById: Mapped[str | None] = mapped_column(String)
    voidedAt: Mapped[datetime | None] = mapped_column(DateTime)
    razorpayOrderId: Mapped[str | None] = mapped_column(String)
    razorpayPaymentId: Mapped[str | None] = mapped_column(String)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AuditLog(Base):
    __tablename__ = "AuditLog"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str | None] = mapped_column(String)
    actorUserId: Mapped[str | None] = mapped_column(String)
    actorEmail: Mapped[str | None] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String, default="business")
    action: Mapped[str] = mapped_column(String)
    entityType: Mapped[str] = mapped_column(String)
    entityId: Mapped[str | None] = mapped_column(String)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)


# --- customer-ordering tables (added by the customer_ordering migration) ---


class CustomerAccount(Base):
    __tablename__ = "CustomerAccount"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    clientId: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    passwordHash: Mapped[str] = mapped_column(String)
    emailVerifiedAt: Mapped[datetime | None] = mapped_column(DateTime)
    archivedAt: Mapped[datetime | None] = mapped_column(DateTime)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class CustomerSession(Base):
    __tablename__ = "CustomerSession"
    id: Mapped[str] = _id_col()
    tokenHash: Mapped[str] = mapped_column(String)
    customerId: Mapped[str] = mapped_column(String)
    expiresAt: Mapped[datetime] = mapped_column(DateTime)
    ipAddress: Mapped[str | None] = mapped_column(String)
    userAgent: Mapped[str | None] = mapped_column(String)


class CustomerInvitation(Base):
    __tablename__ = "CustomerInvitation"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    clientId: Mapped[str | None] = mapped_column(String)
    tokenHash: Mapped[str] = mapped_column(String)
    invitedById: Mapped[str | None] = mapped_column(String)
    expiresAt: Mapped[datetime] = mapped_column(DateTime)
    acceptedAt: Mapped[datetime | None] = mapped_column(DateTime)
    revokedAt: Mapped[datetime | None] = mapped_column(DateTime)
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class OrderStatusHistory(Base):
    __tablename__ = "OrderStatusHistory"
    id: Mapped[str] = _id_col()
    businessId: Mapped[str] = mapped_column(String)
    orderId: Mapped[str] = mapped_column(String)
    fromStatus: Mapped[OrderStatus | None] = mapped_column(_pg_enum(OrderStatus, "OrderStatus"))
    toStatus: Mapped[OrderStatus] = mapped_column(_pg_enum(OrderStatus, "OrderStatus"))
    reason: Mapped[str | None] = mapped_column(String)
    changedByUserId: Mapped[str | None] = mapped_column(String)
    changedByCustomerId: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="admin")
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


# Status transition rules — mirror src/server/orders.ts.
STATUS_TRANSITIONS: dict[OrderStatus, list[OrderStatus]] = {
    OrderStatus.DRAFT: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
    OrderStatus.CONFIRMED: [OrderStatus.IN_PROGRESS, OrderStatus.READY, OrderStatus.COMPLETED, OrderStatus.CANCELLED],
    OrderStatus.IN_PROGRESS: [OrderStatus.READY, OrderStatus.COMPLETED, OrderStatus.CANCELLED],
    OrderStatus.READY: [OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED, OrderStatus.CANCELLED],
    OrderStatus.COMPLETED: [],
    OrderStatus.CANCELLED: [],
}
