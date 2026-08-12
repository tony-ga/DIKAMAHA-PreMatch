"""Pruebas raw-first del follower ESPN live."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.espn_live_follower import (
    EspnLiveMatchFollower,
    InMemoryLiveRawStore,
    _boxscore_aggregate,
    live_inference_payload,
)
from src.espn_prospective_connector import (
    EspnConnectorError,
    EspnFetchResult,
    EspnRequest,
)


NOW = datetime(2025, 1, 10, 21, 0, tzinfo=timezone.utc)


def _result(payload: dict[str, object]) -> EspnFetchResult:
    return EspnFetchResult(payload, 200, NOW, False)


class _Connector:
    """Conector determinista que exige bypass de caché live."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(league="esp.1")
        self.fresh_flags: list[bool] = []

    def scoreboard_fetch_result(self, _date: str, *, use_cache: bool = True) -> EspnFetchResult:
        self.fresh_flags.append(use_cache)
        return _result({"events": [{
            "id": "900001",
            "date": "2025-01-10T20:00:00Z",
            "competitions": [{
                "id": "900001",
                "competitors": [
                    {"homeAway": "home", "score": "1", "team": {"id": "1"}},
                    {"homeAway": "away", "score": "0", "team": {"id": "2"}},
                ],
                "status": {"period": 2, "clock": {"value": 3600}, "type": {"state": "in", "detail": "60'"}},
            }],
        }]})

    def event_fetch_result(self, _event_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        self.fresh_flags.append(use_cache)
        return _result({"id": "900001"})

    def plays_fetch_result(self, _event_id: str, _competition_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        self.fresh_flags.append(use_cache)
        return _result({"items": [{
            "id": "p1",
            "type": {"type": "goal"},
            "scoringPlay": True,
            "clock": {"value": 600},
            "period": {"number": 1},
            "team": {"id": "1"},
            "text": "Goal",
        }]})

    @staticmethod
    def resource_request(_resource: str, **_identifiers: str) -> EspnRequest:
        return EspnRequest("situation", "https://sports.core.api.espn.com/v2/situation", {"limit": 100})

    def fetch_request_result(self, _request: EspnRequest, *, use_cache: bool = True) -> EspnFetchResult:
        self.fresh_flags.append(use_cache)
        return _result({"possession": "home"})

    def summary_fetch_result(self, _event_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        self.fresh_flags.append(use_cache)
        return _result({"boxscore": {"teams": [
            {"team": {"id": "1"}, "statistics": [
                {"name": "totalShots", "displayValue": "5"},
                {"name": "shotsOnTarget", "displayValue": "2"},
                {"name": "wonCorners", "displayValue": "3"},
            ]},
            {"team": {"id": "2"}, "statistics": [
                {"name": "totalShots", "displayValue": "3"},
                {"name": "shotsOnTarget", "displayValue": "1"},
                {"name": "wonCorners", "displayValue": "1"},
            ]},
        ]}})


def test_poll_is_fresh_raw_first_and_builds_inference_payload() -> None:
    connector = _Connector()
    store = InMemoryLiveRawStore()
    snapshots = EspnLiveMatchFollower(connector, store).poll_once("20250110")
    assert len(snapshots) == 1
    assert len(store.rows) == 5
    assert connector.fresh_flags == [False, False, False, False, False]
    snapshot = snapshots[0]
    assert snapshot["contract_version"] == "live_event_stream_v1"
    assert snapshot["match_clock_seconds"] == 3600.0
    assert snapshot["score_home"] == 1
    assert snapshot["events"][0]["event_type"] == "goal"
    assert len(snapshot["raw_receipts"]) == 5
    assert snapshot["boxscore_aggregate"] == {
        "home": {"shots": 5, "shots_on_target": 2, "corners": 3, "shots_off_target": 3},
        "away": {"shots": 3, "shots_on_target": 1, "corners": 1, "shots_off_target": 2},
    }
    payload = live_inference_payload(snapshot, lambda_base_home=1.5, lambda_base_away=1.1)
    assert payload["markov_live_enabled"] is True
    assert payload["hawkes_enabled"] is True
    assert payload["official_prediction"] is False


def test_inactive_fixture_is_not_polled() -> None:
    connector = _Connector()
    original = connector.scoreboard_fetch_result

    def scoreboard(date: str, *, use_cache: bool = True) -> EspnFetchResult:
        result = original(date, use_cache=use_cache)
        result.payload["events"][0]["competitions"][0]["status"]["type"]["state"] = "pre"
        return result

    connector.scoreboard_fetch_result = scoreboard  # type: ignore[method-assign]
    store = InMemoryLiveRawStore()
    assert EspnLiveMatchFollower(connector, store).poll_once("20250110") == []
    assert len(store.rows) == 1


def test_one_broken_fixture_does_not_block_other_active_matches() -> None:
    connector = _Connector()
    original_scoreboard = connector.scoreboard_fetch_result
    original_event = connector.event_fetch_result

    def scoreboard(date: str, *, use_cache: bool = True) -> EspnFetchResult:
        result = original_scoreboard(date, use_cache=use_cache)
        broken = dict(result.payload["events"][0])
        broken["id"] = "900000"
        broken["competitions"] = [dict(broken["competitions"][0])]
        broken["competitions"][0]["id"] = "900000"
        result.payload["events"] = [broken, result.payload["events"][0]]
        return result

    def event(event_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        if event_id == "900000":
            raise ValueError("malformed_event")
        return original_event(event_id, use_cache=use_cache)

    connector.scoreboard_fetch_result = scoreboard  # type: ignore[method-assign]
    connector.event_fetch_result = event  # type: ignore[method-assign]
    follower = EspnLiveMatchFollower(connector, InMemoryLiveRawStore())

    snapshots = follower.poll_once("20250110")

    assert [row["provider_event_id"] for row in snapshots] == ["900001"]
    assert follower.last_errors == [{
        "provider_event_id": "900000",
        "error": "malformed_event",
    }]


def test_repeated_poll_rejects_clock_regression_after_raw_capture() -> None:
    connector = _Connector()
    original = connector.scoreboard_fetch_result
    calls = 0

    def scoreboard(date: str, *, use_cache: bool = True) -> EspnFetchResult:
        nonlocal calls
        calls += 1
        result = original(date, use_cache=use_cache)
        if calls == 2:
            result.payload["events"][0]["competitions"][0]["status"]["clock"]["value"] = 3500
        return result

    connector.scoreboard_fetch_result = scoreboard  # type: ignore[method-assign]
    store = InMemoryLiveRawStore()
    follower = EspnLiveMatchFollower(connector, store)

    assert len(follower.poll_once("20250110")) == 1
    assert follower.poll_once("20250110") == []
    assert follower.last_errors == [{
        "provider_event_id": "900001",
        "error": "live_snapshot_clock_regression",
    }]
    assert len(store.rows) == 10


def test_stoppage_display_clock_overrides_capped_numeric_clock() -> None:
    """ESPN conserva 5400 en `value` y publica 90'+N' en el display."""

    connector = _Connector()
    original_scoreboard = connector.scoreboard_fetch_result
    original_plays = connector.plays_fetch_result

    def scoreboard(date: str, *, use_cache: bool = True) -> EspnFetchResult:
        result = original_scoreboard(date, use_cache=use_cache)
        status = result.payload["events"][0]["competitions"][0]["status"]
        status["clock"] = 5400.0
        status["displayClock"] = "90'+8'"
        return result

    def plays(
        event_id: str, competition_id: str, *, use_cache: bool = True,
    ) -> EspnFetchResult:
        result = original_plays(event_id, competition_id, use_cache=use_cache)
        result.payload["items"][0]["clock"] = {
            "value": 5400.0, "displayValue": "90'+4'",
        }
        result.payload["items"][0]["period"] = {"number": 2}
        return result

    connector.scoreboard_fetch_result = scoreboard  # type: ignore[method-assign]
    connector.plays_fetch_result = plays  # type: ignore[method-assign]

    snapshot = EspnLiveMatchFollower(
        connector, InMemoryLiveRawStore(),
    ).poll_once("20250110")[0]

    assert snapshot["match_clock_seconds"] == 98 * 60
    assert snapshot["events"][0]["match_clock_seconds"] == 94 * 60


def test_boxscore_aggregate_maps_espn_fields_and_derives_shots_off_target() -> None:
    summary = {"boxscore": {"teams": [
        {"team": {"id": "160"}, "statistics": [
            {"name": "totalShots", "displayValue": "7"},
            {"name": "shotsOnTarget", "displayValue": "2"},
            {"name": "blockedShots", "displayValue": "1"},
            {"name": "wonCorners", "displayValue": "0"},
            {"name": "foulsCommitted", "displayValue": "7"},
            {"name": "possessionPct", "displayValue": "68.3"},
        ]},
        {"team": {"id": "362"}, "statistics": [
            {"name": "totalShots", "displayValue": "11"},
            {"name": "shotsOnTarget", "displayValue": "3"},
            {"name": "wonCorners", "displayValue": "2"},
        ]},
    ]}}

    result = _boxscore_aggregate(summary, home_id=160, away_id=362)

    assert result["home"]["shots"] == 7
    assert result["home"]["shots_on_target"] == 2
    assert result["home"]["shots_off_target"] == 4, "7 - 2 en puerta - 1 bloqueado"
    assert result["home"]["fouls"] == 7
    assert "possessionPct" not in result["home"], "sólo se mapean los campos declarados"
    assert result["away"]["corners"] == 2
    assert result["away"]["shots_off_target"] == 8, "sin blockedShots publicado, se asume 0"


def test_boxscore_aggregate_ignores_a_team_not_in_the_fixture() -> None:
    """Un equipo que no coincide con home/away no debe filtrarse a ningún lado."""

    summary = {"boxscore": {"teams": [
        {"team": {"id": "160"}, "statistics": [{"name": "totalShots", "displayValue": "7"}]},
        {"team": {"id": "999"}, "statistics": [{"name": "totalShots", "displayValue": "3"}]},
    ]}}

    assert _boxscore_aggregate(summary, home_id=160, away_id=362) is None


def test_boxscore_aggregate_degrades_on_missing_or_malformed_summary() -> None:
    assert _boxscore_aggregate(None, home_id=160, away_id=362) is None
    assert _boxscore_aggregate({}, home_id=160, away_id=362) is None
    assert _boxscore_aggregate({"boxscore": {}}, home_id=160, away_id=362) is None
    assert _boxscore_aggregate(
        {"boxscore": {"teams": "not-a-list"}}, home_id=160, away_id=362,
    ) is None


def test_poll_degrades_gracefully_when_summary_fetch_fails() -> None:
    """El boxscore es un enriquecimiento opcional: su fallo no debe bloquear el poll."""

    connector = _Connector()

    def failing_summary(_event_id: str, *, use_cache: bool = True) -> EspnFetchResult:
        raise EspnConnectorError("summary_unavailable")

    connector.summary_fetch_result = failing_summary  # type: ignore[method-assign]
    store = InMemoryLiveRawStore()

    snapshots = EspnLiveMatchFollower(connector, store).poll_once("20250110")

    assert len(snapshots) == 1
    assert snapshots[0]["boxscore_aggregate"] is None
    assert len(store.rows) == 4, "sin el receipt de summary, que nunca se guarda si falla"
