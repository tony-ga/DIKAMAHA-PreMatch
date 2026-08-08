"""Regresiones del runtime de producto Markov Live + Hawkes."""

from __future__ import annotations

import json
from datetime import datetime

from src.dikamaha_inference import DikamahaInferenceEngine
from src.live_prediction_runtime import (
    LivePredictionRuntime,
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
