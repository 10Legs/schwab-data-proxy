"""
bootstrap.py — Token Gate bootstrap (Approach 2).

Standalone one-shot script. Run as the `init` service in docker-compose.
Probes an existing token; falls through to interactive re-auth if token is
missing, corrupt, or the refresh token is expired.

Uses sync schwab-py (asyncio=False) — no event loop required.
"""

import argparse
import sys
from pathlib import Path
from typing import Callable

import schwab.auth as schwab_auth

try:
    from authlib.integrations.base_client.errors import OAuthError as _OAuthError
except ImportError:  # pragma: no cover — authlib is always present via schwab-py
    _OAuthError = None

# Import settings from the installed package (schwab_data_proxy is on PYTHONPATH
# because Dockerfile COPYs app/ into /app/).
from schwab_data_proxy.settings import settings

_AUTH_FAILURE_SIGNALS = (
    "invalid_grant",
    "unsupported_token_type",
    "refresh token is invalid",
    "400 bad request",
    "401",
)


def _is_auth_failure(exc: Exception) -> bool:
    """Return True if *exc* signals an expired, invalid, or revoked refresh
    token (or a 400/401 from the token endpoint).

    Distinguishes an OAuth credential failure from a transient network error:
    - authlib raises ``OAuthError`` when the token-endpoint POST returns an
      error body — this is the primary and most reliable signal.
    - String scanning against known OAuth / HTTP-4xx phrases covers wrapped
      exceptions and future authlib internals.

    Returns False for network-level errors (connection refused, timeout, DNS,
    5xx) so that a Schwab outage does not delete a valid token.
    """
    # Primary: authlib raises OAuthError for all token-endpoint failures.
    # This is structurally distinct from httpx.TransportError (network errors).
    if _OAuthError is not None and isinstance(exc, _OAuthError):
        return True

    # Fallback: string-scan for known OAuth / HTTP-4xx signals in case the
    # exception is wrapped or raised by a different layer.
    exc_text = (str(exc) + " " + getattr(exc, "error", "")).lower()
    return any(sig in exc_text for sig in _AUTH_FAILURE_SIGNALS)


BANNER = """
─────────────────────────────────────────────
Schwab OAuth Bootstrap
─────────────────────────────────────────────
1. Open the URL below in a browser and log in
2. After authorizing, your browser will redirect
   to a URL starting with your callback URL
   (the page may fail to load — that is OK)
3. Copy the FULL URL from the address bar
4. Paste it here and press Enter
─────────────────────────────────────────────
"""


def _probe_existing_token(
    client_id: str,
    client_secret: str,
    token_path: str,
    probe_fn: Callable = None,
) -> bool:
    """
    Return True if the token file exists, loads without error, and passes a
    live probe call.  A 401 or 400 response triggers re-auth and the token
    file is deleted.  Network errors and other non-4xx HTTP errors are treated
    as "token probably fine" and the token file is left intact.

    probe_fn: callable(client) -> response.  Defaults to get_quote("SPY")
    (market data endpoint).  Pass get_user_preference for trader clients whose
    app does not have market data scope.
    """
    path = Path(token_path)
    if not path.exists():
        print(f"[bootstrap] Token file not found at {token_path}", file=sys.stderr)
        return False

    try:
        client = schwab_auth.client_from_token_file(
            token_path=token_path,
            api_key=client_id,
            app_secret=client_secret,
            asyncio=False,
        )
        print("[bootstrap] Token file loaded OK", file=sys.stderr)
    except Exception as exc:
        print(f"[bootstrap] Could not load token file: {exc}", file=sys.stderr)
        path.unlink(missing_ok=True)
        print(
            f"[bootstrap] Removed invalid token at {token_path}, re-authenticating.",
            file=sys.stderr,
        )
        return False

    if probe_fn is None:
        probe_fn = lambda c: c.get_quote("SPY")  # noqa: E731

    try:
        resp = probe_fn(client)
        if resp.status_code in (400, 401):
            print(
                f"[bootstrap] Token probe returned {resp.status_code} — refresh token expired.",
                file=sys.stderr,
            )
            path.unlink(missing_ok=True)
            print(
                f"[bootstrap] Removed invalid token at {token_path}, re-authenticating.",
                file=sys.stderr,
            )
            return False
        print(
            f"[bootstrap] Token probe returned HTTP {resp.status_code} — token valid.",
            file=sys.stderr,
        )
    except Exception as exc:
        if _is_auth_failure(exc):
            # The token endpoint rejected our refresh token (expired/revoked/invalid).
            # authlib raised an exception instead of returning a response object,
            # so the status_code branch above never fires.  Delete the stale token
            # so the caller falls through to interactive re-auth.
            print(
                f"[bootstrap] Token probe request failed ({exc}) — refresh token invalid.",
                file=sys.stderr,
            )
            path.unlink(missing_ok=True)
            print(
                f"[bootstrap] Removed invalid token at {token_path}, re-authenticating.",
                file=sys.stderr,
            )
            return False
        # Genuine network error (connection refused, timeout, DNS, 5xx, no internet).
        # Do NOT delete the token — a Schwab outage must not nuke a valid credential.
        print(
            f"[bootstrap] Token probe request failed ({exc}) — assuming token valid.",
            file=sys.stderr,
        )

    return True


