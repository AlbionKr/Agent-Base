from decimal import Decimal

import pytest

from app.chain.units import units_to_usdc, usdc_to_units


@pytest.mark.parametrize(
    "amount,units",
    [("0", 0), ("0.000001", 1), ("0.001", 1_000), ("1", 1_000_000), ("24.80", 24_800_000)],
)
def test_round_trip(amount, units):
    assert usdc_to_units(Decimal(amount)) == units
    assert units_to_usdc(units) == Decimal(amount)


def test_floats_are_refused():
    with pytest.raises(TypeError):
        usdc_to_units(0.1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        units_to_usdc(1.0)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["0.0000001", "-1", "NaN", "Infinity"])
def test_invalid_amounts_are_refused(bad):
    with pytest.raises(ValueError):
        usdc_to_units(Decimal(bad))
