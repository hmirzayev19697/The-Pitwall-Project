"""A polite HTTP client: request budget, retries with exponential backoff, custom User-Agent.

Free community APIs (OpenF1, Jolpica) are shared by many people. This client makes sure
PitWall never hammers them:

* :class:`RateLimiter` keeps a sliding window of request times and blocks until a new
  request fits every configured budget (for example 3 per second *and* 30 per minute).
* :class:`PoliteHttpClient` retries transient failures (HTTP 429, 5xx, timeouts, dropped
  connections) with exponential backoff and full jitter, and honours ``Retry-After``.

Clock and sleep functions are injectable so tests run instantly and deterministically.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self

import httpx

from pitwall.common.logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

Clock = Callable[[], float]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class RateLimit:
    """Allow at most ``max_requests`` within any rolling ``period_s`` seconds."""

    max_requests: int
    period_s: float

    def __post_init__(self) -> None:
        if self.max_requests <= 0 or self.period_s <= 0:
            raise ValueError("max_requests and period_s must be positive")


class RateLimiter:
    """Thread-safe sliding-window limiter that enforces several budgets at once."""

    def __init__(
        self,
        limits: list[RateLimit],
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        if not limits:
            raise ValueError("at least one RateLimit is required")
        self._limits = sorted(limits, key=lambda limit: limit.period_s)
        self._history: deque[float] = deque()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def _wait_time(self, now: float) -> float:
        """Seconds to wait before one more request fits all budgets (0 if it fits now)."""
        longest = self._limits[-1].period_s
        while self._history and now - self._history[0] >= longest:
            self._history.popleft()
        wait = 0.0
        for limit in self._limits:
            in_window = [t for t in self._history if now - t < limit.period_s]
            if len(in_window) >= limit.max_requests:
                # The oldest request that must expire before we are back under budget.
                oldest = in_window[len(in_window) - limit.max_requests]
                wait = max(wait, oldest + limit.period_s - now)
        return wait

    def acquire(self) -> float:
        """Block until a request is allowed, record it and return the seconds waited."""
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                wait = self._wait_time(now)
                if wait <= 0:
                    self._history.append(now)
                    return waited
                log.debug("rate limit reached, waiting", extra={"wait_s": round(wait, 3)})
                self._sleep(wait)
                waited += wait


class HttpRequestError(RuntimeError):
    """Raised when a request still fails after every retry, or fails permanently."""

    def __init__(self, message: str, url: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


def backoff_delay(
    attempt: int,
    base_s: float,
    max_s: float,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter: uniform(0, min(max_s, base_s * 2**attempt)).

    Full jitter spreads retries from many clients apart, which avoids synchronised
    "thundering herd" retries. See https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/.
    """
    cap = min(max_s, base_s * (2**attempt))
    return (rng or random).uniform(0, cap)


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Parse a ``Retry-After`` header (seconds or HTTP date) into seconds to wait."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    reference = time.time() if now is None else now
    return max(0.0, when.timestamp() - reference)


class PoliteHttpClient:
    """Synchronous JSON-over-HTTP client with rate limiting and retries.

    Use it as a context manager so the connection pool is closed::

        with PoliteHttpClient(base_url="https://api.openf1.org/v1", ...) as client:
            response = client.get("/sessions", params={"year": 2026})
    """

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        rate_limiter: RateLimiter,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 5,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Sleep = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
        )
        self._limiter = rate_limiter
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._sleep = sleep
        self._rng = rng
        self.request_count = 0

    def build_url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """Return the absolute URL a request would use (handy as a cache key)."""
        return str(self._client.build_request("GET", path, params=params).url)

    def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        ok_statuses: frozenset[int] = frozenset(),
    ) -> httpx.Response:
        """GET ``path`` with retries.

        Args:
            path: Path relative to ``base_url`` (or an absolute URL).
            params: Query parameters, encoded by httpx.
            ok_statuses: Extra non-2xx status codes to return instead of raising, for APIs
                that use them for normal outcomes (OpenF1 answers 404 for "no results").

        Returns:
            The final successful :class:`httpx.Response`.

        Raises:
            HttpRequestError: on a non-retryable status, or once retries are exhausted.
        """
        url = self.build_url(path, params)
        last_error = "unknown error"
        last_status: int | None = None
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            self.request_count += 1
            started = time.perf_counter()
            retry_after: float | None = None
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error, last_status = f"{type(exc).__name__}: {exc}", None
            else:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                status = response.status_code
                if response.is_success or status in ok_statuses:
                    log.info(
                        "http get",
                        extra={"url": url, "status": status, "elapsed_ms": elapsed_ms},
                    )
                    return response
                last_error, last_status = f"HTTP {status}", status
                if status not in RETRYABLE_STATUS:
                    raise HttpRequestError(
                        f"GET {url} failed permanently: HTTP {status}: {response.text[:200]}",
                        url=url,
                        status_code=status,
                    )
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt == self._max_retries:
                break
            delay = backoff_delay(attempt, self._backoff_base_s, self._backoff_max_s, self._rng)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self._backoff_max_s))
            log.warning(
                "http retry",
                extra={
                    "url": url,
                    "attempt": attempt + 1,
                    "error": last_error,
                    "sleep_s": round(delay, 3),
                },
            )
            self._sleep(delay)

        raise HttpRequestError(
            f"GET {url} failed after {self._max_retries + 1} attempts: {last_error}",
            url=url,
            status_code=last_status,
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
