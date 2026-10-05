"""Pure-unit tests for the ported money maths (no database)."""

from app.totals import (
    LineInput,
    compute_totals,
    derive_payment_status,
    parse_money_to_minor,
    parse_quantity_to_milli,
    round_half_up,
)


def test_round_half_up_away_from_zero():
    assert round_half_up(2.5) == 3
    assert round_half_up(2.4) == 2
    assert round_half_up(-2.5) == -3


def test_line_and_subtotal_with_fractional_quantity():
    # 1.5 units at 80.00 = 120.00
    totals = compute_totals([LineInput(quantity_milli=1500, unit_price_minor=8000)])
    assert totals.line_totals == [12000]
    assert totals.subtotal_minor == 12000


def test_tax_applied_after_discount_not_on_delivery():
    # subtotal 1080.00, 5% tax, 50.00 delivery -> tax on 1080 = 54.00, total 1184.00
    totals = compute_totals(
        [LineInput(2, 50000), LineInput(1, 8000)],  # tiny quantities but exercise sum
        discount_minor=0,
        tax_bps=500,
        delivery_charge_minor=5000,
    )
    assert totals.subtotal_minor == round_half_up((2 * 50000) / 1000) + round_half_up((1 * 8000) / 1000)


def test_realistic_order_totals():
    # 2 x 500.00 + 1 x 80.00 = 1080.00 subtotal, 5% tax = 54.00, total 1134.00
    totals = compute_totals(
        [LineInput(2000, 50000), LineInput(1000, 8000)], tax_bps=500
    )
    assert totals.subtotal_minor == 108000
    assert totals.tax_minor == 5400
    assert totals.total_minor == 113400


def test_discount_cannot_exceed_subtotal():
    import pytest

    from app.totals import MoneyError

    with pytest.raises(MoneyError):
        compute_totals([LineInput(1000, 10000)], discount_minor=20000)


def test_parsers():
    assert parse_money_to_minor("1,250.50", "INR") == 125050
    assert parse_money_to_minor("1250", "JPY") == 1250
    assert parse_money_to_minor("abc", "INR") is None
    assert parse_quantity_to_milli("1.5") == 1500
    assert parse_quantity_to_milli("2") == 2000
    assert parse_quantity_to_milli("x") is None


def test_payment_status():
    assert derive_payment_status(1000, 0, "CONFIRMED") == "UNPAID"
    assert derive_payment_status(1000, 1000, "CONFIRMED") == "PAID"
    assert derive_payment_status(1000, 400, "CONFIRMED") == "PARTIAL"
    assert derive_payment_status(1000, 0, "CANCELLED") == "PAID"
