"""
bootstrap.py — Token Gate bootstrap (Approach 2).

Standalone one-shot script. Run as the `init` service in docker-compose.
Probes an existing token; falls through to interactive re-auth if token is
missing, corrupt, or the refresh token is expired.

Uses sync schwab-py (asyncio=False) — no event loop required.
"""

import sys
from pathlib import Path
from typing import Callable

import schwab.auth as schwab_auth

# Import settings from the installed package (schwab_data_proxy is on PYTHONPATH
# because Dockerfile COPYs app/ into /app/).
from schwab_data_proxy.settings import settings

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
    live probe call.  Only a 401 response triggers re-auth — network errors
    and non-401 HTTP errors are treated as "token probably fine."

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
        return False

    if probe_fn is None:
        probe_fn = lambda c: c.get_quote("SPY")  # noqa: E731

    try:
        resp = probe_fn(client)
        if resp.status_code == 401:
            print(
                "[bootstrap] Token probe returned 401 — refresh token expired.",
                file=sys.stderr,
            )
            return False
        print(
            f"[bootstrap] Token probe returned HTTP {resp.status_code} — token valid.",
            file=sys.stderr,
        )
    except Exception as exc:
        # Network error, timeout, etc. — assume token is fine; proxy will handle it.
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
    # ── Market Data app ──────────────────────────────────────────────────────
    data_id = settings.SCHWAB_DATA_CLIENT_ID
    data_secret = settings.SCHWAB_DATA_CLIENT_SECRET
    data_token_path = settings.SCHWAB_DATA_TOKEN_PATH
    data_callback_url = settings.SCHWAB_DATA_CALLBACK_URL

    if _probe_existing_token(data_id, data_secret, data_token_path):
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
        if _probe_existing_token(
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
