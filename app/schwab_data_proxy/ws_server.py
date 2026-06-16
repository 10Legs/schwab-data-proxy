"""
WebSocket server — per-client connection handler with reader/writer tasks and heartbeat.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .stream_router import ServiceKey, stream_router

logger = logging.getLogger(__name__)

ws_router = APIRouter()

HEARTBEAT_INTERVAL = 15  # seconds


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class WSConnection:
    def __init__(self, ws: WebSocket) -> None:
        self.id: str = str(uuid.uuid4())
        self.ws: WebSocket = ws
        self.out: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscriptions: Dict[str, Set[str]] = {
            "LEVELONE_EQUITIES": set(),
            "LEVELONE_OPTIONS": set(),
        }

    # ------------------------------------------------------------------
    # Reader — parse commands from client
    # ------------------------------------------------------------------

    async def reader(self) -> None:
        try:
            while True:
                raw = await self.ws.receive_json()
                await self._handle_command(raw)
        except WebSocketDisconnect:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("WSConnection[%s] reader error: %s", self.id, exc)
            raise

    async def _handle_command(self, msg: dict) -> None:
        cmd = msg.get("type") or msg.get("command")
        ref = msg.get("ref", cmd)

        if cmd == "ping":
            await self._enqueue_priority({"type": "pong", "server_time": _now_iso()})
            return

        if cmd in ("subscribe", "unsubscribe"):
            service: Optional[str] = msg.get("service")
            symbols_raw = msg.get("symbols", [])

            if service not in ("LEVELONE_EQUITIES", "LEVELONE_OPTIONS"):
                await self._enqueue_priority({
                    "type": "error",
                    "code": "BAD_COMMAND",
                    "message": f"Unknown service: {service!r}. Must be LEVELONE_EQUITIES or LEVELONE_OPTIONS",
                })
                return

            if not isinstance(symbols_raw, list) or not symbols_raw:
                await self._enqueue_priority({
                    "type": "error",
                    "code": "BAD_COMMAND",
                    "message": "symbols must be a non-empty list",
                })
                return

            symbols: Set[str] = {str(s).upper() for s in symbols_raw}

            if cmd == "subscribe":
                upstream_new = stream_router.manager.add(self.id, service, symbols)
                self.subscriptions[service].update(symbols)
                if upstream_new:
                    await stream_router.apply(service, subscribe=upstream_new, unsubscribe=set())
                await self._enqueue_priority({
                    "type": "ack",
                    "ref": ref,
                    "service": service,
                    "accepted": sorted(symbols),
                    "rejected": [],
                })

            elif cmd == "unsubscribe":
                upstream_drop = stream_router.manager.remove(self.id, service, symbols)
                self.subscriptions[service].difference_update(symbols)
                if upstream_drop:
                    await stream_router.apply(service, subscribe=set(), unsubscribe=upstream_drop)
                await self._enqueue_priority({
                    "type": "ack",
                    "ref": ref,
                    "service": service,
                    "accepted": sorted(symbols),
                    "rejected": [],
                })
        else:
            await self._enqueue_priority({
                "type": "error",
                "code": "BAD_COMMAND",
                "message": f"Unknown command: {cmd!r}",
            })

    async def _enqueue_priority(self, frame: dict) -> None:
        """Enqueue ack/error/heartbeat/pong — never dropped, wait if needed."""
        await self.out.put(frame)

    # ------------------------------------------------------------------
    # Writer — drain queue → WebSocket
    # ------------------------------------------------------------------

    async def writer(self) -> None:
        try:
            while True:
                frame = await self.out.get()
                await self.ws.send_json(frame)
        except WebSocketDisconnect:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("WSConnection[%s] writer error: %s", self.id, exc)
            raise

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._enqueue_priority({"type": "heartbeat", "server_time": _now_iso()})
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@ws_router.websocket("/stream")
async def websocket_stream(ws: WebSocket) -> None:
    await ws.accept()
    conn = WSConnection(ws)
    stream_router.register(conn)

    # Send hello frame
    await conn.out.put({
        "type": "hello",
        "client_id": conn.id,
        "server_time": _now_iso(),
        "protocol": 1,
    })

    reader_task = asyncio.create_task(conn.reader(), name=f"ws-reader-{conn.id}")
    writer_task = asyncio.create_task(conn.writer(), name=f"ws-writer-{conn.id}")
    heartbeat_task = asyncio.create_task(conn.heartbeat(), name=f"ws-heartbeat-{conn.id}")

    try:
        done, pending = await asyncio.wait(
            [reader_task, writer_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        # Surface any exception for logging
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.error("WSConnection[%s] task error: %s", conn.id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("WSConnection[%s] unexpected error: %s", conn.id, exc)
    finally:
        heartbeat_task.cancel()
        stream_router.unregister(conn)
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("WSConnection[%s] closed", conn.id)
