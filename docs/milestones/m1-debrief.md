# Milestone 1 debrief: Foundations

**Goal.** A professional repo skeleton that every later milestone builds on: uv, ruff, strict
mypy, pre-commit, CI, Docker Compose with object storage and Dagster, and an OpenF1 client with
retries, rate limiting and caching, tested against real recorded responses.

**Release:** tag `v0.1.0`. 43 unit tests pass offline, 1 live integration test passes on demand,
coverage 97.7% (CI gate: 70%).

## Changes from the spec (and why)

1. **SeaweedFS instead of MinIO for local S3.** The `minio/minio` image can no longer be pulled
   from Docker Hub (MinIO stopped shipping free community images in 2025). SeaweedFS is a mature,
   Apache-2.0, S3-compatible server. Code only uses `PITWALL_S3_*` settings, so switching to MinIO,
   R2 or S3 later is config only. Recorded in `docs/adr/0002-...md`.
2. **OpenF1 filter syntax.** Comparison filters must be written into the raw query
   (`date>=2026-03-08T04:10:00`). The usual "encode as key/value" approach sends `date%3E%3D=...`,
   and the API answers HTTP 500. The client builds the query string itself.
3. **OpenF1 has no "empty" status.** No results comes back as HTTP 404 `{"detail": "No results found."}`,
   and so does a misspelled endpoint. The client turns that 404 into `[]` and only accepts
   endpoint names from an enum.
4. **Burst limit set to 2 req/s** (plus 30 per minute). The API sends no rate-limit headers, and
   3 req/s still triggered an HTTP 429 while recording fixtures. Both limits are configurable.

## What was built

| File | What it does |
| --- | --- |
| `pyproject.toml`, `uv.lock`, `.python-version` | Project metadata and pinned dependencies (uv). Ruff, strict mypy, pytest and coverage settings. Dagster is the `orchestration` extra |
| `Makefile` | `make install / lint / format / typecheck / test / check / fixtures / up / down / dagster` |
| `.pre-commit-config.yaml` | Whitespace, YAML/TOML, large-file and private-key checks, then ruff and mypy from the project venv |
| `.env.example` | Every setting with its default; copy to `.env` (git-ignored) |
| `src/pitwall/common/config.py` | `Settings` (pydantic-settings): typed, validated `PITWALL_*` env vars; secrets are `SecretStr` |
| `src/pitwall/common/logging.py` | JSON-lines or console logging; `extra={...}` fields become JSON keys |
| `src/pitwall/common/http.py` | `RateLimiter` (sliding window, several budgets at once), `backoff_delay` (exponential + full jitter), `parse_retry_after`, and `PoliteHttpClient` (custom User-Agent, retries on 429/5xx/network errors, honours `Retry-After`) |
| `src/pitwall/ingestion/cache.py` | `ResponseCache`: one JSON file per request URL (SHA-256 key) with URL, source, fetch time and status; atomic writes; optional max age |
| `src/pitwall/ingestion/openf1_client.py` | `OpenF1Client`: `Endpoint` enum, `Filter` for `=`/`<`/`>=` etc., cache-first `fetch()`, typed `sessions()`, windowed `car_data()`. Returns `FetchResult` with provenance for bronze |
| `src/pitwall/ingestion/record_fixtures.py` | `make fixtures`: re-records 14 real requests (2026 Australian GP race + an empty-result case) into `tests/fixtures/openf1/` |
| `src/pitwall/orchestration/definitions.py` | Dagster `Definitions`: `OpenF1Resource` and a season-partitioned `openf1_season_sessions` asset that writes the calendar (idempotent overwrite) |
| `docker/Dockerfile`, `docker/dagster/*.yaml` | App image (Python 3.12 slim, uv, non-root user); Dagster instance (SQLite in a volume, max 2 concurrent runs) and workspace |
| `docker-compose.yml` | `s3` (SeaweedFS on :9000), `s3-init` (creates the bronze bucket, then exits), `dagster-webserver` (:3000), `dagster-daemon` |
| `.github/workflows/ci.yml` | Lint, type-check, tests with coverage, then Docker image build and compose validation |
| `tests/` | Unit tests for limiter, retries, cache, settings, logging, client, recorder and the Dagster asset; opt-in live test |
| `docs/adr/0001`, `0002`, `docs/data-sources/openf1.md` | Decisions and verified API behaviour |

## How it fits together

```text
Dagster asset / your code
   └─ OpenF1Client.fetch(Endpoint.LAPS, session_key=11234)
        ├─ build sorted query  ->  URL  (the cache key)
        ├─ ResponseCache.get(url) ── hit ──> FetchResult(from_cache=True)
        └─ miss ─> PoliteHttpClient.get
                     ├─ RateLimiter.acquire()   (wait until under 2/s and 30/min)
                     ├─ httpx GET with User-Agent
                     ├─ 429 / 5xx / network error -> sleep(backoff or Retry-After), retry
                     └─ 200, or 404 "No results"  -> []
                   ResponseCache.put(url, records)  -> FetchResult(from_cache=False)
```

In Milestone 2, bronze assets will take each `FetchResult` (records + `url` + `fetched_at`) and
write it to S3 under `season/meeting_key/session_key/endpoint`, adding `_ingested_at`, `_source`
and `_request_url`.

## Design decisions

