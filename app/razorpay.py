"""Minimal Razorpay client + signature helpers (no SDK dependency).

Used for END-CUSTOMER order payments, which settle to each SUPPLIER's own
Razorpay account (keys stored per-business). The platform/SaaS-billing
integration uses the platform's own keys and lives in the web app.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx

from .errors import ApiError

_API = "https://api.razorpay.com/v1"


class PaymentConfigError(ApiError):
    status_code = 503
    code = "payment_unavailable"


async def create_order(key_id: str, key_secret: str, *, amount_minor: int, currency: str, receipt: str, notes: dict | None = None) -> dict:
    """Create a Razorpay Order for `amount_minor` (in the smallest currency unit)."""
    if amount_minor <= 0:
        raise PaymentConfigError("Nothing is due on this order.")
    payload = {"amount": amount_minor, "currency": currency, "receipt": receipt[:40], "notes": notes or {}}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{_API}/orders", json=payload, auth=(key_id, key_secret))
    if resp.status_code // 100 != 2:
        raise PaymentConfigError("Could not start the payment. Try again.")
    return resp.json()


async def fetch_payment(key_id: str, key_secret: str, payment_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{_API}/payments/{payment_id}", auth=(key_id, key_secret))
    if resp.status_code // 100 != 2:
        raise PaymentConfigError("Could not verify the payment.")
    return resp.json()


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_payment_signature(order_id: str, payment_id: str, signature: str, key_secret: str) -> bool:
    """Razorpay Checkout handshake signature: HMAC_SHA256("<order_id>|<payment_id>")."""
    expected = _hmac_hex(key_secret, f"{order_id}|{payment_id}".encode("utf-8"))
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(body: bytes, signature: str, webhook_secret: str) -> bool:
    """Razorpay webhook signature: HMAC_SHA256(raw request body)."""
    expected = _hmac_hex(webhook_secret, body)
    return hmac.compare_digest(expected, signature or "")
