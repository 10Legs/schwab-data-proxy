"""
Trader API proxy endpoints — accounts, orders, transactions.

Never cached. Always uses session.trader_client().
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from .rest_proxy import _error_response, _now_iso
from .schwab_session import session

logger = logging.getLogger(__name__)

trader_router = APIRouter(prefix="/trader/v1")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _default_from_date() -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=60)


def _default_to_date() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_dt(s: Optional[str], default: datetime) -> datetime:
    if s is None:
        return default
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Core helper: _trader_call
# ---------------------------------------------------------------------------


async def _trader_call(coro_factory) -> JSONResponse:
    try:
        resp = await coro_factory()
    except Exception as exc:  # noqa: BLE001
        logger.error("Trader upstream call failed: %s", exc)
        return _error_response("UPSTREAM_ERROR", str(exc))

    if resp.status_code == 201:
        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").rsplit("/", 1)[-1] if location else None
        return JSONResponse(
            status_code=201,
            content={"order_id": order_id, "as_of": _now_iso()},
        )

    if resp.status_code == 204:
        return JSONResponse(status_code=204, content=None)

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return JSONResponse(content={"data": data, "as_of": _now_iso()})

    if resp.status_code == 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        message = str(detail) if detail else "Invalid request parameters"
        return _error_response("BAD_REQUEST", message, upstream_status=400, http_status=400)

    if resp.status_code == 401:
        return _error_response(
            "UNAUTHORIZED", "Trader token expired", upstream_status=401, http_status=401
        )

    if resp.status_code == 403:
        return _error_response(
            "FORBIDDEN", "Insufficient permissions", upstream_status=403, http_status=403
        )

    if resp.status_code == 404:
        return _error_response(
            "NOT_FOUND", "Resource not found", upstream_status=404, http_status=404
        )

    if resp.status_code == 429:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        message = str(detail) if detail else "Schwab rate limit exceeded"
        return _error_response(
            "RATE_LIMITED", message, upstream_status=429, http_status=429
        )

    return _error_response(
        "UPSTREAM_ERROR",
        f"Schwab returned HTTP {resp.status_code}",
        upstream_status=resp.status_code,
        http_status=502,
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@trader_router.get("/accounts/numbers")
async def get_account_numbers() -> JSONResponse:
    """Return list of {accountNumber, hashValue} pairs."""
    client = session.trader_client()

    async def call():
        return await client.get_account_numbers()

    return await _trader_call(call)


@trader_router.get("/accounts")
async def get_accounts(
    fields: Optional[str] = Query(
        None, description="Comma-separated: positions,orders"
    ),
) -> JSONResponse:
    client = session.trader_client()

    async def call():
        kwargs: dict[str, Any] = {}
        if fields:
            field_enums = []
            for f in fields.split(","):
                f = f.strip()
                try:
                    field_enums.append(client.Account.Fields[f.upper()])
                except (KeyError, AttributeError):
                    field_enums.append(f)
            if field_enums:
                kwargs["fields"] = field_enums
        return await client.get_accounts(**kwargs)

    return await _trader_call(call)


@trader_router.get("/accounts/{account_hash}")
async def get_account(
    account_hash: str = Path(..., description="Schwab account hash"),
    fields: Optional[str] = Query(
        None, description="Comma-separated: positions,orders"
    ),
) -> JSONResponse:
    client = session.trader_client()

    async def call():
        kwargs: dict[str, Any] = {}
        if fields:
            field_enums = []
            for f in fields.split(","):
                f = f.strip()
                try:
                    field_enums.append(client.Account.Fields[f.upper()])
                except (KeyError, AttributeError):
                    field_enums.append(f)
            if field_enums:
                kwargs["fields"] = field_enums
        return await client.get_account(account_hash, **kwargs)

    return await _trader_call(call)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@trader_router.get("/accounts/{account_hash}/orders")
async def get_orders(
    account_hash: str = Path(..., description="Schwab account hash"),
    from_date: Optional[str] = Query(None, description="ISO8601 datetime (default: 60 days ago)"),
    to_date: Optional[str] = Query(None, description="ISO8601 datetime (default: now)"),
    max_results: Optional[int] = Query(None, description="Maximum number of orders to return"),
    status: Optional[str] = Query(None, description="Order status filter"),
) -> JSONResponse:
    client = session.trader_client()

    from_dt = _parse_dt(from_date, _default_from_date())
    to_dt = _parse_dt(to_date, _default_to_date())

    async def call():
        kwargs: dict[str, Any] = {
            "from_entered_datetime": from_dt,
            "to_entered_datetime": to_dt,
        }
        if max_results is not None:
            kwargs["max_results"] = max_results
        if status is not None:
            try:
                kwargs["status"] = client.Order.Status[status.upper()]
            except (KeyError, AttributeError):
                kwargs["status"] = status
        return await client.get_orders_for_account(account_hash, **kwargs)

    return await _trader_call(call)


@trader_router.get("/accounts/{account_hash}/orders/{order_id}")
async def get_order(
    account_hash: str = Path(..., description="Schwab account hash"),
    order_id: int = Path(..., description="Schwab order ID"),
) -> JSONResponse:
    client = session.trader_client()

    async def call():
        return await client.get_order(order_id, account_hash)

    return await _trader_call(call)


@trader_router.post("/accounts/{account_hash}/orders")
async def place_order(
    request: Request,
    account_hash: str = Path(..., description="Schwab account hash"),
) -> JSONResponse:
    order_spec = await request.json()
    client = session.trader_client()

    async def call():
        return await client.place_order(account_hash, order_spec)

    return await _trader_call(call)


@trader_router.put("/accounts/{account_hash}/orders/{order_id}")
async def replace_order(
    request: Request,
    account_hash: str = Path(..., description="Schwab account hash"),
    order_id: int = Path(..., description="Schwab order ID"),
) -> JSONResponse:
    order_spec = await request.json()
    client = session.trader_client()

    async def call():
        return await client.replace_order(account_hash, order_id, order_spec)

    return await _trader_call(call)


@trader_router.delete("/accounts/{account_hash}/orders/{order_id}")
async def cancel_order(
    account_hash: str = Path(..., description="Schwab account hash"),
    order_id: int = Path(..., description="Schwab order ID"),
) -> JSONResponse:
    client = session.trader_client()

    async def call():
        return await client.cancel_order(account_hash, order_id)

    return await _trader_call(call)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@trader_router.get("/accounts/{account_hash}/transactions")
async def get_transactions(
    account_hash: str = Path(..., description="Schwab account hash"),
    types: Optional[str] = Query(None, description="Comma-separated transaction types"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    start_date: Optional[str] = Query(None, description="ISO8601 datetime (default: 60 days ago)"),
    end_date: Optional[str] = Query(None, description="ISO8601 datetime (default: now)"),
) -> JSONResponse:
    client = session.trader_client()

    start_dt = _parse_dt(start_date, _default_from_date())
    end_dt = _parse_dt(end_date, _default_to_date())

    async def call():
        kwargs: dict[str, Any] = {
            "start_date": start_dt,
            "end_date": end_dt,
        }
        if types is not None:
            type_enums = []
            for t in types.split(","):
                t = t.strip()
                try:
                    type_enums.append(client.Transactions.TransactionType[t.upper()])
                except (KeyError, AttributeError):
                    type_enums.append(t)
            if type_enums:
                kwargs["transaction_types"] = type_enums
        if symbol is not None:
            kwargs["symbol"] = symbol
        return await client.get_transactions(account_hash, **kwargs)

    return await _trader_call(call)


@trader_router.get("/accounts/{account_hash}/transactions/{transaction_id}")
async def get_transaction(
    account_hash: str = Path(..., description="Schwab account hash"),
    transaction_id: str = Path(..., description="Schwab transaction ID"),
) -> JSONResponse:
    client = session.trader_client()

    async def call():
        return await client.get_transaction(account_hash, transaction_id)

    return await _trader_call(call)
