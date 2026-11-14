"""Tests for the fixture recorder, run against the recorded responses themselves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from pitwall.ingestion import record_fixtures
from pitwall.ingestion.record_fixtures import FIXTURES, main
from tests.conftest import FIXTURE_DIR


def test_every_spec_has_a_committed_fixture() -> None:
    committed = {p.stem for p in FIXTURE_DIR.glob("*.json")}
    assert {spec.name for spec in FIXTURES} == committed


def test_main_rewrites_fixtures_and_truncates(
    tmp_path: Path, openf1_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(record_fixtures.RateLimiter, "acquire", lambda self: 0.0)
    out = tmp_path / "fixtures"
    assert main(["--out", str(out), "--max-records", "3"]) == 0
    laps = json.loads((out / "laps_aus_2026_race_d1.json").read_text("utf-8"))
    assert len(laps["body"]) == 3
    assert laps["status_code"] == 200
    radio = json.loads((out / "team_radio_aus_2026_race_d1_empty.json").read_text("utf-8"))
    assert radio["status_code"] == 404
    assert radio["body"] == {"detail": "No results found."}
