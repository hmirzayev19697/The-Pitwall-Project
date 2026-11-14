"""Tests for the response cache, settings and structured logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pitwall.common.config import Settings, get_settings
from pitwall.common.logging import ConsoleFormatter, JsonFormatter, configure_logging
from pitwall.ingestion.cache import ResponseCache, cache_key, dumps_canonical


# ------------------------------------------------------------------ cache
def test_cache_round_trip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put("openf1", "https://x/laps?a=1", 200, [{"lap": 1}])
    hit = cache.get("openf1", "https://x/laps?a=1")
    assert hit is not None
    assert hit.payload == [{"lap": 1}]
    assert hit.status_code == 200
    assert cache.get("openf1", "https://x/laps?a=2") is None


def test_cache_respects_max_age(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    entry = cache.put("openf1", "u", 200, [])
    assert cache.get("openf1", "u", max_age_s=60) is not None
    assert entry.age_s(datetime.now(UTC) + timedelta(hours=1)) > 3500
    assert cache.get("openf1", "u", max_age_s=-1) is None


def test_corrupt_entry_is_ignored(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put("openf1", "u", 200, [])
    key = cache_key("u")
    (tmp_path / "openf1" / key[:2] / f"{key}.json").write_text("{not json", "utf-8")
    assert cache.get("openf1", "u") is None


def test_cache_leaves_no_temp_files_and_clears(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put("openf1", "a", 200, [])
    cache.put("jolpica", "b", 200, [])
    assert not list(tmp_path.rglob("*.tmp"))
    assert cache.clear("openf1") == 1
    assert cache.clear() == 1


def test_dumps_canonical_is_deterministic() -> None:
    assert dumps_canonical({"b": 1, "a": 2}) == dumps_canonical({"a": 2, "b": 1})


# ------------------------------------------------------------------ settings
def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_OPENF1_REQUESTS_PER_MINUTE", "12")
    monkeypatch.setenv("PITWALL_S3_SECRET_ACCESS_KEY", "s3cret")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.openf1_requests_per_minute == 12
    assert settings.s3_secret_access_key.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)  # secrets never leak into logs
    assert settings.http_cache_dir == settings.data_dir / "http_cache"


def test_settings_validate_values() -> None:
    with pytest.raises(ValueError):
        Settings(openf1_requests_per_minute=0)


# ------------------------------------------------------------------ logging
def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("pitwall.test", logging.INFO, __file__, 1, "hello %s", ("F1",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_extras() -> None:
    line = JsonFormatter().format(_record(rows=10))
    payload = json.loads(line)
    assert payload["msg"] == "hello F1"
    assert payload["rows"] == 10
    assert payload["level"] == "INFO"


def test_json_formatter_includes_exceptions() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "boom" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_console_formatter_appends_extras() -> None:
    line = ConsoleFormatter().format(_record(url="https://x"))
    assert "hello F1" in line
    assert "url=https://x" in line


def test_configure_logging_replaces_handlers() -> None:
    configure_logging("DEBUG", "console")
    configure_logging("WARNING", "json")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING
