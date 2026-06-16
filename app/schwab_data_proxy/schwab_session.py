"""
SchwabSession — wraps schwab-py AsyncClient and StreamClient.
Token is pre-bootstrapped on disk; no OAuth flow runs inside the container.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import schwab.auth as schwab_auth
import schwab.streaming as schwab_streaming

from .settings import settings

logger = logging.getLogger(__name__)


class SchwabSession:
    def __init__(self) -> None:
        self._client = None
        self._trader_client = None
        self._stream_client = None

    async def start(self) -> None:
        """Load token from SCHWAB_DATA_TOKEN_PATH. Fatal if file missing or invalid."""
        token_path = Path(settings.SCHWAB_DATA_TOKEN_PATH)
        if not token_path.exists():
            logger.critical(
                "Token file not found at %s — run the schwab-py auth flow to bootstrap it",
                token_path,
            )
            sys.exit(1)

        try:
            self._client = schwab_auth.client_from_token_file(
                token_path=str(token_path),
                api_key=settings.SCHWAB_DATA_CLIENT_ID,
                app_secret=settings.SCHWAB_DATA_CLIENT_SECRET,
                asyncio=True,
            )
            logger.info("SchwabSession started; token loaded from %s", token_path)
        except Exception as exc:  # noqa: BLE001
            logger.critical("Failed to load Schwab token: %s", exc)
            sys.exit(1)

        # Load trader client — dedicated credentials if configured, else fall back.
        trader_key = settings.SCHWAB_TRADER_CLIENT_ID
        trader_token_path = Path(settings.SCHWAB_TRADER_TOKEN_PATH)
        if trader_key and trader_token_path.exists():
            try:
                self._trader_client = schwab_auth.client_from_token_file(
                    token_path=str(trader_token_path),
                    api_key=trader_key,
                    app_secret=settings.SCHWAB_TRADER_CLIENT_SECRET,
                    asyncio=True,
                )
                logger.info(
                    "SchwabSession: using dedicated trader client (token: %s)",
                    trader_token_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to load trader token (%s) — falling back to main client for streaming: %s",
                    trader_token_path,
                    exc,
                )
                self._trader_client = self._client
        else:
            if trader_key:
                logger.warning(
                    "SCHWAB_TRADER_CLIENT_ID is set but token file %s does not exist — "
                    "falling back to main client for streaming",
                    trader_token_path,
                )
            else:
                logger.info(
                    "SCHWAB_TRADER_CLIENT_ID not set — using main client for streaming"
                )
            self._trader_client = self._client

        # Validate token is actually usable (catches dead refresh tokens before
        # the proxy starts serving traffic).
        try:
            resp = await self._client.get_quote("SPY")
            if resp.status_code == 401:
                logger.critical(
                    "Token probe returned 401 — refresh token is expired. "
                    "Re-run the bootstrap: docker compose run --rm init"
                )
                sys.exit(1)
            elif resp.status_code != 200:
                logger.warning(
                    "Token probe returned HTTP %s — proceeding anyway", resp.status_code
                )
            else:
                logger.info("Token probe OK (SPY quote returned 200)")
        except Exception as exc:  # noqa: BLE001
            logger.critical(
                "Token probe failed: %s — token may be expired. "
                "Re-run the bootstrap: docker compose run --rm init",
                exc,
            )
            sys.exit(1)

    def client(self):
        """Return the authenticated AsyncClient."""
        if self._client is None:
            raise RuntimeError("SchwabSession.start() has not been called")
        return self._client

    def trader_client(self):
        """Return the trader AsyncClient (dedicated or main fallback)."""
        if self._trader_client is None:
            raise RuntimeError("SchwabSession.start() has not been called")
        return self._trader_client

    def stream_client(self):
        """
        Lazily construct StreamClient via the trader AsyncClient session.
        Uses dedicated trader credentials when configured, else falls back to main.
        Must be called after start().
        """
        if self._client is None:
            raise RuntimeError("SchwabSession.start() has not been called")
        if self._stream_client is None:
            self._stream_client = schwab_streaming.StreamClient(self.trader_client())
        return self._stream_client

    def reset_stream_client(self) -> None:
        """Discard the cached StreamClient so the next call to stream_client() creates a fresh one."""
        self._stream_client = None

    async def token_refresh_loop(self) -> None:
        """
        Keep the token alive by making a cheap SPY quote call every
        min(token_ttl / 2, 1800) seconds.  On failure: exponential backoff
        capped at 60 s.
        """
        interval = 1800  # ping every 30 min; schwab-py refresh token TTL is ~7 days
        backoff = 5
        max_backoff = 60

        while True:
            await asyncio.sleep(interval)
            try:
                resp = await self._client.get_quote("SPY")
                if resp.status_code == 200:
                    logger.debug("Token refresh ping OK (SPY quote returned 200)")
                    backoff = 5  # reset on success
                else:
                    logger.warning(
                        "Token refresh ping returned HTTP %s", resp.status_code
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
            except Exception as exc:  # noqa: BLE001
                logger.error("Token refresh loop error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)


session = SchwabSession()