- **httpx + own retry loop** rather than `requests` + `tenacity`: httpx has a pluggable transport
  (clean mocking with respx) and async support for later; the retry logic is ~40 lines, fully
  tested, and one less dependency.
- **Client-side rate limiter**, because OpenF1 sends no limit headers. Sliding window over a
  token bucket because it enforces "N per period" exactly, and it is easy to reason about.
- **Full-jitter backoff**: spreads out retries so many clients do not retry in lock-step.
- **Cache keyed on the exact URL** with sorted parameters: the same logical request always maps to
  one file. Finished sessions never change, so entries never expire by default; the current
  season's calendar is refreshed after 6 hours.
- **Recorded fixtures, not hand-written mocks**: tests replay what OpenF1 really sends (including
  the 404 quirk and the null 2026 `drs` field). Rejected: VCR-style cassettes, which hide the data
  in YAML and couple tests to request order.
- **stdlib logging with a JSON formatter** rather than structlog: same output, zero dependencies.
- **Dagster as an optional extra**: it is large, and the client library does not need it.
- **SQLite Dagster storage** in a shared volume: fine for one laptop; Postgres is a config swap.

## Concepts to understand

1. **Idempotency**: running the same job twice gives the same result, no duplicates. The calendar
   asset overwrites its partition atomically. [Dagster partitions](https://docs.dagster.io/guides/build/partitions-and-backfills)
2. **Exponential backoff with jitter**: wait 1, 2, 4, 8 s (randomised) between retries so a
   struggling server can recover. [AWS Architecture Blog](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
3. **Rate limiting (sliding window)**: remember recent request times; block while the window is
   full. [Cloudflare explainer](https://www.cloudflare.com/learning/bots/what-is-rate-limiting/)
4. **Software-defined assets**: describe the data you want (an asset), not the steps; the
   orchestrator tracks lineage and freshness. [Dagster assets](https://docs.dagster.io/guides/build/assets)
5. **Configuration as environment variables (12-factor)**: one codebase, behaviour set by env, no
   secrets in git. [The Twelve-Factor App: Config](https://12factor.net/config)

## How to run and test it

```bash
cd pitwall
make install          # uv sync --all-extras + pre-commit install
make check            # expect: "All checks passed!", "Success: no issues found in 11 source files",
                      #         "43 passed, 1 skipped", "Total coverage: 97.66%"
make test-integration # live OpenF1 call; expect "1 passed"
make up               # builds the image, then: Dagster at http://localhost:3000
docker compose ps -a  # s3 (healthy), s3-init Exited (0), dagster-webserver (healthy), dagster-daemon Up
docker compose logs s3-init   # "bucket pitwall-bronze ready"
```

In the Dagster UI open **Assets → openf1_season_sessions → Materialize**, pick partition `2026`.
The run succeeds and writes `/app/data/calendar/season=2026/sessions.json` (121 sessions:
testing, practice, qualifying, sprints and races, with the cancelled Bahrain and Saudi rounds
removed). Headless equivalent:

```bash
docker compose exec dagster-webserver \
  dagster asset materialize -m pitwall.orchestration.definitions \
  --select openf1_season_sessions --partition 2026
```

Python quick check:

```python
from pitwall.ingestion.openf1_client import Endpoint, OpenF1Client
with OpenF1Client.from_settings() as f1:
    race = f1.sessions(2026, "Race")[0]                     # Melbourne, session_key 11234
    laps = f1.fetch(Endpoint.LAPS, session_key=race.session_key, driver_number=1)
    print(len(laps), laps.from_cache)                       # 58 False, then 58 True on re-run
```

## Interview questions

**1. Why build a rate limiter on the client when the API could just return 429s?**
Because 429s are the server doing damage control: by then you have already overloaded a free,
shared service and you waste retries. OpenF1 publishes no limit headers, so the client enforces a
conservative budget (2/s and 30/min) itself, and keeps retry-on-429 with `Retry-After` as a
safety net. The limiter supports several windows at once because short bursts and sustained
volume are separate limits.

**2. How do you test HTTP code without hitting the network, and how do you know the mocks are realistic?**
The tests replay real responses recorded by `make fixtures`, served by respx at their original
URLs, with `assert_all_mocked=True` so any unexpected request fails the test. A fake clock makes
rate-limit and backoff tests instant and deterministic. A separate opt-in integration test hits
the live API to catch upstream schema drift that fixtures cannot.

**3. What makes the pipeline idempotent, and why does it matter for backfills?**
Each asset writes a whole partition (season or session) to a deterministic path with an atomic
replace, never an append. Re-running yields the same files and no duplicates, so a failed or
partial backfill can simply be re-run. The raw cache adds a second guarantee: a re-run re-reads
the stored response instead of calling the API, so results are reproducible even if the API changes.

## README update

> **Foundations (v0.1.0).** PitWall talks to shared, free community APIs, so the first thing built
> is a client that is polite by design: a sliding-window request budget, retries with exponential
> backoff and jitter that honour `Retry-After`, a custom User-Agent, and a disk cache that stores
> every raw response with its URL and fetch time so no request is ever repeated. The OpenF1 client
> was written against real responses, which surfaced three quirks it now handles: empty results
> arrive as HTTP 404, unknown endpoints look identical to empty ones, and comparison filters must
> be written literally into the query. Tests replay real 2026 Australian GP responses offline;
> `make up` starts S3-compatible storage and Dagster with a partitioned season-calendar asset.

(Already added to `README.md`.)
