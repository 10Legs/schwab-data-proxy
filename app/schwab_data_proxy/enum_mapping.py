"""
Enum mapping helpers — translate REST query-string params into the typed enums
that schwab-py (pinned 1.5.1) requires.

schwab-py enforces enum types on its client methods (``enforce_enums=True`` by
default). Passing raw strings/ints raises ``ValueError``/``AttributeError`` at
call time. These helpers centralize the param -> enum translation so the REST
handlers stay thin and the mapping is unit-testable against a mocked client.

Design notes (validated against schwab-py 1.5.1):
- ``client.Quote.Fields`` replaced the old ``client.Quote.FIELD_*`` members.
- ``PriceHistory.Period`` / ``PriceHistory.Frequency`` are int-valued enums; the
  proxy receives raw ints and must construct the enum *by value*.
- ``PriceHistory.PeriodType`` / ``FrequencyType`` are string-valued enums; the
  proxy receives the lowercase token (e.g. ``"daily"``) and constructs *by value*.
- ``get_option_chain`` takes ``strike_range`` (NOT ``range``) and the Options
  enums are string-valued (``ITM``, ``CALL`` ...). Callers may send either the
  member name (``IN_THE_MONEY``) or the wire value (``ITM``); both are accepted.
- ``MarketHours.Market`` is a string-valued enum (``equity`` ...).

Every mapper accepts the live (or mocked) ``client`` so it can read the enum
classes off it, and raises ``UnknownEnumValue`` on an unmappable input rather
than silently passing a raw string through (which would surface as an opaque
schwab-py error downstream).
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class UnknownEnumValue(ValueError):
    """Raised when an inbound param cannot be mapped to a schwab-py enum member."""


def _coerce(enum_cls: type[Enum], raw: Any, *, label: str) -> Enum:
    """Map ``raw`` onto ``enum_cls`` by member name or by wire value.

    Accepts (in order):
      1. an already-constructed member of ``enum_cls`` (pass-through)
      2. the member NAME, case-insensitive (e.g. ``"in_the_money"``)
      3. the member VALUE (e.g. ``"ITM"``, ``1``, ``"daily"``)
    """
    if isinstance(raw, enum_cls):
        return raw

    # by name (case-insensitive) — only meaningful for string-ish inputs
    if isinstance(raw, str):
        try:
            return enum_cls[raw.strip().upper()]
        except KeyError:
            pass

    # by value (handles int-valued Period/Frequency and string-valued enums)
    try:
        return enum_cls(raw)
    except ValueError:
        pass

    # last attempt: string value match case-insensitively
    if isinstance(raw, str):
        target = raw.strip().upper()
        for member in enum_cls:
            if str(member.value).upper() == target:
                return member

    valid = [m.name for m in enum_cls]
    raise UnknownEnumValue(
        f"{label}: cannot map {raw!r} to {enum_cls.__name__}; valid: {valid}"
    )


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


def map_quote_fields(client: Any, fields: str) -> list[Enum]:
    """``"quote,reference"`` -> ``[Quote.Fields.QUOTE, Quote.Fields.REFERENCE]``."""
    enum_cls = client.Quote.Fields
    out: list[Enum] = []
    for f in fields.split(","):
        f = f.strip()
        if not f:
            continue
        out.append(_coerce(enum_cls, f, label="quotes.fields"))
    return out


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


def map_period_type(client: Any, value: str) -> Enum:
    return _coerce(
        client.PriceHistory.PeriodType, value, label="pricehistory.period_type"
    )


def map_period(client: Any, value: int) -> Enum:
    return _coerce(client.PriceHistory.Period, value, label="pricehistory.period")


def map_frequency_type(client: Any, value: str) -> Enum:
    return _coerce(
        client.PriceHistory.FrequencyType, value, label="pricehistory.frequency_type"
    )


def map_frequency(client: Any, value: int) -> Enum:
    return _coerce(client.PriceHistory.Frequency, value, label="pricehistory.frequency")


# ---------------------------------------------------------------------------
# Option chains
# ---------------------------------------------------------------------------


def map_contract_type(client: Any, value: str) -> Enum:
    return _coerce(client.Options.ContractType, value, label="chains.contract_type")


def map_strategy(client: Any, value: str) -> Enum:
    return _coerce(client.Options.Strategy, value, label="chains.strategy")


def map_strike_range(client: Any, value: str) -> Enum:
    return _coerce(client.Options.StrikeRange, value, label="chains.range")


def map_expiration_month(client: Any, value: str) -> Enum:
    return _coerce(client.Options.ExpirationMonth, value, label="chains.exp_month")


def map_option_type(client: Any, value: str) -> Enum:
    return _coerce(client.Options.Type, value, label="chains.option_type")


def map_entitlement(client: Any, value: str) -> Enum:
    return _coerce(client.Options.Entitlement, value, label="chains.entitlement")


# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------


def map_market(client: Any, value: str) -> Enum:
    return _coerce(client.MarketHours.Market, value, label="markets.markets")
