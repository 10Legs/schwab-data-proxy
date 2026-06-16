"""
Handler-level regression tests: assert the REST endpoints build their schwab-py
client calls with the correct enum *types* and kwarg *names*, end-to-end, with a
mocked client (no network, no auth).

This is the test that would have caught the live 502s:
  - get_quotes called with Quote.Fields members (not FIELD_* attrs)
  - get_price_history called with Period/Frequency enums (not raw ints)
  - get_option_chain called with strike_range= (not range=)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from schwab.client.base import BaseClient

from app.schwab_data_proxy import rest_proxy


def _ok_response(payload=None):
    return SimpleNamespace(
        status_code=200,
        json=lambda: payload if payload is not None else {"ok": True},
        text="",
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    # nested enum classes resolve from the real schwab-py (pinned) API
    client.Quote = BaseClient.Quote
    client.PriceHistory = BaseClient.PriceHistory
    client.Options = BaseClient.Options
    client.MarketHours = BaseClient.MarketHours
    client.get_quotes.return_value = _ok_response()
    client.get_price_history.return_value = _ok_response()
    client.get_option_chain.return_value = _ok_response()
    client.get_market_hours.return_value = _ok_response()

    monkeypatch.setattr(rest_proxy.session, "client", lambda: client)
    # bypass the TTL cache between tests
    rest_proxy._cache.clear()
    return client


@pytest.mark.asyncio
async def test_quotes_passes_Fields_enums(mock_client):
    resp = await rest_proxy.get_quotes(request=None, symbols="AAPL", fields="quote")
    assert resp.status_code == 200
    args, kwargs = mock_client.get_quotes.call_args
    assert args[0] == ["AAPL"]
    assert kwargs["fields"] == [mock_client.Quote.Fields.QUOTE]


@pytest.mark.asyncio
async def test_pricehistory_passes_typed_enums(mock_client):
    resp = await rest_proxy.get_pricehistory(
        symbol="AAPL",
        period_type="year",
        period=1,
        frequency_type="daily",
        frequency=1,
        start_datetime=None,
        end_datetime=None,
        need_extended_hours_data=None,
        need_previous_close=None,
    )
    assert resp.status_code == 200
    _, kwargs = mock_client.get_price_history.call_args
    assert kwargs["period_type"] is mock_client.PriceHistory.PeriodType.YEAR
    assert kwargs["period"] is mock_client.PriceHistory.Period.ONE_DAY
    assert kwargs["frequency_type"] is mock_client.PriceHistory.FrequencyType.DAILY
    assert kwargs["frequency"] is mock_client.PriceHistory.Frequency.EVERY_MINUTE
    # all enum types, never raw ints
    assert isinstance(kwargs["period"], mock_client.PriceHistory.Period)
    assert isinstance(kwargs["frequency"], mock_client.PriceHistory.Frequency)


@pytest.mark.asyncio
async def test_chains_uses_strike_range_kwarg(mock_client):
    # Calling the handler directly (not via FastAPI) means unset Query params keep
    # their Query(None) default objects rather than resolving to None, so pass
    # every optional explicitly as None.
    resp = await rest_proxy.get_chains(
        symbol="AAPL",
        contract_type="call",
        strike_count=None,
        include_underlying_quote=None,
        strategy=None,
        interval=None,
        strike=None,
        range="ITM",
        from_date=None,
        to_date=None,
        volatility=None,
        underlying_price=None,
        interest_rate=None,
        days_to_expiration=None,
        exp_month=None,
        option_type=None,
        entitlement=None,
    )
    assert resp.status_code == 200
    _, kwargs = mock_client.get_option_chain.call_args
    # regression: kwarg must be strike_range, not range
    assert "range" not in kwargs
    assert kwargs["strike_range"] is mock_client.Options.StrikeRange.IN_THE_MONEY
    assert kwargs["contract_type"] is mock_client.Options.ContractType.CALL


@pytest.mark.asyncio
async def test_markets_passes_Market_enums(mock_client):
    resp = await rest_proxy.get_markets(markets="equity,option", date=None)
    assert resp.status_code == 200
    _, kwargs = mock_client.get_market_hours.call_args
    assert kwargs["markets"] == [
        mock_client.MarketHours.Market.EQUITY,
        mock_client.MarketHours.Market.OPTION,
    ]


@pytest.mark.asyncio
async def test_bad_enum_param_returns_400(mock_client):
    resp = await rest_proxy.get_markets(markets="crypto", date=None)
    assert resp.status_code == 400
