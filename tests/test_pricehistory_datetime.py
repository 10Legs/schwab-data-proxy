"""
Unit tests for _parse_datetime_param.

Exercises epoch-ms integer strings, ISO 8601 with/without timezone offset,
invalid input, and edge cases.  conftest.py sets SCHWAB_SKIP_INIT=true before
collection so no Schwab session init is attempted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schwab_data_proxy.rest_proxy import _parse_datetime_param


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Epoch-millisecond paths
# ---------------------------------------------------------------------------


def test_epoch_ms_integer_string_returns_utc_datetime():
    """ "1749945600000" → 2026-06-14 16:00:00 UTC (1749945600 seconds)."""
    result = _parse_datetime_param("1749945600000")
    expected = datetime.fromtimestamp(1749945600, tz=UTC)
    assert result == expected
    assert result.tzinfo == UTC


def test_epoch_ms_zero_is_unix_epoch():
    """Edge: "0" must NOT be skipped as falsy — int("0") path must fire."""
    result = _parse_datetime_param("0")
    expected = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert result == expected
    assert result.tzinfo == UTC


def test_epoch_ms_large_recent_timestamp():
    """Spot-check a June 2026 market-open epoch-ms value."""
    # 2026-06-16 13:30:00 UTC  →  1750081800 seconds
    ms_value = 1750081800 * 1000
    result = _parse_datetime_param(str(ms_value))
    expected = datetime.fromtimestamp(1750081800, tz=UTC)
    assert result == expected
    assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# Known ambiguity: bare 4-digit year "2026"
# ---------------------------------------------------------------------------


def test_epoch_ms_ambiguity_bare_year_string():
    """
    KNOWN AMBIGUITY (pinned, do not change app logic):
    "2026" passes int() → treated as epoch-ms (2026 ms = 1970-01-01T00:00:02.026Z),
    NOT as ISO year 2026.  This is intentional because exo always sends full ms
    values; a bare year is not a valid input format for this API.
    Document this so future maintainers treat any change as deliberate.
    """
    result = _parse_datetime_param("2026")
    expected = datetime.fromtimestamp(2.026, tz=UTC)
    assert result == expected
    # Confirm it is NOT interpreted as year 2026
    assert result.year == 1970


# ---------------------------------------------------------------------------
# ISO 8601 paths
# ---------------------------------------------------------------------------


def test_iso8601_with_utc_offset_preserves_tzinfo():
    """ISO 8601 with +00:00 — tzinfo is preserved (not double-replaced)."""
    result = _parse_datetime_param("2026-06-16T09:30:00+00:00")
    assert result == datetime(2026, 6, 16, 9, 30, 0, tzinfo=UTC)
    assert result.tzinfo is not None


def test_iso8601_with_nonzero_offset():
    """ISO 8601 with -05:00 — offset is preserved exactly as parsed."""
    from datetime import timezone as tz_mod, timedelta

    result = _parse_datetime_param("2026-06-16T09:30:00-05:00")
    eastern = tz_mod(timedelta(hours=-5))
    assert result == datetime(2026, 6, 16, 9, 30, 0, tzinfo=eastern)
    # tzinfo is not None and not replaced with UTC
    assert result.utcoffset().total_seconds() == -5 * 3600


def test_iso8601_without_tz_defaults_to_utc():
    """ISO 8601 naive string → UTC applied as default."""
    result = _parse_datetime_param("2026-06-16T09:30:00")
    assert result == datetime(2026, 6, 16, 9, 30, 0, tzinfo=UTC)
    assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_garbage_input_raises_value_error():
    """Non-parseable string → ValueError with expected message fragment."""
    with pytest.raises(ValueError, match="Cannot parse datetime param"):
        _parse_datetime_param("not-a-date")


def test_empty_string_raises_value_error():
    """Empty string → int("") raises ValueError; ISO parse also fails."""
    with pytest.raises(ValueError, match="Cannot parse datetime param"):
        _parse_datetime_param("")
