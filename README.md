# PitWall

**F1 2026 strategy and telemetry platform.** PitWall ingests 2023–2026 Formula 1 session data
from free public APIs, models it in a bronze → silver → gold lakehouse, predicts tyre
degradation and pit windows, simulates races, and measures how the 2026 regulations
(active aero, Overtake Mode, ~50/50 electric power) changed racing.

> Unofficial project, not affiliated with Formula 1. No F1 logos or trademarks are used.

![CI](https://img.shields.io/badge/CI-GitHub_Actions-blue) ![Python](https://img.shields.io/badge/python-3.12-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Status

| Milestone | Scope | State |
| --- | --- | --- |
| M1 Foundations | Repo scaffold, tooling, CI, Docker Compose (S3 storage + Dagster), OpenF1 client with retries, rate limit and cache, recorded fixtures | ✅ done |
| M2 Bronze ingestion | All endpoints for one race land in bronze; FastF1 loader; 2026 backfill | next |
| M3 – M10 | Silver/gold dbt models, quality suite, ML models, API, dashboard, publish | planned |

## Architecture (target)

```text
  OpenF1 API     FastF1     Jolpica-F1
       \           |          /
        v          v         v
   [ Ingestion: Python extractors (Dagster assets, partitioned by session) ]
                   |
   BRONZE  raw JSON / Parquet in object storage (SeaweedFS S3 locally, R2 / S3 in cloud)
                   |
   SILVER  cleaned, typed, deduplicated tables (dbt on DuckDB)
                   |
   GOLD    analytics marts + ML feature tables (dbt)
             |                |                    |
     [ ML + MLflow ]   [ FastAPI REST API ]   [ Streamlit dashboard ]
```

## Foundations (Milestone 1)

PitWall talks to shared, free community APIs, so the first thing built is a client that is
polite by design. `PoliteHttpClient` enforces a sliding-window request budget (2 requests per
second and 30 per minute by default), retries HTTP 429, 5xx and network errors with
exponential backoff and full jitter, honours `Retry-After`, and identifies itself with a
custom User-Agent. `OpenF1Client` sits on top: it validates endpoint names, builds
deterministic queries (including comparison filters such as `date>=`), treats OpenF1's
"404 No results found" as an empty result, and caches every raw response on disk with its
URL and fetch time, so the same request never hits the API twice. Unit tests replay real
OpenF1 responses recorded from the 2026 Australian Grand Prix, so CI never touches the
network. `make up` starts S3-compatible object storage for the bronze layer (SeaweedFS, since
MinIO no longer ships free Docker images; see [ADR 0002](docs/adr/0002-seaweedfs-for-local-object-storage.md))
and Dagster, which already runs a partitioned `openf1_season_sessions` asset.

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/), Docker with Compose v2, GNU Make.

```bash
cp .env.example .env        # local settings; never commit .env
make install                # uv sync + pre-commit hooks
make check                  # lint + type-check + tests
make up                     # Dagster on :3000, S3 API on :9000
make down                   # stop the stack
```

Fetch something real from Python:

```python
from pitwall.ingestion.openf1_client import Endpoint, OpenF1Client

with OpenF1Client.from_settings() as f1:
    races = f1.sessions(year=2026, session_type="Race")
    laps = f1.fetch(Endpoint.LAPS, session_key=races[0].session_key, driver_number=1)
    print(races[0].location, len(laps), "laps")
```

## Repository layout

```text
src/pitwall/
  common/          config (pydantic-settings), structured logging, polite HTTP client
  ingestion/       OpenF1 client, raw response cache, fixture recorder
  orchestration/   Dagster definitions (assets, resources)
tests/
  unit/            fast tests, no network, replay recorded fixtures
  integration/     opt-in tests against the live API (PITWALL_RUN_INTEGRATION=1)
  fixtures/openf1/ real OpenF1 responses (2026 Australian GP)
docker/            Dockerfile and Dagster instance config
docs/              ADRs, data-source notes, milestone debriefs
```

## Data sources

- [OpenF1](https://openf1.org) for sessions, laps, stints, pits, intervals, race control and
  ~3.7 Hz car telemetry. Free for historical data. See [docs/data-sources/openf1.md](docs/data-sources/openf1.md)
  for behaviour verified against real responses.
- [FastF1](https://github.com/theOehrly/Fast-F1) and [Jolpica-F1](https://github.com/jolpica/jolpica-f1)
  arrive in Milestone 2.

## License

MIT. Data belongs to its respective providers; respect their terms and rate limits.
