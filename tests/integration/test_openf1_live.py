"""Live checks against the real OpenF1 API. Opt in with ``PITWALL_RUN_INTEGRATION=1``.

These catch upstream schema changes that recorded fixtures cannot. They are skipped in CI.
"""

from __future__ import annotations

import os

import pytest

from pitwall.ingestion.openf1_client import Endpoint, OpenF1Client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PITWALL_RUN_INTEGRATION") != "1",
        reason="set PITWALL_RUN_INTEGRATION=1 to hit the live API",
    ),
]


def test_live_2026_calendar_and_laps() -> None:
    with OpenF1Client.from_settings() as client:
        races = client.sessions(2026, "Race")
        assert races, "OpenF1 returned no 2026 races"
        laps = client.fetch(Endpoint.LAPS, session_key=races[0].session_key, driver_number=1)
        assert laps.records
        assert {"lap_number", "lap_duration"} <= laps.records[0].keys()
