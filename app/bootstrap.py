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
    Return True if the token file is valid and passes a live REST probe.
    Return False if the token is missing, corrupt, or the refresh token is dead.
    """
    path = Path(token_path)
    if not path.exists():
        return False

    try:
        client = schwab_auth.client_from_token_file(
            token_path=token_path,
            api_key=app_key,
            app_secret=app_secret,
            asyncio=False,
        )
    except Exception as exc:
        print(f"[bootstrap] Could not load token file: {exc}", file=sys.stderr)
        return False

    # Cheap REST call to force a refresh attempt.
    try:
        resp = client.get_quote("SPY")
    except Exception as exc:
        print(f"[bootstrap] Token probe request failed: {exc}", file=sys.stderr)
        return False

    if resp.status_code == 200:
        return True

    if resp.status_code == 401:
        print(
            "[bootstrap] Token probe returned 401 — refresh token likely expired.",
            file=sys.stderr,
        )
        return False

    # Unexpected status — log but treat as valid; proxy will deal with it.
    print(
        f"[bootstrap] Token probe returned HTTP {resp.status_code} — treating token as valid.",
        file=sys.stderr,
    )
    return True


def main() -> None:
    app_key = settings.SCHWAB_APP_KEY
    app_secret = settings.SCHWAB_APP_SECRET
    token_path = settings.SCHWAB_TOKEN_PATH
    callback_url = settings.SCHWAB_CALLBACK_URL

    # 1. Probe existing token.
    if _probe_existing_token(app_key, app_secret, token_path):
        print("Token valid. Bootstrap complete.")
        sys.exit(0)

    # 2. Interactive re-auth.
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

    print(f"Token written to {token_path}. Bootstrap complete.")
    sys.exit(0)


if __name__ == "__main__":
    main()
