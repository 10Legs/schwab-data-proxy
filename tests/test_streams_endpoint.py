"""
Endpoint tests for GET /streams.

Uses FastAPI TestClient (sync). Stubs stream_router.status() via monkeypatch.
PROXY_API_KEY is scoped per-test via monkeypatch to avoid cross-test leakage.
SCHWAB_SKIP_INIT=true prevents session startup during TestClient init.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Ensure skip-init is set before the app module is imported for the first time.
os.environ.setdefault("SCHWAB_SKIP_INIT", "true")

from app.schwab_data_proxy.main import app  # noqa: E402
from app.schwab_data_proxy import stream_router as stream_router_module  # noqa: E402

_COUNTS_PAYLOAD = {
    "upstream_ready": True,
    "client_count": 1,
    "connections": [
        {"client_id": "abc", "counts": {"LEVELONE_EQUITIES": 2, "LEVELONE_OPTIONS": 0}}
    ],
    "upstream_union": {"LEVELONE_EQUITIES": 2, "LEVELONE_OPTIONS": 0},
}

_VERBOSE_PAYLOAD = {
    "upstream_ready": True,
    "client_count": 1,
    "connections": [
        {
            "client_id": "abc",
            "counts": {"LEVELONE_EQUITIES": 2, "LEVELONE_OPTIONS": 0},
            "symbols": {
                "LEVELONE_EQUITIES": ["AAPL", "MSFT"],
                "LEVELONE_OPTIONS": [],
            },
        }
    ],
    "upstream_union": {
        "LEVELONE_EQUITIES": ["AAPL", "MSFT"],
        "LEVELONE_OPTIONS": [],
    },
}


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def stub_status(monkeypatch):
    """Stub stream_router.status() to return canned payloads."""

    def _status(verbose: bool = False) -> dict:
        return _VERBOSE_PAYLOAD if verbose else _COUNTS_PAYLOAD

    monkeypatch.setattr(stream_router_module.stream_router, "status", _status)


# ---------------------------------------------------------------------------
# Auth behaviour
# ---------------------------------------------------------------------------


def test_no_key_returns_401(client, monkeypatch, stub_status):
    monkeypatch.setenv("PROXY_API_KEY", "secret")
    # Patch settings so middleware sees the new value
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "secret")

    resp = client.get("/streams")
    assert resp.status_code == 401


def test_valid_key_returns_200(client, monkeypatch, stub_status):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "secret")

    resp = client.get("/streams", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200


def test_wrong_key_returns_401(client, monkeypatch, stub_status):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "secret")

    resp = client.get("/streams", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# No-auth mode (PROXY_API_KEY empty)
# ---------------------------------------------------------------------------


def test_no_auth_mode_returns_200(client, monkeypatch, stub_status):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "")

    resp = client.get("/streams")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Default response (counts only)
# ---------------------------------------------------------------------------


def test_default_omits_symbols(client, monkeypatch, stub_status):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "")

    resp = client.get("/streams")
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_count"] == 1
    for conn in body["connections"]:
        assert "symbols" not in conn
    assert isinstance(body["upstream_union"]["LEVELONE_EQUITIES"], int)


# ---------------------------------------------------------------------------
# verbose=true
# ---------------------------------------------------------------------------


def test_verbose_includes_symbols(client, monkeypatch, stub_status):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "")

    resp = client.get("/streams?verbose=true")
    assert resp.status_code == 200
    body = resp.json()
    for conn in body["connections"]:
        assert "symbols" in conn
        assert isinstance(conn["symbols"]["LEVELONE_EQUITIES"], list)
    assert isinstance(body["upstream_union"]["LEVELONE_EQUITIES"], list)


# ---------------------------------------------------------------------------
# Invalid verbose value → 422
# ---------------------------------------------------------------------------


def test_verbose_garbage_returns_422(client, monkeypatch):
    from app.schwab_data_proxy import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "PROXY_API_KEY", "")

    resp = client.get("/streams?verbose=garbage")
    assert resp.status_code == 422
