"""Tests for the rate limiter, backoff and retrying HTTP client."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx

from pitwall.common.http import (
    HttpRequestError,
    PoliteHttpClient,
    RateLimit,
    RateLimiter,
    backoff_delay,
    parse_retry_after,
)
from tests.conftest import FakeClock

URL = "https://api.example.test"


# ------------------------------------------------------------------ RateLimiter
def test_rate_limit_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        RateLimit(0, 1.0)
    with pytest.raises(ValueError):
        RateLimit(1, 0)


def test_rate_limiter_requires_a_limit() -> None:
    with pytest.raises(ValueError):
        RateLimiter([])


def test_rate_limiter_allows_burst_then_waits(fake_clock: FakeClock) -> None:
    limiter = RateLimiter([RateLimit(3, 1.0)], clock=fake_clock, sleep=fake_clock.sleep)
    waits = [limiter.acquire() for _ in range(4)]
    assert waits[:3] == [0.0, 0.0, 0.0]
    assert waits[3] == pytest.approx(1.0)


def test_rate_limiter_enforces_every_budget(fake_clock: FakeClock) -> None:
    limiter = RateLimiter(
        [RateLimit(2, 1.0), RateLimit(3, 60.0)], clock=fake_clock, sleep=fake_clock.sleep
    )
    start = fake_clock.now
    for _ in range(4):
        limiter.acquire()
    # The 4th request must wait for the first to leave the 60 s window.
    assert fake_clock.now - start == pytest.approx(60.0)


def test_rate_limiter_forgets_old_requests(fake_clock: FakeClock) -> None:
    limiter = RateLimiter([RateLimit(1, 1.0)], clock=fake_clock, sleep=fake_clock.sleep)
    limiter.acquire()
    fake_clock.now += 5
    assert limiter.acquire() == 0.0


# ------------------------------------------------------------------ backoff helpers
def test_backoff_delay_is_capped_and_jittered() -> None:
    rng = random.Random(42)
    delays = [backoff_delay(a, 1.0, 10.0, rng) for a in range(10)]
    assert all(0 <= d <= 10.0 for d in delays)
    assert backoff_delay(0, 1.0, 10.0, random.Random(1)) <= 1.0


def test_parse_retry_after_seconds_and_dates() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    now = datetime(2026, 1, 1, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(header, now=now.timestamp()) == pytest.approx(30.0)
    past = format_datetime(now - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(past, now=now.timestamp()) == 0.0


# ------------------------------------------------------------------ PoliteHttpClient
def _client(fake_clock: FakeClock, max_retries: int = 3) -> PoliteHttpClient:
    limiter = RateLimiter([RateLimit(100, 1.0)], clock=fake_clock, sleep=fake_clock.sleep)
    return PoliteHttpClient(
        URL,
        "PitWall-tests/1.0",
        limiter,
        max_retries=max_retries,
        sleep=fake_clock.sleep,
        rng=random.Random(0),
    )


@respx.mock
def test_sends_custom_user_agent(fake_clock: FakeClock) -> None:
    route = respx.get(f"{URL}/ping").mock(return_value=httpx.Response(200, json={}))
    with _client(fake_clock) as client:
        client.get("/ping")
    assert route.calls.last.request.headers["User-Agent"] == "PitWall-tests/1.0"


@respx.mock
def test_retries_429_then_succeeds_and_honours_retry_after(fake_clock: FakeClock) -> None:
    route = respx.get(f"{URL}/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "5"}),
            httpx.Response(503),
            httpx.Response(200, json=[1]),
        ]
    )
    with _client(fake_clock) as client:
        response = client.get("/x")
    assert response.json() == [1]
    assert route.call_count == 3
    assert client.request_count == 3
    assert fake_clock.sleeps[0] >= 5.0  # Retry-After wins over a smaller backoff


@respx.mock
def test_retries_transport_errors(fake_clock: FakeClock) -> None:
    respx.get(f"{URL}/x").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[])]
    )
    with _client(fake_clock) as client:
        assert client.get("/x").status_code == 200


@respx.mock
def test_gives_up_after_max_retries(fake_clock: FakeClock) -> None:
    route = respx.get(f"{URL}/x").mock(return_value=httpx.Response(500))
    with _client(fake_clock, max_retries=2) as client, pytest.raises(HttpRequestError) as err:
        client.get("/x")
    assert route.call_count == 3
    assert err.value.status_code == 500
    assert len(fake_clock.sleeps) == 2


@respx.mock
def test_does_not_retry_client_errors(fake_clock: FakeClock) -> None:
    route = respx.get(f"{URL}/x").mock(return_value=httpx.Response(400, text="bad"))
    with _client(fake_clock) as client, pytest.raises(HttpRequestError) as err:
        client.get("/x")
    assert route.call_count == 1
    assert err.value.status_code == 400


@respx.mock
def test_ok_statuses_are_returned(fake_clock: FakeClock) -> None:
    respx.get(f"{URL}/x").mock(return_value=httpx.Response(404, json={"detail": "x"}))
    with _client(fake_clock) as client:
        assert client.get("/x", ok_statuses=frozenset({404})).status_code == 404