def _interactive_auth(
    label: str,
    client_id: str,
    client_secret: str,
    callback_url: str,
    token_path: str,
) -> None:
    """Run the manual OAuth flow for one app. Exits on failure or abort."""
    if not sys.stdin.isatty():
        print(
            f"[bootstrap] {label} token is missing or expired and no TTY is available.\n"
            "Run interactively to re-authenticate:\n"
            "  docker compose run --rm init",
            file=sys.stderr,
        )
        sys.exit(1)

    # Always start clean — remove any stale/partial token before writing a new one.
    Path(token_path).unlink(missing_ok=True)

    print(f"\n--- {label} Authentication ---")
    print(BANNER)

    try:
        schwab_auth.client_from_manual_flow(
            api_key=client_id,
            app_secret=client_secret,
            callback_url=callback_url,
            token_path=token_path,
            asyncio=False,
        )
    except KeyboardInterrupt:
        print("\n[bootstrap] Aborted by operator.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[bootstrap] {label} authentication failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[bootstrap] {label} token written to {token_path}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Schwab OAuth bootstrap")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip token probe and force interactive re-authentication for all apps.",
    )
    args = parser.parse_args()

    if args.force:
        print("[bootstrap] --force", file=sys.stderr)

    # ── Market Data app ──────────────────────────────────────────────────────
    data_id = settings.SCHWAB_DATA_CLIENT_ID
    data_secret = settings.SCHWAB_DATA_CLIENT_SECRET
    data_token_path = settings.SCHWAB_DATA_TOKEN_PATH
    data_callback_url = settings.SCHWAB_DATA_CALLBACK_URL

    if not args.force and _probe_existing_token(data_id, data_secret, data_token_path):
        print("[bootstrap] Market data token valid.")
    else:
        _interactive_auth(
            label="Market Data",
            client_id=data_id,
            client_secret=data_secret,
            callback_url=data_callback_url,
            token_path=data_token_path,
        )

    # ── Trader API app (optional) ────────────────────────────────────────────
    trader_id = settings.SCHWAB_TRADER_CLIENT_ID
    if trader_id:
        trader_secret = settings.SCHWAB_TRADER_CLIENT_SECRET
        trader_token_path = settings.SCHWAB_TRADER_TOKEN_PATH
        trader_callback_url = settings.SCHWAB_TRADER_CALLBACK_URL or data_callback_url

        # Probe with a trader endpoint — get_quote is market data scope and
        # would 401 on a trader-only app, causing a false re-auth loop.
        if not args.force and _probe_existing_token(
            trader_id,
            trader_secret,
            trader_token_path,
            probe_fn=lambda c: c.get_account_numbers(),
        ):
            print("[bootstrap] Trader token valid.")
        else:
            _interactive_auth(
                label="Trader API",
                client_id=trader_id,
                client_secret=trader_secret,
                callback_url=trader_callback_url,
                token_path=trader_token_path,
            )

    print("[bootstrap] Bootstrap complete.")
    sys.exit(0)


if __name__ == "__main__":
    main()
