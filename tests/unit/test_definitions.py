"""Tests for the Dagster definitions, run offline against recorded fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

dg = pytest.importorskip("dagster")

from pitwall.common.config import get_settings  # noqa: E402
from pitwall.orchestration.definitions import (  # noqa: E402
    SEASONS,
    OpenF1Resource,
    calendar_path,
    defs,
    openf1_season_sessions,
)


def test_definitions_load() -> None:
    assert defs.resolve_asset_graph().get_all_asset_keys() == {
        dg.AssetKey("openf1_season_sessions")
    }
    assert SEASONS == ["2023", "2024", "2025", "2026"]


def test_season_asset_writes_calendar(openf1_mock: respx.MockRouter) -> None:
    # The fixture only covers the Race listing, so mock the full listing from it.
    from tests.conftest import load_fixture

    doc = load_fixture("sessions_2026_race")
    openf1_mock.get("https://api.openf1.org/v1/sessions?year=2026").respond(200, json=doc["body"])

    result = dg.materialize(
        [openf1_season_sessions],
        partition_key="2026",
        resources={"openf1": OpenF1Resource(cache_enabled=False)},
    )
    assert result.success
    target: Path = calendar_path(get_settings(), "2026")
    written = json.loads(target.read_text("utf-8"))
    cancelled = sum(1 for s in doc["body"] if s["is_cancelled"])
    assert len(written) == len(doc["body"]) - cancelled
    assert not target.with_suffix(".tmp").exists()
