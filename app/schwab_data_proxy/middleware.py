"""
HTTP request-logging middleware for schwab-data-proxy.

Emits ONE structured log line per request via the application logger so the
standard formatter captures the real source file and line number from this
module (not from uvicorn internals).

Line format (via the standard formatter):
  YYYY-MM-DD HH:MM:SS [INFO] schwab_data_proxy.middleware middleware.py:NN —
  host:port METHOD /path → status_code  duration_ms ms
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with client address, method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000

        client = request.client
        client_addr = f"{client.host}:{client.port}" if client else "unknown"

        logger.info(
            "%s %s %s → %d  %.1f ms",
            client_addr,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        return response
