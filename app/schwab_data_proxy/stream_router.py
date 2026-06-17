"""
StreamRouter — upstream Schwab streaming connection with fan-out to downstream WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Set

from .subscription_manager import ServiceKey, SubscriptionManager

if TYPE_CHECKING:
    from .ws_server import WSConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field maps: Schwab numeric code → human-readable name
# Names match LevelOneEquityFields / LevelOneOptionFields enum member names
# (lowercase snake_case) from schwab-py 1.5.1.
# ---------------------------------------------------------------------------

EQUITY_FIELD_MAP: Dict[int, str] = {
    1: "bid_price",
    2: "ask_price",
    3: "last_price",
    4: "bid_size",
    5: "ask_size",
    8: "total_volume",
    9: "last_size",
    10: "high_price",
    11: "low_price",
    12: "close_price",
    17: "open_price",
    18: "net_change",
    29: "regular_market_last_price",
    31: "regular_market_net_change",
    33: "mark",
    34: "quote_time_millis",
    35: "trade_time_millis",
    42: "net_change_percent",
    44: "mark_change",
    45: "mark_change_percent",
}

OPTIONS_FIELD_MAP: Dict[int, str] = {
    1: "description",
    2: "bid_price",
    3: "ask_price",
    4: "last_price",
    5: "high_price",
    6: "low_price",
    7: "close_price",
    8: "total_volume",
    9: "open_interest",
    10: "volatility",
    15: "open_price",
    16: "bid_size",
    17: "ask_size",
    19: "net_change",
    22: "underlying",
    27: "days_to_expiration",
    28: "delta",
    29: "gamma",
    30: "theta",
    31: "vega",
    32: "rho",
    35: "underlying_price",
    37: "mark",
    44: "net_percent_change",
}

SERVICE_FIELD_MAP: Dict[str, Dict[int, str]] = {
    "LEVELONE_EQUITIES": EQUITY_FIELD_MAP,
    "LEVELONE_OPTIONS": OPTIONS_FIELD_MAP,
}


def _normalize_fields(service: str, raw_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Convert numeric string keys to named fields using the service field map."""
    field_map = SERVICE_FIELD_MAP.get(service, {})
    result: Dict[str, Any] = {}
    for k, v in raw_fields.items():
        try:
            numeric = int(k)
            name = field_map.get(numeric, k)
        except (ValueError, TypeError):
            name = k
        result[name] = v
    return result


