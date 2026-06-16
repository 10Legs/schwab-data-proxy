"""
bootstrap.py — Token Gate bootstrap (Approach 2).

Standalone one-shot script. Run as the `init` service in docker-compose.
Probes an existing token; falls through to interactive re-auth if token is
missing, corrupt, or the refresh token is expired.

Uses sync schwab-py (asyncio=False) — no event loop required.
"""

import sys
from pathlib import Path

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


def _probe_existing_token(app_key: str, app_secret: str, token_path: str) -> bool:
    """
    Return True if the token file exists and loads without error.
    We only do a live REST call to detect a dead refresh token (401).
    Network errors and non-401 HTTP errors are treated as "token probably fine" —
    a transient network failure inside the init container should not trigger re-auth.
    """
    path = Path(token_path)
    if not path.exists():
        print(f"[bootstrap] Token file not found at {token_path}", file=sys.stderr)
        return False

    try:
        client = schwab_auth.client_from_token_file(
            token_path=token_path,
            api_key=app_key,
            app_secret=app_secret,
            asyncio=False,
        )
        print("[bootstrap] Token file loaded OK", file=sys.stderr)
    except Exception as exc:
        print(f"[bootstrap] Could not load token file: {exc}", file=sys.stderr)
        return False

    # Attempt a live call only to detect a dead refresh token (401).
    # Any other outcome (network error, 5xx, market closed, etc.) → treat as valid.
    try:
        resp = client.get_quote("SPY")
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


def main() -> None:
    app_key = settings.SCHWAB_DATA_APP_KEY
    app_secret = settings.SCHWAB_DATA_APP_SECRET
    token_path = settings.SCHWAB_DATA_TOKEN_PATH
    callback_url = settings.SCHWAB_DATA_CALLBACK_URL

    # 1. Probe existing token.
    if _probe_existing_token(app_key, app_secret, token_path):
        print("Market data token valid.")
    else:
        # 2. Guard: no TTY means we're detached — can't prompt, must fail fast.
        if not sys.stdin.isatty():
            print(
                "[bootstrap] Token is missing or expired and no TTY is available for re-auth.\n"
                "Run interactively to re-authenticate:\n"
                "  docker compose run --rm init",
                file=sys.stderr,
            )
            sys.exit(1)

        # 3. Interactive re-auth for market data.
        print(BANNER)

        try:
            schwab_auth.client_from_manual_flow(
                api_key=app_key,
                app_secret=app_secret,
                callback_url=callback_url,
                token_path=token_path,
                asyncio=False,
            )
        except KeyboardInterrupt:
            print("\n[bootstrap] Aborted by operator.", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"[bootstrap] Authentication failed: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"Market data token written to {token_path}.")

    # --- Trader API bootstrap (optional) ---
    trader_key = settings.SCHWAB_TRADER_APP_KEY
    if trader_key:
        trader_secret = settings.SCHWAB_TRADER_APP_SECRET
        trader_token_path = settings.SCHWAB_TRADER_TOKEN_PATH
        # Use trader-specific callback if set; fall back to data callback.
        trader_callback_url = settings.SCHWAB_TRADER_CALLBACK_URL or callback_url

        if _probe_existing_token(trader_key, trader_secret, trader_token_path):
            print("Trader token valid.")
        else:
            # Guard: no TTY — can't prompt.
            if not sys.stdin.isatty():
                print(
                    "[bootstrap] Trader token is missing or expired and no TTY is available.\n"
                    "Run interactively to re-authenticate:\n"
                    "  docker compose run --rm init",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Interactive re-auth for trader credentials.
            print("\n--- Trader API Authentication ---")
            print(BANNER)

            try:
                schwab_auth.client_from_manual_flow(
                    api_key=trader_key,
                    app_secret=trader_secret,
                    callback_url=trader_callback_url,
                    token_path=trader_token_path,
                    asyncio=False,
                )
            except KeyboardInterrupt:
                print("\n[bootstrap] Aborted by operator.", file=sys.stderr)
                sys.exit(1)
            except Exception as exc:
                print(f"[bootstrap] Trader authentication failed: {exc}", file=sys.stderr)
                sys.exit(1)

            print(f"Trader token written to {trader_token_path}.")

    print("Bootstrap complete.")
    sys.exit(0)


if __name__ == "__main__":
    main()
