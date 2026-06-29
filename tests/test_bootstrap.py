"""
Tests for app/bootstrap.py — token probe logic and --force CLI flag.

All tests mock schwab-py calls; no real network or auth is performed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.bootstrap as bootstrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int):
    return SimpleNamespace(status_code=status_code)


def _write_token(path: Path, content: str = '{"token": "valid"}') -> None:
    path.write_text(content)


# ---------------------------------------------------------------------------
# _probe_existing_token: valid token — returns True, file untouched
# ---------------------------------------------------------------------------

def test_probe_valid_token_returns_true(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    mock_client.get_quote.return_value = _make_response(200)

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is True
    assert token_file.exists(), "Valid token file must not be deleted"


# ---------------------------------------------------------------------------
# _probe_existing_token: missing file — returns False without touching FS
# ---------------------------------------------------------------------------

def test_probe_missing_file_returns_false(tmp_path):
    token_file = tmp_path / "missing_token.json"
    # do NOT create the file

    result = bootstrap._probe_existing_token(
        client_id="id",
        client_secret="secret",
        token_path=str(token_file),
    )

    assert result is False


# ---------------------------------------------------------------------------
# _probe_existing_token: corrupt / unloadable file — deleted, returns False
# ---------------------------------------------------------------------------

def test_probe_corrupt_file_deletes_and_returns_false(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file, content="NOT_VALID_JSON{{{{")

    with patch(
        "app.bootstrap.schwab_auth.client_from_token_file",
        side_effect=ValueError("bad token"),
    ):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is False
    assert not token_file.exists(), "Corrupt token file must be deleted"


# ---------------------------------------------------------------------------
# _probe_existing_token: 401 response — deleted, returns False
# ---------------------------------------------------------------------------

def test_probe_401_deletes_and_returns_false(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    mock_client.get_quote.return_value = _make_response(401)

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is False
    assert not token_file.exists(), "Token file must be deleted on 401"


# ---------------------------------------------------------------------------
# _probe_existing_token: 400 response — deleted, returns False
# ---------------------------------------------------------------------------

def test_probe_400_deletes_and_returns_false(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    mock_client.get_quote.return_value = _make_response(400)

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is False
    assert not token_file.exists(), "Token file must be deleted on 400"


# ---------------------------------------------------------------------------
# _probe_existing_token: network error — NOT deleted, returns True (skip)
# ---------------------------------------------------------------------------

def test_probe_network_error_keeps_file_and_returns_true(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    mock_client.get_quote.side_effect = ConnectionError("timeout")

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is True
    assert token_file.exists(), "Token file must NOT be deleted on network error"


# ---------------------------------------------------------------------------
# _probe_existing_token: non-4xx HTTP error (e.g. 503) — NOT deleted, returns True
# ---------------------------------------------------------------------------

def test_probe_503_keeps_file_and_returns_true(tmp_path):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    mock_client.get_quote.return_value = _make_response(503)

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    assert result is True
    assert token_file.exists(), "Token file must NOT be deleted on 503"


# ---------------------------------------------------------------------------
# _probe_existing_token: custom probe_fn is called (trader path)
# ---------------------------------------------------------------------------

def test_probe_uses_custom_probe_fn(tmp_path):
    token_file = tmp_path / "trader_token.json"
    _write_token(token_file)

    mock_client = MagicMock()
    custom_probe = MagicMock(return_value=_make_response(200))

    with patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client):
        result = bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
            probe_fn=custom_probe,
        )

    assert result is True
    custom_probe.assert_called_once_with(mock_client)


# ---------------------------------------------------------------------------
# _interactive_auth: defensively unlinks token before calling manual flow
# ---------------------------------------------------------------------------

def test_interactive_auth_unlinks_existing_token(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    # Make stdin look like a TTY
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))

    unlinked_before_flow = []

    def _fake_manual_flow(**kwargs):
        unlinked_before_flow.append(not token_file.exists())
        # Write the new token as the real flow would
        Path(kwargs["token_path"]).write_text('{"new": "token"}')

    with patch("app.bootstrap.schwab_auth.client_from_manual_flow", side_effect=_fake_manual_flow):
        bootstrap._interactive_auth(
            label="Test",
            client_id="id",
            client_secret="secret",
            callback_url="https://127.0.0.1",
            token_path=str(token_file),
        )

    assert unlinked_before_flow == [True], (
        "_interactive_auth must unlink the old token before calling client_from_manual_flow"
    )


# ---------------------------------------------------------------------------
# main(): --force bypasses probe and goes straight to interactive auth
# ---------------------------------------------------------------------------

def test_main_force_bypasses_probe(tmp_path, monkeypatch):
    """With --force, probe is never called and interactive auth runs for both apps."""
    token_file = tmp_path / "token.json"
    _write_token(token_file)  # A valid-looking token exists

    monkeypatch.setattr("sys.argv", ["bootstrap.py", "--force"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))

    # Patch settings so only the market-data app runs (no trader ID)
    mock_settings = MagicMock()
    mock_settings.SCHWAB_DATA_CLIENT_ID = "data_id"
    mock_settings.SCHWAB_DATA_CLIENT_SECRET = "data_secret"
    mock_settings.SCHWAB_DATA_TOKEN_PATH = str(token_file)
    mock_settings.SCHWAB_DATA_CALLBACK_URL = "https://127.0.0.1"
    mock_settings.SCHWAB_TRADER_CLIENT_ID = ""  # no trader app

    probe_calls = []

    def _fake_probe(*args, **kwargs):
        probe_calls.append(True)
        return True  # would pass if probe ran

    def _fake_manual_flow(**kwargs):
        Path(kwargs["token_path"]).write_text('{"refreshed": "token"}')

    with (
        patch("app.bootstrap.settings", mock_settings),
        patch("app.bootstrap._probe_existing_token", side_effect=_fake_probe),
        patch("app.bootstrap.schwab_auth.client_from_manual_flow", side_effect=_fake_manual_flow),
        pytest.raises(SystemExit) as exc_info,
    ):
        bootstrap.main()

    assert exc_info.value.code == 0
    assert probe_calls == [], "--force must bypass _probe_existing_token entirely"


# ---------------------------------------------------------------------------
# main(): no --force, valid token → probe runs, no interactive auth
# ---------------------------------------------------------------------------

def test_main_no_force_valid_token_skips_interactive_auth(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    _write_token(token_file)

    monkeypatch.setattr("sys.argv", ["bootstrap.py"])

    mock_settings = MagicMock()
    mock_settings.SCHWAB_DATA_CLIENT_ID = "data_id"
    mock_settings.SCHWAB_DATA_CLIENT_SECRET = "data_secret"
    mock_settings.SCHWAB_DATA_TOKEN_PATH = str(token_file)
    mock_settings.SCHWAB_DATA_CALLBACK_URL = "https://127.0.0.1"
    mock_settings.SCHWAB_TRADER_CLIENT_ID = ""

    interactive_calls = []

    def _fake_interactive(**kwargs):
        interactive_calls.append(True)

    mock_client = MagicMock()
    mock_client.get_quote.return_value = _make_response(200)

    with (
        patch("app.bootstrap.settings", mock_settings),
        patch("app.bootstrap.schwab_auth.client_from_token_file", return_value=mock_client),
        patch("app.bootstrap._interactive_auth", side_effect=_fake_interactive),
        pytest.raises(SystemExit) as exc_info,
    ):
        bootstrap.main()

    assert exc_info.value.code == 0
    assert interactive_calls == [], "Valid token must not trigger interactive auth"


# ---------------------------------------------------------------------------
# Stderr log format check: exact message logged on invalid token delete
# ---------------------------------------------------------------------------

def test_probe_corrupt_logs_exact_removal_message(tmp_path, capsys):
    token_file = tmp_path / "token.json"
    _write_token(token_file, content="CORRUPT")

    with patch(
        "app.bootstrap.schwab_auth.client_from_token_file",
        side_effect=ValueError("json decode error"),
    ):
        bootstrap._probe_existing_token(
            client_id="id",
            client_secret="secret",
            token_path=str(token_file),
        )

    captured = capsys.readouterr()
    expected_fragment = f"[bootstrap] Removed invalid token at {token_file}, re-authenticating."
    assert expected_fragment in captured.err, (
        f"Expected stderr to contain:\n  {expected_fragment!r}\nGot:\n  {captured.err!r}"
    )
