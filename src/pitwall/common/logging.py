"""Structured logging built on the standard library.

JSON lines in production and CI (easy to ship to any log store and to grep with ``jq``),
readable key=value lines on a laptop. Extra fields passed with ``extra={...}`` are kept
as top-level JSON keys, so ``log.info("fetched", extra={"rows": 10})`` gives
``{"msg": "fetched", "rows": 10, ...}``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from pitwall.common.config import LogFormat, LogLevel

# Attributes every LogRecord has; anything else on the record came from ``extra=``.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _RESERVED}


class JsonFormatter(logging.Formatter):
    """Render each record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record`` as one JSON line."""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly single-line format with extras appended as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record`` for a terminal."""
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        line = f"{ts} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if extras:
            line = f"{line}  {extras}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(level: LogLevel = "INFO", fmt: LogFormat = "json") -> None:
    """Configure the root logger once for the whole process.

    Calling it again replaces the previous handler, so it is safe in tests and notebooks.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # httpx logs every request at INFO; our client already logs what matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (thin wrapper so call sites stay uniform)."""
    return logging.getLogger(name)
