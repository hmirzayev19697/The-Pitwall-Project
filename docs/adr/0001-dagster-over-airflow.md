# ADR 0001: Dagster over Airflow for orchestration

- Status: accepted
- Date: 2026-11-11

## Context

PitWall's pipeline is naturally organised around **data assets** (a session's laps, its stints,
a gold mart) rather than around tasks. Every asset is partitioned by session, must be
re-runnable without duplicating data, and needs backfills over four seasons. It has to run on
a laptop and headless in a GitHub Actions cron.

## Decision

Use Dagster software-defined assets with partitions.

## Consequences

- Lineage from bronze to gold is visible in the UI without extra tooling.
- Partitions and backfills are first-class: "re-run the 2026 Las Vegas race" is one click or
  one CLI call, and assets overwrite their partition, which keeps runs idempotent.
- Assets are plain Python functions, so they are unit-testable with `dagster.materialize`
  (see `tests/unit/test_definitions.py`).
- `dagster asset materialize` runs jobs headlessly, which fits the free GitHub Actions cron.
- Cost: Dagster is a large dependency, so it lives in the `orchestration` extra.

## Alternatives considered

- **Apache Airflow**: the industry default, but task-centric; partitions and asset lineage
  need extra conventions, and a local install is heavier (scheduler, webserver, metadata DB).
- **Prefect**: pleasant API, but weaker asset/partition model for this use case.
- **Cron + scripts**: no lineage, retries or backfill tooling.
