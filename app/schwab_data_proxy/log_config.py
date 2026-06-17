"""
Unified logging configuration for schwab-data-proxy.

Call configure_logging(level) once at module start (before uvicorn launches).
All loggers — uvicorn, uvicorn.access, and the application root — are wired
here so that every log line shares a consistent format and there is no
competing basicConfig left in the codebase.
"""

from __future__ import annotations

import logging
import logging.config


def configure_logging(level: str = "INFO") -> None:
    """Apply dictConfig-based logging across the entire process.

    Args:
        level: A logging level string such as "INFO", "DEBUG", "WARNING".
               Case-insensitive; invalid values fall back to INFO.
    """
    log_level = level.upper() if level else "INFO"
    # Guard against bad values
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        log_level = "INFO"

    config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d — %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s [%(levelname)s] %(client_addr)s "%(request_line)s" %(status_code)s',
                "datefmt": "%Y-%m-%d %H:%M:%S",
                "use_colors": False,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "standard",
            },
            "access_console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "access",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access_console"],
                "level": log_level,
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": log_level,
        },
    }

    logging.config.dictConfig(config)
