"""End-customer order payments via Razorpay.

Money settles to the SUPPLIER's own Razorpay account (keys stored on the
business), never to the platform. On a verified payment we record a `Payment`
row (method RAZORPAY) and re-sync the order's paid amount / payment status,
exactly like an office-recorded cash/cheque collection — so online and manual
payments reconcile identically. Recording is idempotent on the Razorpay
payment id (unique column).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import razorpay
from .db import now_utc
from .deps import CustomerContext
from .errors import ConflictError, NotFoundError, ValidationError
from .ids import cuid
from .models import Business, Order, Payment, PaymentKind, PaymentMethod, PaymentStatus
from .razorpay import PaymentConfigError
from .totals import derive_payment_status


async def _order_for_customer(db: AsyncSession, ctx: CustomerContext, order_id: str) -> Order:
    order = (
        await db.execute(
            select(Order).where(Order.id == order_id, Order.businessId == ctx.business.id, Order.clientId == ctx.client_id)
        )
    ).scalar_one_or_none()
    if not order:
        raise NotFoundError("Order")
    return order


def _balance_minor(order: Order) -> int:
    due = 0 if order.status.value == "CANCELLED" else order.totalMinor
    return due - order.paidMinor


async def _require_business_keys(db: AsyncSession, business_id: str) -> Business:
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    if not business.razorpayKeyId or not business.razorpayKeySecret:
        raise PaymentConfigError("Online payments are not enabled for this business.")
    return business


async def start_order_payment(db: AsyncSession, ctx: CustomerContext, order_id: str) -> dict:
    """Create a Razorpay Order for the outstanding balance and return the
    fields the Checkout widget needs."""
    order = await _order_for_customer(db, ctx, order_id)
    balance = _balance_minor(order)
    if balance <= 0:
        raise ValidationError("This order is already paid.")
    business = await _require_business_keys(db, ctx.business.id)
    rzp = await razorpay.create_order(
        business.razorpayKeyId,
        business.razorpayKeySecret,
        amount_minor=balance,
        currency=ctx.business.currency,
        receipt=f"order_{order.id}",
        notes={"businessId": ctx.business.id, "orderId": order.id},
    )
    return {
        "razorpayOrderId": rzp["id"],
        "keyId": business.razorpayKeyId,
        "amountMinor": balance,
        "currency": ctx.business.currency,
        "orderNumber": order.number,
        "name": ctx.business.name,
    }


async def confirm_order_payment(db: AsyncSession, ctx: CustomerContext, order_id: str, verify) -> dict:
    """Verify the Checkout handshake signature, then record the payment."""
    order = await _order_for_customer(db, ctx, order_id)
    business = await _require_business_keys(db, ctx.business.id)
    if not razorpay.verify_payment_signature(verify.razorpayOrderId, verify.razorpayPaymentId, verify.razorpaySignature, business.razorpayKeySecret):
        raise ValidationError("Payment could not be verified.")
    # Trust the gateway for the captured amount, not the client.
    payment = await razorpay.fetch_payment(business.razorpayKeyId, business.razorpayKeySecret, verify.razorpayPaymentId)
    if payment.get("status") not in ("captured", "authorized"):
        raise ValidationError("Payment was not completed.")
    amount_minor = int(payment.get("amount") or 0)
    return await _record_and_resync(db, ctx.business.id, order, amount_minor, verify.razorpayOrderId, verify.razorpayPaymentId)


async def _record_and_resync(db: AsyncSession, business_id: str, order: Order, amount_minor: int, rzp_order_id: str, rzp_payment_id: str) -> dict:
    if amount_minor <= 0:
        raise ValidationError("Invalid payment amount.")
    db.add(
        Payment(
            id=cuid(),
            businessId=business_id,
            orderId=order.id,
            kind=PaymentKind.PAYMENT,
            amountMinor=amount_minor,
            paidOn=now_utc().date(),
            method=PaymentMethod.RAZORPAY,
            reference=rzp_payment_id,
            razorpayOrderId=rzp_order_id,
            razorpayPaymentId=rzp_payment_id,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # Unique razorpayPaymentId -> already recorded (e.g. webhook raced the client).
        await db.rollback()
        raise ConflictError("This payment was already recorded.")
    await _resync_order(db, business_id, order.id)
    await db.commit()
    return {"ok": True, "orderId": order.id, "amountMinor": amount_minor}


async def _resync_order(db: AsyncSession, business_id: str, order_id: str) -> None:
    payments = (
        await db.execute(select(Payment).where(Payment.businessId == business_id, Payment.orderId == order_id))
    ).scalars().all()
    paid = sum((-p.amountMinor if p.kind == PaymentKind.REFUND else p.amountMinor) for p in payments if p.voidedAt is None)
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one()
    order.paidMinor = paid
    order.paymentStatus = PaymentStatus(derive_payment_status(order.totalMinor, paid, order.status.value))
