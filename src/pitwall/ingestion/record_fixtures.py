"""Record real OpenF1 responses as test fixtures.

Unit tests must not touch the network, but they must not use made-up payloads either. This
script fetches a fixed set of real requests (the 2026 Australian Grand Prix race, plus a
few edge cases) and saves each raw response body, so tests replay exactly what OpenF1 sends.

Long lists are cut to ``--max-records`` rows to keep the repo small; the fixture records
the original length in ``total_records``.

Run it with ``make fixtures`` (or ``uv run pitwall-record-fixtures``).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitwall.common.config import get_settings
from pitwall.common.http import PoliteHttpClient, RateLimit, RateLimiter
from pitwall.common.logging import configure_logging, get_logger
from pitwall.ingestion.openf1_client import Endpoint, Filter, build_path

log = get_logger(__name__)

# 2026 Australian Grand Prix, race (first round of the 2026 season).
AUS_2026_MEETING = 1279
AUS_2026_RACE = 11234
DRIVER = 1  # car number 1 in 2026 (Lando Norris, McLaren)
WINDOW_START = datetime(2026, 3, 8, 4, 10, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 3, 8, 4, 10, 5, tzinfo=UTC)


@dataclass(frozen=True)
class FixtureSpec:
    """One request to record, saved as ``<name>.json``."""

    name: str
    endpoint: Endpoint
    filters: tuple[Filter, ...]


def _eq(**kwargs: int | str) -> tuple[Filter, ...]:
    return tuple(Filter(k, "=", v) for k, v in kwargs.items())


FIXTURES: tuple[FixtureSpec, ...] = (
    FixtureSpec("sessions_2026_race", Endpoint.SESSIONS, _eq(year=2026, session_type="Race")),
    FixtureSpec("meetings_aus_2026", Endpoint.MEETINGS, _eq(meeting_key=AUS_2026_MEETING)),
    FixtureSpec("drivers_aus_2026_race", Endpoint.DRIVERS, _eq(session_key=AUS_2026_RACE)),
    FixtureSpec(
        "laps_aus_2026_race_d1",
        Endpoint.LAPS,
        _eq(session_key=AUS_2026_RACE, driver_number=DRIVER),
    ),
    FixtureSpec("stints_aus_2026_race", Endpoint.STINTS, _eq(session_key=AUS_2026_RACE)),
    FixtureSpec("pit_aus_2026_race", Endpoint.PIT, _eq(session_key=AUS_2026_RACE)),
    FixtureSpec(
        "race_control_aus_2026_race", Endpoint.RACE_CONTROL, _eq(session_key=AUS_2026_RACE)
    ),
    FixtureSpec("weather_aus_2026_race", Endpoint.WEATHER, _eq(session_key=AUS_2026_RACE)),
    FixtureSpec(
        "intervals_aus_2026_race_d1",
        Endpoint.INTERVALS,
        _eq(session_key=AUS_2026_RACE, driver_number=DRIVER),
    ),
    FixtureSpec(
        "position_aus_2026_race_d1",
        Endpoint.POSITION,
        _eq(session_key=AUS_2026_RACE, driver_number=DRIVER),
    ),
    FixtureSpec(
        "car_data_aus_2026_race_d1_5s",
        Endpoint.CAR_DATA,
        (
            *_eq(session_key=AUS_2026_RACE, driver_number=DRIVER),
            Filter("date", ">=", WINDOW_START),
            Filter("date", "<", WINDOW_END),
        ),
    ),
    FixtureSpec(
        "session_result_aus_2026_race", Endpoint.SESSION_RESULT, _eq(session_key=AUS_2026_RACE)
    ),
    FixtureSpec("overtakes_aus_2026_race", Endpoint.OVERTAKES, _eq(session_key=AUS_2026_RACE)),
    # Edge case: OpenF1 answers 404 + {"detail": "No results found."} for empty results.
    FixtureSpec(
        "team_radio_aus_2026_race_d1_empty",
        Endpoint.TEAM_RADIO,
        _eq(session_key=AUS_2026_RACE, driver_number=DRIVER),
    ),
)


def record(spec: FixtureSpec, http: PoliteHttpClient, max_records: int) -> dict[str, Any]:
    """Fetch ``spec`` and return the fixture document (raw body, possibly truncated)."""
    path = build_path(spec.endpoint, spec.filters)
    response = http.get(path, ok_statuses=frozenset({404}))
    body: Any = response.json()
    total = len(body) if isinstance(body, list) else None
    if isinstance(body, list):
        body = body[:max_records]
    return {
        "endpoint": spec.endpoint.value,
        "path": path,
        "url": http.build_url(path),
        "recorded_at": datetime.now(UTC).isoformat(),
        "status_code": response.status_code,
        "total_records": total,
        "body": body,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/openf1"))
    parser.add_argument("--max-records", type=int, default=25)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, "console")
    limiter = RateLimiter(
        [
            RateLimit(settings.openf1_requests_per_second, 1.0),
            RateLimit(settings.openf1_requests_per_minute, 60.0),
        ]
    )
    args.out.mkdir(parents=True, exist_ok=True)
    with PoliteHttpClient(
        str(settings.openf1_base_url).rstrip("/"), settings.user_agent, limiter
    ) as http:
        for spec in FIXTURES:
            doc = record(spec, http, args.max_records)
            target = args.out / f"{spec.name}.json"
            target.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8")
            log.info(
                "fixture recorded",
                extra={"file": str(target), "status": doc["status_code"]},
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
