"""Shared pytest fixtures: recorded OpenF1 responses and offline clients."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from pitwall.common.http import PoliteHttpClient, RateLimit, RateLimiter
from pitwall.ingestion.cache import ResponseCache
from pitwall.ingestion.openf1_client import OpenF1Client

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "openf1"
BASE_URL = "https://api.openf1.org/v1"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded OpenF1 response by file stem."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text("utf-8"))


def all_fixtures() -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in sorted(FIXTURE_DIR.glob("*.json"))]


class FakeClock:
    """Deterministic clock whose ``sleep`` advances time instead of blocking."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep tests independent of a developer's .env and real data folder."""
    from pitwall.common.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PITWALL_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def openf1_mock() -> Iterator[respx.MockRouter]:
    """Serve every recorded fixture at its original URL; anything else is a test error."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        for doc in all_fixtures():
            router.get(doc["url"]).mock(
                return_value=httpx.Response(doc["status_code"], json=doc["body"])
            )
        yield router


@pytest.fixture
def make_client(tmp_path: Path, fake_clock: FakeClock) -> Callable[..., OpenF1Client]:
    """Factory for an offline OpenF1Client with a fake clock and a temp cache."""

    def _make(*, cache: bool = True, max_retries: int = 2) -> OpenF1Client:
        limiter = RateLimiter([RateLimit(100, 1.0)], clock=fake_clock, sleep=fake_clock.sleep)
        http = PoliteHttpClient(
            BASE_URL,
            "PitWall-tests",
            limiter,
            max_retries=max_retries,
            sleep=fake_clock.sleep,
        )
        return OpenF1Client(http, ResponseCache(tmp_path / "cache") if cache else None)

    return _make
