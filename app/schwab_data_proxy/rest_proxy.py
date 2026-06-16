"""
REST proxy endpoints — thin pass-through to Schwab API with TTL+LRU cache.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import enum_mapping
from .enum_mapping import UnknownEnumValue
from .schwab_session import session
from .settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared cache: key=(endpoint_name, frozenset(sorted_params))
_cache: TTLCache = TTLCache(maxsize=4096, ttl=settings.CACHE_TTL_SECONDS)
_cache_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _make_key(endpoint: str, params: dict) -> tuple:
    return (endpoint, frozenset(sorted(params.items())))


def _error_response(
    code: str,
    message: str,
    upstream_status: Optional[int] = None,
    http_status: int = 502,
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if upstream_status is not None:
        body["error"]["upstream_status"] = upstream_status
    return JSONResponse(status_code=http_status, content=body)


async def _cached_call(endpoint: str, params: dict, coroutine_factory) -> JSONResponse:
    key = _make_key(endpoint, params)

    async with _cache_lock:
        cached = _cache.get(key)

    if cached is not None:
        cached["cached"] = True
        return JSONResponse(content=cached)

    try:
        resp = await coroutine_factory()
    except UnknownEnumValue as exc:
        # An inbound param could not be mapped to a schwab-py enum — this is a
        # client error (bad query string), not an upstream failure.
        logger.warning("Invalid enum param for %s: %s", endpoint, exc)
        return _error_response("BAD_REQUEST", str(exc), http_status=400)
    except Exception as exc:  # noqa: BLE001
        logger.error("Upstream call failed for %s: %s", endpoint, exc)
        return _error_response("UPSTREAM_ERROR", str(exc))

    if resp.status_code == 429:
        return _error_response(
            "RATE_LIMITED",
            "Schwab rate limit exceeded",
            upstream_status=429,
            http_status=429,
        )
    if resp.status_code == 400:
        return _error_response(
            "BAD_REQUEST",
            "Invalid request parameters",
            upstream_status=400,
            http_status=400,
        )
    if resp.status_code != 200:
        return _error_response(
            "UPSTREAM_ERROR",
            f"Schwab returned HTTP {resp.status_code}",
            upstream_status=resp.status_code,
            http_status=502,
        )

    try:
        data = resp.json()
    except Exception:
        data = resp.text

    payload = {"data": data, "cached": False, "as_of": _now_iso()}

    async with _cache_lock:
        _cache[key] = dict(payload)

    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# /v1/quotes
# ---------------------------------------------------------------------------


@router.get("/v1/quotes")
async def get_quotes(
    request: Request,
    symbols: str = Query(..., description="Comma-separated list of symbols"),
    fields: Optional[str] = Query(
        None,
        description="Comma-separated fields: quote,reference,extended,fundamental,regular",
    ),
) -> JSONResponse:
    params = {"symbols": symbols}
    if fields:
        params["fields"] = fields

    client = session.client()
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return _error_response(
            "BAD_REQUEST",
            "symbols parameter is required and must not be empty",
            http_status=400,
        )

    async def call():
        kwargs: dict[str, Any] = {}
        if fields:
            field_values = enum_mapping.map_quote_fields(client, fields)
            if field_values:
                kwargs["fields"] = field_values
        return await client.get_quotes(symbol_list, **kwargs)

    return await _cached_call("quotes", params, call)


# ---------------------------------------------------------------------------
# /v1/chains
# ---------------------------------------------------------------------------


@router.get("/v1/chains")
async def get_chains(
    symbol: str = Query(...),
    contract_type: Optional[str] = Query(None),
    strike_count: Optional[int] = Query(None),
    include_underlying_quote: Optional[bool] = Query(None),
    strategy: Optional[str] = Query(None),
    interval: Optional[float] = Query(None),
    strike: Optional[float] = Query(None),
    range: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    volatility: Optional[float] = Query(None),
    underlying_price: Optional[float] = Query(None),
    interest_rate: Optional[float] = Query(None),
    days_to_expiration: Optional[int] = Query(None),
    exp_month: Optional[str] = Query(None),
    option_type: Optional[str] = Query(None),
    entitlement: Optional[str] = Query(None),
) -> JSONResponse:
    params = {
        k: v
        for k, v in {
            "symbol": symbol,
            "contract_type": contract_type,
            "strike_count": strike_count,
            "include_underlying_quote": include_underlying_quote,
            "strategy": strategy,
            "interval": interval,
            "strike": strike,
            "range": range,
            "from_date": from_date,
            "to_date": to_date,
            "volatility": volatility,
            "underlying_price": underlying_price,
            "interest_rate": interest_rate,
            "days_to_expiration": days_to_expiration,
            "exp_month": exp_month,
            "option_type": option_type,
            "entitlement": entitlement,
        }.items()
        if v is not None
    }

    client = session.client()

    async def call():
        kwargs: dict[str, Any] = {"symbol": symbol}
        if contract_type is not None:
            kwargs["contract_type"] = enum_mapping.map_contract_type(
                client, contract_type
            )
        if strike_count is not None:
            kwargs["strike_count"] = strike_count
        if include_underlying_quote is not None:
            kwargs["include_underlying_quote"] = include_underlying_quote
        if strategy is not None:
            kwargs["strategy"] = enum_mapping.map_strategy(client, strategy)
        if interval is not None:
            kwargs["interval"] = interval
        if strike is not None:
            kwargs["strike"] = strike
        if range is not None:
            # schwab-py 1.5.1 names this kwarg ``strike_range`` (not ``range``).
            kwargs["strike_range"] = enum_mapping.map_strike_range(client, range)
        if from_date is not None:
            kwargs["from_date"] = from_date
        if to_date is not None:
            kwargs["to_date"] = to_date
        if volatility is not None:
            kwargs["volatility"] = volatility
        if underlying_price is not None:
            kwargs["underlying_price"] = underlying_price
        if interest_rate is not None:
            kwargs["interest_rate"] = interest_rate
        if days_to_expiration is not None:
            kwargs["days_to_expiration"] = days_to_expiration
        if exp_month is not None:
            kwargs["exp_month"] = enum_mapping.map_expiration_month(client, exp_month)
        if option_type is not None:
            kwargs["option_type"] = enum_mapping.map_option_type(client, option_type)
        if entitlement is not None:
            kwargs["entitlement"] = enum_mapping.map_entitlement(client, entitlement)
        return await client.get_option_chain(**kwargs)

    return await _cached_call("chains", params, call)


# ---------------------------------------------------------------------------
# /v1/pricehistory
# ---------------------------------------------------------------------------


@router.get("/v1/pricehistory")
async def get_pricehistory(
    symbol: str = Query(...),
    period_type: Optional[str] = Query(None),
    period: Optional[int] = Query(None),
    frequency_type: Optional[str] = Query(None),
    frequency: Optional[int] = Query(None),
    start_datetime: Optional[str] = Query(None),
    end_datetime: Optional[str] = Query(None),
    need_extended_hours_data: Optional[bool] = Query(None),
    need_previous_close: Optional[bool] = Query(None),
) -> JSONResponse:
    params = {
        k: v
        for k, v in {
            "symbol": symbol,
            "period_type": period_type,
            "period": period,
            "frequency_type": frequency_type,
            "frequency": frequency,
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
            "need_extended_hours_data": need_extended_hours_data,
            "need_previous_close": need_previous_close,
        }.items()
        if v is not None
    }

    client = session.client()

    async def call():
        kwargs: dict[str, Any] = {"symbol": symbol}
        if period_type is not None:
            kwargs["period_type"] = enum_mapping.map_period_type(client, period_type)
        if period is not None:
            # schwab-py 1.5.1 enforces PriceHistory.Period (int-valued enum).
            kwargs["period"] = enum_mapping.map_period(client, period)
        if frequency_type is not None:
            kwargs["frequency_type"] = enum_mapping.map_frequency_type(
                client, frequency_type
            )
        if frequency is not None:
            # schwab-py 1.5.1 enforces PriceHistory.Frequency (int-valued enum).
            kwargs["frequency"] = enum_mapping.map_frequency(client, frequency)
        if start_datetime is not None:
            kwargs["start_datetime"] = start_datetime
        if end_datetime is not None:
            kwargs["end_datetime"] = end_datetime
        if need_extended_hours_data is not None:
            kwargs["need_extended_hours_data"] = need_extended_hours_data
        if need_previous_close is not None:
            kwargs["need_previous_close"] = need_previous_close
        return await client.get_price_history(**kwargs)

    return await _cached_call("pricehistory", params, call)


# ---------------------------------------------------------------------------
# /v1/markets
# ---------------------------------------------------------------------------


@router.get("/v1/markets")
async def get_markets(
    markets: str = Query(
        ..., description="Comma-separated: equity,option,bond,future,forex"
    ),
    date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
) -> JSONResponse:
    params = {"markets": markets}
    if date:
        params["date"] = date

    client = session.client()
    market_list = [m.strip() for m in markets.split(",") if m.strip()]
    if not market_list:
        return _error_response(
            "BAD_REQUEST", "markets parameter is required", http_status=400
        )

    async def call():
        market_enums = [enum_mapping.map_market(client, m) for m in market_list]
        kwargs: dict[str, Any] = {"markets": market_enums}
        if date:
            kwargs["date"] = date
        return await client.get_market_hours(**kwargs)

    return await _cached_call("markets", params, call)
