"""Exact conversion between USDC amounts (Decimal) and base units (int)."""

from __future__ import annotations

from decimal import Decimal

USDC_DECIMALS = 6
_SCALE = Decimal(10) ** USDC_DECIMALS


def usdc_to_units(amount: Decimal) -> int:
    """`Decimal("0.001")` -> `1000`. Refuses floats and sub-unit precision."""
    if not isinstance(amount, Decimal):
        raise TypeError(f"amount must be a Decimal, got {type(amount).__name__}")
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"amount must be a finite, non-negative Decimal: {amount}")
    scaled = amount * _SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than {USDC_DECIMALS} decimal places")
    return int(scaled)


def units_to_usdc(units: int) -> Decimal:
    if isinstance(units, bool) or not isinstance(units, int):
        raise TypeError(f"units must be an int, got {type(units).__name__}")
    return Decimal(units) / _SCALE
