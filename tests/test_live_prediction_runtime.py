"""Regresiones del runtime de producto Markov Live + Hawkes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import src.live_prediction_runtime as live_runtime
from src.dikamaha_inference import DikamahaInferenceEngine
from src.live_prediction_runtime import (
    LivePredictionRuntime,
    _candidate_live_dates,
    _match_dynamics,
    _observed_live_presentation,
    predict_shadow_snapshot,
)
from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput


def _historical_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(10):
        match_id = 1000 + index
        date = f"2025-{index + 1:02d}-01T12:00:00+00:00"
        rows.extend([
            {"match_id": match_id, "match_date": date,
             "league_slug": "esp.1", "is_home": True,
             "team_id": 1 if index % 2 == 0 else 3, "goals": index % 3},
            {"match_id": match_id, "match_date": date,
             "league_slug": "esp.1", "is_home": False,
             "team_id": 2 if index % 2 == 0 else 4, "goals": (index + 1) % 2},
        ])
    return rows


def test_live_prior_reconstruction_is_strict_causal_and_reproducible(tmp_path) -> None:
    """Permite kickoff pasado sin usar datos del partido objetivo."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    engine = UniversalPrematchEngine(
        windows, team_markets_enabled=False,
        official_goal_chain_enabled=False,
    )
    request = UpcomingMatchInput(
        "esp.1", 1, 2, "2026-01-15T20:00:00+00:00", 9999,
    )

    first = engine.reconstruct_live_prior(request)
    second = engine.reconstruct_live_prior(request)

    assert first == second
    assert first["status"] == "reconstructed_causal_prematch_prior"
    assert datetime.fromisoformat(first["cutoff_ts"]) < datetime.fromisoformat(
        first["kickoff_ts"])
    assert first["audit"]["target_match_data_used"] is False
    assert first["audit"]["cutoff_strictly_before_kickoff"] is True
    assert len(first["source_hash"]) == 64


def _snapshot() -> dict[str, object]:
    return {
        "contract_version": "live_event_stream_v1",
        "provider_event_id": "900001", "home_team_id": 1,
        "away_team_id": 2, "kickoff_ts": "2026-08-08T20:00:00+00:00",
        "source_fetched_at": "2026-08-08T20:10:00+00:00",
        "league_slug": "esp.1", "competition_id": "900001",
        "period": 1, "match_clock_seconds": 600.0,
        "score_home": 0, "score_away": 0, "events": [],
        "source_hash": "live-source",
    }


def _prior() -> dict[str, object]:
    return {
        "provider_event_id": "900001", "home_team_id": 1,
        "away_team_id": 2, "league_slug": "esp.1",
        "cutoff_ts": "2026-08-01T20:00:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "source_hash": "prior-source",
    }


def test_non_admitted_hawkes_is_exact_markov_complement() -> None:
    """Fuera de allowlist Hawkes no compite ni altera mercados Markov."""

    policy = {
        "allowed_leagues": ["eng.1"], "rho_goal": 1.0,
        "rho_next_event": 0.0,
    }
    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(), policy)

    assert result["hawkes_league_admission"]["admitted"] is False
    assert result["hawkes_league_admission"][
        "fallback_exact_markov_live"] is True
    assert result["experimental_combined_live"]["markets"] == (
        result["experimental_markov_live"]["markets"])


class _BrokenProbabilityEngine:
    def predict(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        """Simula un fallo matemático interno sin filtrar payloads."""

        raise FloatingPointError("synthetic_engine_failure")


def test_official_live_engine_falls_back_to_markov_on_internal_error() -> None:
    """Conserva una salida oficial usable si falla el motor compuesto."""

    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        {
            "allowed_leagues": ["esp.1"], "rho_goal": 1.0,
            "rho_next_event": 0.0,
        },
        probability_engine=_BrokenProbabilityEngine(),
    )

    assert result["official_source"] == "markov_live_v1_fallback"
    assert result["official_live_prediction"]["fallback"]["applied"] is True
    assert result["official_live_prediction"]["markets"] == (
        result["experimental_markov_live"]["markets"]
    )
    team_markets = result["experimental_live_team_markets"]
    assert team_markets["status"] == "unavailable_fallback_active"
    assert team_markets["bounded_market_grid_view"] == []
    assert team_markets["next_goal"] == {}


def test_official_live_engine_publishes_team_markets_on_success() -> None:
    """La rejilla restante viaja junto a la salida oficial sin alterarla."""

    result = predict_shadow_snapshot(
        DikamahaInferenceEngine(), _snapshot(), _prior(),
        {
            "allowed_leagues": ["esp.1"], "rho_goal": 1.0,
            "rho_next_event": 0.0,
        },
    )
    team_markets = result["experimental_live_team_markets"]
    next_goal = team_markets["next_goal"]

    assert team_markets["status"] == "experimental_shadow_not_promoted"
    assert team_markets["bounded_market_grid_view"]
    assert set(team_markets["remaining_intensities"]) == {
        "corners", "shots_commercial",
    }
    assert abs(
        next_goal["probability_home_next_goal"]
        + next_goal["probability_away_next_goal"]
        + next_goal["probability_no_more_goals"] - 1.0
    ) <= 1e-10


class _ScoreboardConnector:
    def scoreboard(self, date: str) -> dict[str, object]:
        assert date == "20260808"
        return {"events": [{
            "id": "900001", "date": "2026-08-08T20:00:00Z",
            "competitions": [{
                "id": "900001",
                "status": {"period": 1, "displayClock": "32'",
                           "type": {"state": "in", "detail": "32'"}},
                "competitors": [
                    {"homeAway": "home", "score": "1",
                     "team": {"id": "1", "displayName": "Equipo A"}},
                    {"homeAway": "away", "score": "0",
                     "team": {"id": "2", "displayName": "Equipo B"}},
                ],
            }],
        }]}


