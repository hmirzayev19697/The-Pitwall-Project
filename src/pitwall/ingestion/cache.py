"""Disk cache for raw API responses.

Each response is stored as one JSON file named after the SHA-256 of its full request URL,
wrapped in a small envelope with provenance (where and when it was fetched). Writes are
atomic (write to a temp file, then rename), so a crash never leaves a half-written entry.

This is the "never hit the API twice for the same thing" layer. In Milestone 2 the bronze
assets copy these payloads to object storage, partitioned by season/meeting/session.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pitwall.common.logging import get_logger

log = get_logger(__name__)


class CacheEntry(BaseModel):
    """A cached response plus its provenance."""

    url: str
    source: str
    fetched_at: datetime
    status_code: int
    payload: Any

    def age_s(self, now: datetime | None = None) -> float:
        """Seconds since the entry was fetched."""
        return ((now or datetime.now(UTC)) - self.fetched_at).total_seconds()


def cache_key(url: str) -> str:
    """Stable file-name-safe key for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class ResponseCache:
    """Content store for raw responses, sharded by source and key prefix."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, source: str, url: str) -> Path:
        key = cache_key(url)
        return self.root / source / key[:2] / f"{key}.json"

    def get(self, source: str, url: str, max_age_s: float | None = None) -> CacheEntry | None:
        """Return the cached entry for ``url``, or ``None`` if missing, stale or corrupt."""
        path = self._path(source, url)
        if not path.exists():
            return None
        try:
            entry = CacheEntry.model_validate_json(path.read_bytes())
        except ValueError:
            log.warning("corrupt cache entry ignored", extra={"path": str(path)})
            return None
        if entry.url != url:  # astronomically unlikely hash collision; be safe anyway
            return None
        if max_age_s is not None and entry.age_s() > max_age_s:
            return None
        return entry

    def put(self, source: str, url: str, status_code: int, payload: Any) -> CacheEntry:
        """Store ``payload`` atomically and return the written entry."""
        entry = CacheEntry(
            url=url,
            source=source,
            fetched_at=datetime.now(UTC),
            status_code=status_code,
            payload=payload,
        )
        path = self._path(source, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(entry.model_dump_json())
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return entry

    def clear(self, source: str | None = None) -> int:
        """Delete cached entries (for one source or all) and return how many were removed."""
        base = self.root / source if source else self.root
        removed = 0
        for file in base.rglob("*.json"):
            file.unlink()
            removed += 1
        return removed


def dumps_canonical(payload: Any) -> str:
    """Serialise JSON deterministically (sorted keys) for fixtures and hashing."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