class StreamRouter:
    def __init__(self) -> None:
        self.manager = SubscriptionManager()
        self._connections: Set["WSConnection"] = set()
        self.stream_ready: bool = False
        self._stream_client = None

    # ------------------------------------------------------------------
    # Connection registry
    # ------------------------------------------------------------------

    def register(self, conn: "WSConnection") -> None:
        self._connections.add(conn)
        logger.info(
            "StreamRouter: client %s registered (%d total)",
            conn.id,
            len(self._connections),
        )

    def unregister(self, conn: "WSConnection") -> None:
        self._connections.discard(conn)
        logger.info(
            "StreamRouter: client %s unregistered (%d total)",
            conn.id,
            len(self._connections),
        )
        # Schedule async cleanup (release subscriptions)
        asyncio.create_task(self._release_client(conn))

    async def _release_client(self, conn: "WSConnection") -> None:
        released = self.manager.release_client(conn.id)
        for service, symbols in released.items():
            if symbols:
                await self.apply(service, subscribe=set(), unsubscribe=symbols)

    # ------------------------------------------------------------------
    # Subscription application
    # ------------------------------------------------------------------

    async def apply(
        self,
        service: ServiceKey,
        subscribe: Set[str],
        unsubscribe: Set[str],
        initial: bool = False,
    ) -> None:
        """Send upstream sub/unsub commands only for non-empty deltas."""
        if self._stream_client is None:
            return

        try:
            if subscribe:
                logger.debug("StreamRouter: subscribing %s on %s", subscribe, service)
                await self._send_subscription(
                    service, list(subscribe), add=True, initial=initial
                )
            if unsubscribe:
                logger.debug(
                    "StreamRouter: unsubscribing %s on %s", unsubscribe, service
                )
                await self._send_subscription(service, list(unsubscribe), add=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("StreamRouter.apply error for %s: %s", service, exc)

    async def _send_subscription(
        self, service: ServiceKey, symbols: list[str], add: bool, initial: bool = False
    ) -> None:
        sc = self._stream_client
        if service == "LEVELONE_EQUITIES":
            fields = [sc.LevelOneEquityFields(k) for k in EQUITY_FIELD_MAP]
            if add:
                # Use subs() for initial/reconnect subscriptions, add() for incremental
                if initial:
                    await sc.level_one_equity_subs(symbols, fields=fields)
                else:
                    await sc.level_one_equity_add(symbols, fields=fields)
            else:
                await sc.level_one_equity_unsubs(symbols)
        elif service == "LEVELONE_OPTIONS":
            fields = [sc.LevelOneOptionFields(k) for k in OPTIONS_FIELD_MAP]
            if add:
                if initial:
                    await sc.level_one_option_subs(symbols, fields=fields)
                else:
                    await sc.level_one_option_add(symbols, fields=fields)
            else:
                await sc.level_one_option_unsubs(symbols)

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------

    def _on_tick(self, service: str, msg: Any) -> None:
        """
        Normalize and fan-out tick frames to subscribed clients.
        Drops oldest tick frame on queue overflow. Never drops ack/error/heartbeat.
        """
        if not isinstance(msg, dict):
            return

        content_list = msg.get("content", [])
        if not isinstance(content_list, list):
            content_list = [content_list]

        ts = datetime.now(tz=timezone.utc).isoformat()

        for item in content_list:
            if not isinstance(item, dict):
                continue
            symbol = item.get("key") or item.get("symbol", "")
            raw_fields = {k: v for k, v in item.items() if k not in ("key", "symbol")}
            named_fields = _normalize_fields(service, raw_fields)

            tick = {
                "type": "tick",
                "service": service,
                "symbol": symbol,
                "ts": ts,
                "fields": named_fields,
            }

            for conn in list(self._connections):
                # Only send if client has subscribed to this symbol+service
                if symbol in conn.subscriptions.get(service, set()):
                    try:
                        conn.out.put_nowait(tick)
                    except asyncio.QueueFull:
                        # Drop oldest tick to make room
                        try:
                            conn.out.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            conn.out.put_nowait(tick)
                        except asyncio.QueueFull:
                            logger.warning(
                                "StreamRouter: queue full for client %s after eviction; dropping tick",
                                conn.id,
                            )

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self, verbose: bool = False) -> dict:
        services = ("LEVELONE_EQUITIES", "LEVELONE_OPTIONS")
        connections = []
        for conn in self._connections:
            entry = {
                "client_id": conn.id,
                "counts": {svc: len(conn.subscriptions.get(svc, set())) for svc in services},
            }
            if verbose:
                entry["symbols"] = {svc: sorted(conn.subscriptions.get(svc, set())) for svc in services}
            connections.append(entry)
        upstream = {svc: self.manager.current_union(svc) for svc in services}
        return {
            "upstream_ready": self.stream_ready,
            "client_count": len(self._connections),
            "connections": connections,
            "upstream_union": (
                {svc: sorted(syms) for svc, syms in upstream.items()}
                if verbose
                else {svc: len(syms) for svc, syms in upstream.items()}
            ),
        }

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Login, register handlers, run handle_message loop. Reconnect on disconnect."""
        from .schwab_session import session

        backoff = 5
        max_backoff = 60

        while True:
            try:
                # Always create a fresh StreamClient on each connect attempt
                session.reset_stream_client()
                self._stream_client = session.stream_client()
                sc = self._stream_client

                # Register handlers
                sc.add_level_one_equity_handler(
                    lambda msg: self._on_tick("LEVELONE_EQUITIES", msg)
                )
                sc.add_level_one_option_handler(
                    lambda msg: self._on_tick("LEVELONE_OPTIONS", msg)
                )

                await sc.login()
                self.stream_ready = True
                logger.info("StreamRouter: stream logged in")
                backoff = 5  # reset on successful connect

                # Resubscribe full union using subs() (fresh connection)
                for service in ("LEVELONE_EQUITIES", "LEVELONE_OPTIONS"):
                    current = self.manager.current_union(service)
                    if current:
                        await self.apply(
                            service, subscribe=current, unsubscribe=set(), initial=True
                        )

                # handle_message() processes ONE message per call — loop until disconnect
                while True:
                    await sc.handle_message()

            except asyncio.CancelledError:
                logger.info("StreamRouter: cancelled, exiting")
                self.stream_ready = False
                return
            except Exception as exc:  # noqa: BLE001
                self.stream_ready = False
                logger.error(
                    "StreamRouter: stream disconnected (%s); reconnecting in %ds",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                self._stream_client = None


stream_router = StreamRouter()
