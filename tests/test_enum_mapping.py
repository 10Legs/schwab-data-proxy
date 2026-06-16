"""
Regression tests for the param -> schwab-py enum mapping.

These guard against the schwab-py API drift that broke the live proxy:
  - Quote.FIELD_* was renamed to Quote.Fields.*  (AttributeError at call time)
  - PriceHistory period/frequency now require typed enums, not raw ints
    ("expected type Period, got type int")
  - get_option_chain kwarg is ``strike_range``, not ``range``
  - Options.* / MarketHours.Market require typed enums

The tests run against the *real installed* schwab-py enums (pinned 1.5.1 in
requirements.txt). If schwab-py is upgraded and renames/retypes a member, these
tests fail loudly instead of the proxy 502-ing in production.
"""

from __future__ import annotations

import pytest
from schwab.client.base import BaseClient

from app.schwab_data_proxy import enum_mapping
from app.schwab_data_proxy.enum_mapping import UnknownEnumValue


class FakeClient:
    """Minimal stand-in exposing the same nested enum classes the real
    AsyncClient inherits from BaseClient. No network, no auth."""

    Quote = BaseClient.Quote
    PriceHistory = BaseClient.PriceHistory
    Options = BaseClient.Options
    MarketHours = BaseClient.MarketHours


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


# --- Quotes ---------------------------------------------------------------


def test_quote_fields_maps_to_Fields_enum(client):
    out = enum_mapping.map_quote_fields(client, "quote,reference,extended")
    assert out == [
        client.Quote.Fields.QUOTE,
        client.Quote.Fields.REFERENCE,
        client.Quote.Fields.EXTENDED,
    ]
    # regression: the old code referenced Quote.FIELD_QUOTE which no longer exists
    assert not hasattr(client.Quote, "FIELD_QUOTE")


def test_quote_fields_accepts_member_name_and_value(client):
    # value form ("quote") and name form ("QUOTE") both resolve
    assert enum_mapping.map_quote_fields(client, "QUOTE") == [client.Quote.Fields.QUOTE]


def test_quote_fields_unknown_raises(client):
    with pytest.raises(UnknownEnumValue):
        enum_mapping.map_quote_fields(client, "bogus")


# --- Price history --------------------------------------------------------


def test_period_int_maps_to_Period_enum(client):
    # regression: raw int 1 previously hit "expected type Period, got type int"
    result = enum_mapping.map_period(client, 1)
    assert result is client.PriceHistory.Period.ONE_DAY
    assert isinstance(result, client.PriceHistory.Period)


def test_frequency_int_maps_to_Frequency_enum(client):
    result = enum_mapping.map_frequency(client, 5)
    assert result is client.PriceHistory.Frequency.EVERY_FIVE_MINUTES
    assert isinstance(result, client.PriceHistory.Frequency)


def test_period_type_string_maps(client):
    assert (
        enum_mapping.map_period_type(client, "daily".upper() and "day")
        is client.PriceHistory.PeriodType.DAY
    )
    assert (
        enum_mapping.map_period_type(client, "DAY")
        is client.PriceHistory.PeriodType.DAY
    )


def test_frequency_type_string_maps(client):
    assert (
        enum_mapping.map_frequency_type(client, "daily")
        is client.PriceHistory.FrequencyType.DAILY
    )


def test_period_unknown_int_raises(client):
    with pytest.raises(UnknownEnumValue):
        enum_mapping.map_period(client, 999)


# --- Option chains --------------------------------------------------------


def test_contract_type_maps(client):
    assert (
        enum_mapping.map_contract_type(client, "call")
        is client.Options.ContractType.CALL
    )


def test_strike_range_accepts_name_and_wire_value(client):
    # member name
    assert (
        enum_mapping.map_strike_range(client, "IN_THE_MONEY")
        is client.Options.StrikeRange.IN_THE_MONEY
    )
    # wire value
    assert (
        enum_mapping.map_strike_range(client, "ITM")
        is client.Options.StrikeRange.IN_THE_MONEY
    )


def test_entitlement_maps(client):
    assert (
        enum_mapping.map_entitlement(client, "NP") is client.Options.Entitlement.NON_PRO
    )


def test_exp_month_and_option_type_map(client):
    assert (
        enum_mapping.map_expiration_month(client, "JAN")
        is client.Options.ExpirationMonth.JANUARY
    )
    assert enum_mapping.map_option_type(client, "S") is client.Options.Type.STANDARD


# --- Market hours ---------------------------------------------------------


def test_market_maps(client):
    assert enum_mapping.map_market(client, "equity") is client.MarketHours.Market.EQUITY
    assert enum_mapping.map_market(client, "OPTION") is client.MarketHours.Market.OPTION


def test_market_unknown_raises(client):
    with pytest.raises(UnknownEnumValue):
        enum_mapping.map_market(client, "crypto")
