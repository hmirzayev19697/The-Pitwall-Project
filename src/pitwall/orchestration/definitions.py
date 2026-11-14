"""Dagster entry point (``dagster dev -m pitwall.orchestration.definitions``).

No ``from __future__ import annotations`` here: Dagster inspects real type hints at runtime.

Milestone 1 ships one small, real asset so the Dagster stack has something to run end to
end: the season calendar from OpenF1, one partition per season. Milestone 2 adds the
per-session bronze assets on top of the same resource.
"""

import json
from pathlib import Path

import dagster as dg

from pitwall.common.config import Settings, get_settings
from pitwall.common.logging import configure_logging
from pitwall.ingestion.openf1_client import OpenF1Client

SEASONS = ["2023", "2024", "2025", "2026"]
season_partitions = dg.StaticPartitionsDefinition(SEASONS)


class OpenF1Resource(dg.ConfigurableResource):  # type: ignore[type-arg]
    """Builds a configured :class:`OpenF1Client` for assets."""

    cache_enabled: bool = True

    def client(self) -> OpenF1Client:
        """Return a new client; callers close it with ``with``."""
        settings = get_settings().model_copy(update={"openf1_cache_enabled": self.cache_enabled})
        return OpenF1Client.from_settings(settings)


def calendar_path(settings: Settings, season: str) -> Path:
    """Where the calendar for ``season`` is written (overwritten on each run)."""
    return settings.data_dir / "calendar" / f"season={season}" / "sessions.json"


@dg.asset(
    partitions_def=season_partitions,
    group_name="schedule",
    description="Every session of a season from OpenF1 /v1/sessions, cancelled ones excluded.",
    kinds={"python", "json"},
)
def openf1_season_sessions(
    context: dg.AssetExecutionContext, openf1: OpenF1Resource
) -> dg.MaterializeResult[None]:
    """Fetch the season calendar and write it to disk, replacing the partition (idempotent)."""
    settings = get_settings()
    season = context.partition_key
    with openf1.client() as client:
        sessions = client.sessions(year=int(season))
    target = calendar_path(settings, season)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps([s.model_dump(mode="json") for s in sessions], indent=2), "utf-8")
    tmp.replace(target)
    races = sum(1 for s in sessions if s.session_type == "Race")
    context.log.info("wrote %s sessions to %s", len(sessions), target)
    return dg.MaterializeResult(
        metadata={
            "sessions": len(sessions),
            "race_sessions": races,
            "path": dg.MetadataValue.path(str(target)),
        }
    )


configure_logging(get_settings().log_level, get_settings().log_format)

defs = dg.Definitions(
    assets=[openf1_season_sessions],
    resources={"openf1": OpenF1Resource()},
)
