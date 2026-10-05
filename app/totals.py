"""Order money maths — the authoritative copy lives on the backend.

Ported from src/lib/totals.ts and src/lib/money.ts so web and mobile never
compute money themselves. All amounts are integer minor units (paise/cents);
quantities are integer thousandths ("milli") so 1.5 kg is exact.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


class MoneyError(ValueError):
    """Raised for invalid money/quantity input or impossible totals."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


# INTEGER column ceiling shared with the web app (src/lib/limits.ts).
MAX_MINOR = 2_000_000_000
MAX_QUANTITY_MILLI = 100_000_000
MAX_ITEMS_PER_ORDER = 200


def round_half_up(n: float) -> int:
    """Integer rounding, half away from zero — matches the JS implementation."""
    if n < 0:
        return -math.floor(-n + 0.5)
    return math.floor(n + 0.5)


def line_total(quantity_milli: int, unit_price_minor: int) -> int:
    return round_half_up((quantity_milli * unit_price_minor) / 1000)


@dataclass
class LineInput:
    quantity_milli: int
    unit_price_minor: int


@dataclass
class Totals:
    line_totals: list[int]
    subtotal_minor: int
    discount_minor: int
    tax_minor: int
    delivery_charge_minor: int
    total_minor: int


def compute_totals(
    lines: list[LineInput],
    *,
    discount_minor: int = 0,
    tax_bps: int = 0,
    delivery_charge_minor: int = 0,
) -> Totals:
    """subtotal = sum(line totals); tax = round((subtotal - discount) * bps/10000);
    total = subtotal - discount + tax + delivery. Tax is after discount, not on delivery."""
    line_totals = [line_total(l.quantity_milli, l.unit_price_minor) for l in lines]
    subtotal_minor = sum(line_totals)
    if discount_minor < 0:
        raise MoneyError("Discount cannot be negative", "discount")
    if discount_minor > subtotal_minor:
        raise MoneyError("Discount cannot exceed the subtotal", "discount")
    if tax_bps < 0 or tax_bps > 10000:
        raise MoneyError("Tax rate must be between 0% and 100%", "taxRate")
    if delivery_charge_minor < 0:
        raise MoneyError("Delivery charge cannot be negative", "deliveryCharge")
    taxable = subtotal_minor - discount_minor
    tax_minor = round_half_up((taxable * tax_bps) / 10000)
    total_minor = taxable + tax_minor + delivery_charge_minor
    if (
        subtotal_minor > MAX_MINOR
        or total_minor > MAX_MINOR
        or any(l > MAX_MINOR for l in line_totals)
    ):
        raise MoneyError("This order's total is larger than RelayDesk supports.")
    return Totals(
        line_totals=line_totals,
        subtotal_minor=subtotal_minor,
        discount_minor=discount_minor,
        tax_minor=tax_minor,
        delivery_charge_minor=delivery_charge_minor,
        total_minor=total_minor,
    )


def amount_due(total_minor: int, status: str) -> int:
    return 0 if status == "CANCELLED" else total_minor


def derive_payment_status(total_minor: int, paid_minor: int, status: str) -> str:
    due = amount_due(total_minor, status)
    if paid_minor > due:
        return "OVERPAID"
    if paid_minor <= 0:
        return "PAID" if due == 0 else "UNPAID"
    if paid_minor < due:
        return "PARTIAL"
    return "PAID"


# --- parsing (minor-unit digits assumed 2 for INR/USD; JPY-style 0 handled) ---

_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF"}


def minor_digits(currency: str) -> int:
    return 0 if currency.upper() in _ZERO_DECIMAL else 2


def parse_money_to_minor(value: str | int | float, currency: str) -> int | None:
    if value is None:
        return None
    cleaned = re.sub(r"[,\s₹$€£]", "", str(value)).strip()
    if cleaned == "":
        return None
    digits = minor_digits(currency)
    pattern = r"^\d+$" if digits == 0 else rf"^\d+(\.\d{{1,{digits}}})?$|^\.\d{{1,{digits}}}$"
    if not re.match(pattern, cleaned):
        return None
    whole, _, frac = cleaned.partition(".")
    whole = whole or "0"
    frac = (frac + "0" * digits)[:digits] if digits else ""
    minor = int(whole) * (10**digits) + (int(frac) if frac else 0)
    return minor


def parse_quantity_to_milli(value: str | int | float) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not re.match(r"^\d+(\.\d{1,3})?$", s):
        return None
    whole, _, frac = s.partition(".")
    milli = int(whole) * 1000 + int((frac + "000")[:3] or "0")
    return milli
