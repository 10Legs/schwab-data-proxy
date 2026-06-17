"""
Unit tests for StreamRouter.status().

Constructs StreamRouter in isolation, injects fake WSConnection objects via
_connections, stubs SubscriptionManager.current_union via monkeypatch.
No I/O, no network.
"""

from __future__ import annotations

from typing import Dict, Set

import pytest

from app.schwab_data_proxy.stream_router import StreamRouter

SERVICES = ("LEVELONE_EQUITIES", "LEVELONE_OPTIONS")


class _FakeConn:
    """Minimal WSConnection stand-in with .id and .subscriptions. Hashable by identity."""

    def __init__(self, client_id: str, subscriptions: Dict[str, Set[str]]) -> None:
        self.id = client_id
        self.subscriptions = subscriptions


def _make_conn(client_id: str, subscriptions: Dict[str, Set[str]]) -> _FakeConn:
    return _FakeConn(client_id, subscriptions)


def _make_router(
    connections,
    union: Dict[str, Set[str]],
    stream_ready: bool = True,
    monkeypatch=None,
) -> StreamRouter:
    router = StreamRouter()
    router.stream_ready = stream_ready
    for conn in connections:
        router._connections.add(conn)
    # Stub current_union to return known sets
    if monkeypatch is not None:
        monkeypatch.setattr(
            router.manager,
            "current_union",
            lambda svc: union.get(svc, set()),
        )
    else:
        router.manager.current_union = lambda svc: union.get(svc, set())
    return router


# ---------------------------------------------------------------------------
# verbose=False
# ---------------------------------------------------------------------------


def test_status_counts_only_no_symbols_key():
    conn = _make_conn(
        "c1",
        {
            "LEVELONE_EQUITIES": {"AAPL", "MSFT"},
            "LEVELONE_OPTIONS": {"AAPL240101C00100000"},
        },
    )
    router = _make_router(
        [conn],
        union={
            "LEVELONE_EQUITIES": {"AAPL", "MSFT"},
            "LEVELONE_OPTIONS": {"AAPL240101C00100000"},
        },
    )

    result = router.status(verbose=False)

    assert result["upstream_ready"] is True
    assert result["client_count"] == 1
    assert len(result["connections"]) == 1

    entry = result["connections"][0]
    assert entry["client_id"] == "c1"
    assert entry["counts"]["LEVELONE_EQUITIES"] == 2
    assert entry["counts"]["LEVELONE_OPTIONS"] == 1
    assert "symbols" not in entry

    # upstream_union values must be ints
    assert isinstance(result["upstream_union"]["LEVELONE_EQUITIES"], int)
    assert isinstance(result["upstream_union"]["LEVELONE_OPTIONS"], int)
    assert result["upstream_union"]["LEVELONE_EQUITIES"] == 2
    assert result["upstream_union"]["LEVELONE_OPTIONS"] == 1


# ---------------------------------------------------------------------------
# verbose=True
# ---------------------------------------------------------------------------


def test_status_verbose_symbols_present_and_sorted():
    conn = _make_conn(
        "c2",
        {
            "LEVELONE_EQUITIES": {"TSLA", "AAPL", "GOOG"},
            "LEVELONE_OPTIONS": set(),
        },
    )
    router = _make_router(
        [conn],
        union={
            "LEVELONE_EQUITIES": {"TSLA", "AAPL", "GOOG"},
            "LEVELONE_OPTIONS": set(),
        },
    )

    result = router.status(verbose=True)

    entry = result["connections"][0]
    assert "symbols" in entry
    assert entry["symbols"]["LEVELONE_EQUITIES"] == ["AAPL", "GOOG", "TSLA"]
    assert entry["symbols"]["LEVELONE_OPTIONS"] == []

    # upstream_union values must be sorted lists
    assert result["upstream_union"]["LEVELONE_EQUITIES"] == ["AAPL", "GOOG", "TSLA"]
    assert result["upstream_union"]["LEVELONE_OPTIONS"] == []


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_status_empty_state():
    router = _make_router(
        [],
        union={"LEVELONE_EQUITIES": set(), "LEVELONE_OPTIONS": set()},
        stream_ready=False,
    )

    result = router.status()

    assert result["upstream_ready"] is False
    assert result["client_count"] == 0
    assert result["connections"] == []
    assert result["upstream_union"]["LEVELONE_EQUITIES"] == 0
    assert result["upstream_union"]["LEVELONE_OPTIONS"] == 0


# ---------------------------------------------------------------------------
# No mutation
# ---------------------------------------------------------------------------


def test_status_does_not_mutate_state():
    conn = _make_conn(
        "c3",
        {
            "LEVELONE_EQUITIES": {"SPY"},
            "LEVELONE_OPTIONS": set(),
        },
    )
    router = _make_router(
        [conn],
        union={"LEVELONE_EQUITIES": {"SPY"}, "LEVELONE_OPTIONS": set()},
    )

    connections_before = frozenset(id(c) for c in router._connections)
    ready_before = router.stream_ready

    router.status(verbose=False)
    router.status(verbose=True)

    connections_after = frozenset(id(c) for c in router._connections)
    assert connections_before == connections_after
    assert router.stream_ready == ready_before
    # subscriptions on the conn object must be unchanged
    assert conn.subscriptions["LEVELONE_EQUITIES"] == {"SPY"}
