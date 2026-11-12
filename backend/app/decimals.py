"""Strict Decimal parsing. Money never touches a float anywhere in this project."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator


def to_decimal(value: Any) -> Decimal:
    """Accept a decimal string, int or Decimal. Reject floats, bools and junk."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(
            f"expected a decimal string, got {type(value).__name__}; "
            "floats are not allowed for prices or quantities"
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal string: {value!r}") from exc
    else:
        raise ValueError(f"expected a decimal string, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"not a finite decimal: {value!r}")
    return result


def to_optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return to_decimal(value)


StrictDecimal = Annotated[Decimal, BeforeValidator(to_decimal)]
OptionalDecimal = Annotated[Decimal | None, BeforeValidator(to_optional_decimal)]
