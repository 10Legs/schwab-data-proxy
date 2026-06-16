"""
schwab-data-proxy — FastAPI application entry point.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse

from .rest_proxy import router as rest_router
from .schwab_session import session
from .settings import settings
from .stream_router import stream_router
from .trader_proxy import trader_router
from .ws_server import ws_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Background tasks holder
# ---------------------------------------------------------------------------

_background_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("schwab-data-proxy starting up")

    if settings.SCHWAB_SKIP_INIT:
        logger.warning(
            "SCHWAB_SKIP_INIT=true — skipping Schwab session init (CI/test mode)"
        )
    else:
        # Initialize Schwab session (loads token — fatal on failure)
        await session.start()

        # Launch background tasks
        stream_task = asyncio.create_task(stream_router.run(), name="stream-router")
        refresh_task = asyncio.create_task(
            session.token_refresh_loop(), name="token-refresh"
        )
        _background_tasks.extend([stream_task, refresh_task])

    logger.info("schwab-data-proxy ready")
    yield

    # Shutdown
    logger.info("schwab-data-proxy shutting down")
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    logger.info("schwab-data-proxy shutdown complete")


# ---------------------------------------------------------------------------
# API key middleware
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {"/healthz", "/readyz"}

    async def dispatch(self, request: StarletteRequest, call_next):
        if not settings.PROXY_API_KEY:
            return await call_next(request)
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        key = request.headers.get("X-API-Key", "")
        if key != settings.PROXY_API_KEY:
            return StarletteJSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or missing API key",
                    }
                },
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="schwab-data-proxy",
    description="Multiplexing proxy for Schwab market data",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(APIKeyMiddleware)

# Mount routers
app.include_router(rest_router)
app.include_router(trader_router)
app.include_router(ws_router)


# ---------------------------------------------------------------------------
# Health / Readiness
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
async def healthz() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.get("/readyz", tags=["ops"])
async def readyz() -> JSONResponse:
    if not stream_router.stream_ready:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "reason": "stream not yet logged in"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})


# ---------------------------------------------------------------------------
# Entrypoint (for local dev without Docker)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "schwab_data_proxy.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        workers=1,
        log_level=settings.LOG_LEVEL.lower(),
    )
