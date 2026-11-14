"""Client for the OpenF1 REST API (https://openf1.org).

Behaviour below was checked against real responses on 2026-11-11 (see
``docs/data-sources/openf1.md``), not taken from memory:

* Every endpoint returns a JSON list of flat objects.
* An empty result is **HTTP 404** with ``{"detail": "No results found."}``. That is a normal
  outcome (e.g. no team radio for a driver), so the client returns ``[]`` for it.
* An unknown endpoint gives the *same* 404, so typos would silently look like "no data".
  The client therefore only accepts endpoints from :class:`Endpoint`.
* Comparison filters are written into the raw query string: ``date>=2026-03-08T04:10:00``.
  Sending them as an encoded key/value pair (``date%3E%3D=...``) gives HTTP 500, so the
  client builds the query string itself (see :meth:`Filter.as_query`).
* No rate-limit headers are returned, so the budget is enforced client side.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from pitwall.common.config import Settings, get_settings
from pitwall.common.http import HttpRequestError, PoliteHttpClient, RateLimit, RateLimiter
from pitwall.common.logging import get_logger
from pitwall.ingestion.cache import ResponseCache

log = get_logger(__name__)

SOURCE = "openf1"
NO_RESULTS_DETAIL = "No results found."

FilterOp = Literal["=", ">", ">=", "<", "<="]
FilterValue = str | int | float | bool | datetime


class Endpoint(StrEnum):
    """OpenF1 endpoints PitWall uses. Each was confirmed to exist with a real request."""

    CAR_DATA = "car_data"
    DRIVERS = "drivers"
    INTERVALS = "intervals"
    LAPS = "laps"
    LOCATION = "location"
    MEETINGS = "meetings"
    OVERTAKES = "overtakes"
    PIT = "pit"
    POSITION = "position"
    RACE_CONTROL = "race_control"
    SESSION_RESULT = "session_result"
    SESSIONS = "sessions"
    STARTING_GRID = "starting_grid"
    STINTS = "stints"
    TEAM_RADIO = "team_radio"
    WEATHER = "weather"


@dataclass(frozen=True)
class Filter:
    """One query filter, e.g. ``Filter("date", ">=", datetime(...))``."""

    field: str
    op: FilterOp
    value: FilterValue

    def as_query(self) -> str:
        """Return this filter as an OpenF1 query fragment, e.g. ``date>=2026-03-08T04%3A10``.

        OpenF1 parses the operator out of the raw query string: ``date>=X`` means
        "date greater than or equal to X". Encoding it as a key/value pair instead
        (``date>=`` = ``X``, sent as ``date%3E%3D=X``) makes the API answer HTTP 500, so the
        operator is written literally and only the field name and value are escaped.
        """
        name = quote(self.field, safe="_")
        value = quote(_format_value(self.value), safe="-_.:")
        return f"{name}{self.op}{value}"


def _format_value(value: FilterValue) -> str:
    if isinstance(value, bool):  # bool before int: bool is a subclass of int
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_query(filters: Iterable[Filter]) -> str:
    """Build a deterministic (sorted) query string so equal requests share one cache key."""
    return "&".join(sorted(f.as_query() for f in filters))


def build_path(endpoint: Endpoint, filters: Iterable[Filter]) -> str:
    """Relative request path including the query, e.g. ``/laps?session_key=11234``."""
    query = build_query(filters)
    return f"/{endpoint.value}?{query}" if query else f"/{endpoint.value}"


@dataclass(frozen=True)
class FetchResult:
    """Records from one request plus provenance, ready to land in the bronze layer."""

    endpoint: Endpoint
    url: str
    fetched_at: datetime
    from_cache: bool
    records: list[dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)


class Session(BaseModel):
    """A row of ``/v1/sessions``. Field names match the real 2023 and 2026 payloads."""

    model_config = ConfigDict(extra="allow", frozen=True)

    session_key: int
    meeting_key: int
    session_type: str
    session_name: str
    date_start: datetime
    date_end: datetime
    year: int
    circuit_key: int
    circuit_short_name: str
    country_key: int
    country_code: str
    country_name: str
    location: str
    gmt_offset: str
    is_cancelled: bool = False


class OpenF1Error(RuntimeError):
    """OpenF1 returned something the client cannot use."""


class OpenF1Client:
    """Rate-limited, retrying, caching client for OpenF1 historical data.

    Example::

        with OpenF1Client.from_settings() as f1:
            races = f1.sessions(year=2026, session_type="Race")
            laps = f1.fetch(Endpoint.LAPS, session_key=races[0].session_key)
    """

    def __init__(self, http: PoliteHttpClient, cache: ResponseCache | None = None) -> None:
        self._http = http
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self:
        """Build a client from :class:`~pitwall.common.config.Settings`."""
        s = settings or get_settings()
        limiter = RateLimiter(
            [
                RateLimit(s.openf1_requests_per_second, 1.0),
                RateLimit(s.openf1_requests_per_minute, 60.0),
            ]
        )
        http = PoliteHttpClient(
            base_url=str(s.openf1_base_url).rstrip("/"),
            user_agent=s.user_agent,
            rate_limiter=limiter,
            timeout_s=s.http_timeout_s,
            max_retries=s.http_max_retries,
            backoff_base_s=s.http_backoff_base_s,
            backoff_max_s=s.http_backoff_max_s,
        )
        cache = ResponseCache(s.http_cache_dir) if s.openf1_cache_enabled else None
        return cls(http, cache)

    # ------------------------------------------------------------------ core
    def fetch(
        self,
        endpoint: Endpoint | str,
        filters: Sequence[Filter] = (),
        *,
        use_cache: bool = True,
        max_age_s: float | None = None,
        **equals: FilterValue,
    ) -> FetchResult:
        """Fetch one endpoint.

        Args:
            endpoint: An :class:`Endpoint` (strings are validated against it).
            filters: Comparison filters such as ``Filter("date", ">=", start)``.
            use_cache: Read from and write to the disk cache.
            max_age_s: Treat cached entries older than this as missing. ``None`` means
                cached data never expires, which is right for finished sessions.
            **equals: Equality filters, e.g. ``session_key=11234, driver_number=1``.

        Returns:
            A :class:`FetchResult`; ``records`` is empty when OpenF1 has no data.

        Raises:
            ValueError: for an endpoint OpenF1 does not have.
            OpenF1Error: if the payload is not a list of objects.
            HttpRequestError: on network or server failure after retries.
        """
        ep = Endpoint(endpoint)  # raises ValueError for unknown names
        all_filters = [*filters, *(Filter(k, "=", v) for k, v in equals.items())]
        path = build_path(ep, all_filters)
        url = self._http.build_url(path)

        if use_cache and self._cache is not None:
            hit = self._cache.get(SOURCE, url, max_age_s=max_age_s)
            if hit is not None:
                log.debug("cache hit", extra={"url": url})
                return FetchResult(ep, url, hit.fetched_at, True, _validate_records(hit.payload))

        response = self._http.get(path, ok_statuses=frozenset({404}))
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise OpenF1Error(f"GET {url} returned non-JSON: {response.text[:200]}") from exc
        if response.status_code == 404:
            if not (isinstance(payload, dict) and payload.get("detail") == NO_RESULTS_DETAIL):
                raise HttpRequestError(
                    f"GET {url} failed: HTTP 404: {response.text[:200]}", url, 404
                )
            payload = []
        records = _validate_records(payload)

        if use_cache and self._cache is not None:
            entry = self._cache.put(SOURCE, url, response.status_code, records)
            fetched_at = entry.fetched_at
        else:
            fetched_at = datetime.now(UTC)
        log.info("openf1 fetched", extra={"endpoint": ep.value, "rows": len(records)})
        return FetchResult(ep, url, fetched_at, False, records)

    # ------------------------------------------------------------------ helpers
    def sessions(
        self,
        year: int,
        session_type: str | None = None,
        *,
        include_cancelled: bool = False,
        max_age_s: float | None = 6 * 3600,
    ) -> list[Session]:
        """List a season's sessions as typed :class:`Session` objects, sorted by start.

        The calendar of the current season changes, so this listing is re-fetched after
        ``max_age_s`` (6 hours by default) instead of being cached forever.
        """
        filters = [Filter("year", "=", year)]
        if session_type:
            filters.append(Filter("session_type", "=", session_type))
        result = self.fetch(Endpoint.SESSIONS, filters, max_age_s=max_age_s)
        sessions = [Session.model_validate(r) for r in result.records]
        if not include_cancelled:
            sessions = [s for s in sessions if not s.is_cancelled]
        return sorted(sessions, key=lambda s: s.date_start)

    def car_data(
        self,
        session_key: int,
        driver_number: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> FetchResult:
        """Telemetry (~3.7 Hz) for one driver, optionally limited to ``[start, end)``.

        Requesting per driver (and per time window) keeps responses small.
        """
        filters: list[Filter] = []
        if start is not None:
            filters.append(Filter("date", ">=", start))
        if end is not None:
            filters.append(Filter("date", "<", end))
        return self.fetch(
            Endpoint.CAR_DATA, filters, session_key=session_key, driver_number=driver_number
        )

    @property
    def request_count(self) -> int:
        """Number of HTTP requests actually sent (cache hits do not count)."""
        return self._http.request_count

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _validate_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
        raise OpenF1Error(f"expected a JSON list of objects, got {type(payload).__name__}")
    return payload
