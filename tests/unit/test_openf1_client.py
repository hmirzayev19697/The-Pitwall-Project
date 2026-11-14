"""Tests for OpenF1Client, replaying real responses recorded from the 2026 Australian GP."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
import respx

from pitwall.common.config import Settings
from pitwall.common.http import HttpRequestError
from pitwall.ingestion.openf1_client import (
    Endpoint,
    Filter,
    OpenF1Client,
    OpenF1Error,
    build_path,
    build_query,
)
from tests.conftest import BASE_URL, load_fixture

MakeClient = Callable[..., OpenF1Client]


# ------------------------------------------------------------------ query building
def test_comparison_filters_are_written_literally() -> None:
    f = Filter("date", ">=", datetime(2026, 3, 8, 4, 10, tzinfo=UTC))
    assert f.as_query() == "date>=2026-03-08T04:10:00%2B00:00"
    assert Filter("lap_number", "<", 5).as_query() == "lap_number<5"
    assert Filter("dnf", "=", True).as_query() == "dnf=true"


def test_query_is_sorted_so_cache_keys_are_stable() -> None:
    a = build_query([Filter("session_key", "=", 1), Filter("driver_number", "=", 44)])
    b = build_query([Filter("driver_number", "=", 44), Filter("session_key", "=", 1)])
    assert a == b == "driver_number=44&session_key=1"
    assert build_path(Endpoint.SESSIONS, []) == "/sessions"


def test_recorded_urls_match_what_the_client_builds() -> None:
    doc = load_fixture("car_data_aus_2026_race_d1_5s")
    path = build_path(
        Endpoint.CAR_DATA,
        [
            Filter("session_key", "=", 11234),
            Filter("driver_number", "=", 1),
            Filter("date", ">=", datetime(2026, 3, 8, 4, 10, 0, tzinfo=UTC)),
            Filter("date", "<", datetime(2026, 3, 8, 4, 10, 5, tzinfo=UTC)),
        ],
    )
    # httpx percent-encodes "<" and ">" on the wire; OpenF1 accepts both spellings.
    assert str(httpx.URL(f"{BASE_URL}{path}")) == doc["url"]


# ------------------------------------------------------------------ fetching
def test_fetch_laps_returns_real_fields(
    make_client: MakeClient, openf1_mock: respx.MockRouter
) -> None:
    with make_client() as client:
        result = client.fetch(Endpoint.LAPS, session_key=11234, driver_number=1)
    assert len(result) == 25  # fixture truncated to 25 of 58 laps
    lap = result.records[0]
    assert {"lap_number", "lap_duration", "duration_sector_1", "is_pit_out_lap"} <= lap.keys()
    assert result.from_cache is False
    assert result.endpoint is Endpoint.LAPS


def test_no_results_404_becomes_empty_list(
    make_client: MakeClient, openf1_mock: respx.MockRouter
) -> None:
    with make_client() as client:
        result = client.fetch(Endpoint.TEAM_RADIO, session_key=11234, driver_number=1)
    assert result.records == []


def test_unknown_endpoint_is_rejected_before_any_request(make_client: MakeClient) -> None:
    with make_client() as client, pytest.raises(ValueError):
        client.fetch("lapz", session_key=1)


def test_second_fetch_is_served_from_cache(
    make_client: MakeClient, openf1_mock: respx.MockRouter
) -> None:
    with make_client() as client:
        first = client.fetch(Endpoint.STINTS, session_key=11234)
        second = client.fetch(Endpoint.STINTS, session_key=11234)
        assert client.request_count == 1
    assert second.from_cache is True
    assert second.records == first.records
    assert second.fetched_at == first.fetched_at


def test_cache_can_be_bypassed(make_client: MakeClient, openf1_mock: respx.MockRouter) -> None:
    with make_client() as client:
        client.fetch(Endpoint.PIT, session_key=11234)
        client.fetch(Endpoint.PIT, session_key=11234, use_cache=False)
        assert client.request_count == 2


def test_works_without_a_cache(make_client: MakeClient, openf1_mock: respx.MockRouter) -> None:
    with make_client(cache=False) as client:
        client.fetch(Endpoint.WEATHER, session_key=11234)
        client.fetch(Endpoint.WEATHER, session_key=11234)
        assert client.request_count == 2


def test_car_data_window_has_2026_fields(
    make_client: MakeClient, openf1_mock: respx.MockRouter
) -> None:
    with make_client() as client:
        result = client.car_data(
            11234,
            1,
            start=datetime(2026, 3, 8, 4, 10, 0, tzinfo=UTC),
            end=datetime(2026, 3, 8, 4, 10, 5, tzinfo=UTC),
        )
    assert len(result) == 20  # ~3.7 Hz over 5 s
    sample = result.records[0]
    assert {"speed", "rpm", "n_gear", "throttle", "brake", "drs"} <= sample.keys()
    # 2026 cars have no DRS: OpenF1 sends the field but it is null.
    assert all(r["drs"] is None for r in result.records)


def test_sessions_are_typed_sorted_and_skip_cancelled(
    make_client: MakeClient, openf1_mock: respx.MockRouter
) -> None:
    with make_client() as client:
        sessions = client.sessions(2026, "Race")
        with_cancelled = client.sessions(2026, "Race", include_cancelled=True)
    raw = load_fixture("sessions_2026_race")["body"]
    cancelled = sum(1 for s in raw if s["is_cancelled"])
    assert cancelled > 0  # the real 2026 calendar has cancelled rounds
    assert len(with_cancelled) == len(raw)
    assert len(sessions) == len(raw) - cancelled
    assert sessions[0].session_key == 11234
    assert sessions[0].location == "Melbourne"
    assert [s.date_start for s in sessions] == sorted(s.date_start for s in sessions)


@respx.mock
def test_other_404s_raise(make_client: MakeClient) -> None:
    respx.get(f"{BASE_URL}/laps?session_key=1").mock(
        return_value=httpx.Response(404, json={"detail": "Not Found"})
    )
    with make_client() as client, pytest.raises(HttpRequestError):
        client.fetch(Endpoint.LAPS, session_key=1)


@respx.mock
def test_non_list_payload_raises(make_client: MakeClient) -> None:
    respx.get(f"{BASE_URL}/laps?session_key=1").mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )
    with make_client() as client, pytest.raises(OpenF1Error):
        client.fetch(Endpoint.LAPS, session_key=1)


@respx.mock
def test_non_json_payload_raises(make_client: MakeClient) -> None:
    respx.get(f"{BASE_URL}/laps?session_key=1").mock(
        return_value=httpx.Response(200, text="<html>")
    )
    with make_client() as client, pytest.raises(OpenF1Error):
        client.fetch(Endpoint.LAPS, session_key=1)


def test_from_settings_uses_configured_cache(tmp_path_factory: pytest.TempPathFactory) -> None:
    data_dir = tmp_path_factory.mktemp("data")
    with OpenF1Client.from_settings(Settings(data_dir=data_dir)) as client:
        assert client._cache is not None
        assert client._cache.root == data_dir / "http_cache"
    with OpenF1Client.from_settings(Settings(openf1_cache_enabled=False)) as client:
        assert client._cache is None