def test_live_catalog_uses_espn_state_score_and_orientation(tmp_path) -> None:
    """Lista sólo estado in/live y conserva local, visitante y marcador."""

    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    prematch = UniversalPrematchEngine(
        windows, team_markets_enabled=False,
        official_goal_chain_enabled=False,
    )
    runtime = LivePredictionRuntime(
        prematch, DikamahaInferenceEngine(),
        connector_factory=lambda _: _ScoreboardConnector(),
    )

    catalog = runtime.list_active("esp.1", 12, "20260808")

    fixture = catalog["fixtures"][0]
    assert fixture["provider_status"] == "in"
    assert fixture["home_team_name"] == "Equipo A"
    assert fixture["away_team_name"] == "Equipo B"
    assert fixture["home_score"] == 1 and fixture["away_score"] == 0
    assert fixture["display_clock"] == "32'"


class _BoundaryConnector:
    def scoreboard(self, date: str) -> dict[str, object]:
        if date != "20260809":
            return {"events": []}
        return _ScoreboardConnector().scoreboard("20260808")


def test_automatic_live_window_includes_previous_espn_day(
    tmp_path, monkeypatch,
) -> None:
    """Evita catálogo vacío cerca de medianoche UTC."""

    assert _candidate_live_dates(
        None, now=datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
    ) == ("20260809", "20260810", "20260811")
    assert _candidate_live_dates("20260808") == ("20260808",)
    windows = tmp_path / "event_windows.json"
    windows.write_text(json.dumps(_historical_rows()), encoding="utf-8")
    runtime = LivePredictionRuntime(
        UniversalPrematchEngine(
            windows, team_markets_enabled=False,
            official_goal_chain_enabled=False,
        ),
        DikamahaInferenceEngine(),
        connector_factory=lambda _: _BoundaryConnector(),
    )
    monkeypatch.setattr(
        live_runtime, "_candidate_live_dates",
        lambda value: ("20260809", "20260810", "20260811"),
    )

    catalog = runtime.list_active("esp.1", 12)

    assert catalog["count"] == 1
    assert catalog["date_count"] == 3
    assert catalog["dates"] == ["20260809", "20260810", "20260811"]
    assert catalog["fixtures"][0]["match_id"] == 900001


def test_observed_live_presentation_aggregates_teams_and_ignores_annulled() -> None:
    """Separa estadísticas visuales por orientación sin alterar el snapshot."""

    snapshot = {
        **_snapshot(),
        "home_team_name": "Local real", "away_team_name": "Visitante real",
        "score_home": 2, "score_away": 1,
        "events": [
            {"event_id": "1", "event_type": "corner", "event_type_raw": "corner", "team_id": 1, "period": 1, "match_clock_seconds": 60, "text": "Corner", "annulled": False},
            {"event_id": "2", "event_type": "shot_on_target", "event_type_raw": "shot_on_target", "team_id": 1, "period": 1, "match_clock_seconds": 120, "text": "Shot", "annulled": False},
            {"event_id": "3", "event_type": "auxiliary", "event_type_raw": "save", "team_id": 2, "period": 1, "match_clock_seconds": 121, "text": "Save", "annulled": False},
            {"event_id": "4", "event_type": "yellow", "event_type_raw": "yellow_card", "team_id": 2, "period": 1, "match_clock_seconds": 180, "text": "Card", "annulled": False},
            {"event_id": "5", "event_type": "corner", "event_type_raw": "corner", "team_id": 2, "period": 1, "match_clock_seconds": 200, "text": "Deleted", "annulled": True},
        ],
    }

    result = _observed_live_presentation(snapshot)
    statistics = result["observed_live_statistics"]

    assert statistics["home"]["goals"] == 2
    assert statistics["away"]["goals"] == 1
    assert statistics["home"]["corners"] == 1
    assert statistics["home"]["shots"] == 1
    assert statistics["away"]["yellow_cards"] == 1
    assert statistics["away"]["saves"] == 1
    assert len(result["recent_actions"]) == 4
    assert result["recent_actions"][0]["event_id"] == "4"
    assert result["automatic_refresh_recommended_seconds"] == 15


def test_match_dynamics_applies_signed_weights_and_centered_smoothing() -> None:
    """Orienta local/visitante y suaviza cinco minutos sin usar el futuro."""

    snapshot = {
        **_snapshot(),
        "home_team_name": "Equipo A", "away_team_name": "Equipo B",
        "match_clock_seconds": 900,
        "events": [
            {"event_type": "shot_on_target", "team_id": 1, "match_clock_seconds": 600},
            {"event_type": "corner", "team_id": 2, "match_clock_seconds": 660},
            {"event_type": "goal", "team_id": 1, "match_clock_seconds": 720},
            {"event_type": "goal", "team_id": 2, "match_clock_seconds": 780, "annulled": True},
        ],
    }

    result = _match_dynamics(snapshot)
    points = result["points"]

    assert len(points) == 90
    assert points[10]["raw_score"] == 8
    assert points[11]["raw_score"] == -3
    assert points[12]["raw_score"] == 25
    assert points[10]["smoothed_score"] == 6
    assert result["goal_markers"] == [{
        "minute": 13, "team_side": "home", "team_name": "Equipo A",
    }]
    assert result["not_model_feature"] is True
    assert result["smoothing"]["window_minutes"] == 5
