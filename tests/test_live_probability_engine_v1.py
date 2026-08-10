"""Pruebas del motor matemático oficial in-live de Fase 116."""

from __future__ import annotations

import math

from src.hawkes_live_v2 import HawkesLiveConfig
from src.live_probability_engine_v1 import (
    LiveProbabilityEngineV1,
    MonteCarloDiagnosticRunner,
)
from src.markov_live_v1 import MarkovLiveInput, MarkovLiveV1


def _request(**changes: object) -> MarkovLiveInput:
    values: dict[str, object] = {
        "match_id": 900001,
        "home_team_id": 10,
        "away_team_id": 20,
        "kickoff_ts": "2026-08-09T20:00:00+00:00",
        "snapshot_ts": "2026-08-09T20:30:00+00:00",
        "match_clock_seconds": 1800.0,
        "period": 1,
        "score_home": 0,
        "score_away": 0,
        "lambda_base_home": 1.55,
        "lambda_base_away": 1.08,
        "events": (),
        "league_slug": "esp.1",
        "source_hash": "snapshot-source-hash",
    }
    values.update(changes)
    return MarkovLiveInput(**values)


def _predict(
    request: MarkovLiveInput, *, rho_goal: float = 0.0,
) -> dict[str, object]:
    markov = MarkovLiveV1().predict(request)
    return LiveProbabilityEngineV1().predict(
        request,
        markov,
        hawkes_config=HawkesLiveConfig(
            rho=rho_goal, rho_goal=rho_goal, rho_next_event=0.0,
        ),
    )


def test_engine_normalizes_markets_and_exposes_all_layers() -> None:
    result = _predict(_request())
    official = result["official_live_prediction"]
    markets = official["markets"]

    assert result["official_source"] == "live_probability_engine_v1"
    assert math.isclose(
        markets["probability_home"] + markets["probability_draw"]
        + markets["probability_away"],
        1.0,
        abs_tol=1e-10,
    )
    assert official["periods"].keys() == {
        "first_half", "second_half", "full_time",
    }
    assert len(markets["exact_score"]) == 12
    assert {
        "dynamic_poisson", "ctmc", "hazard", "dynamic_elo",
        "hawkes_residual", "composed_output", "monte_carlo_diagnostic",
        "audit", "provenance",
    } <= result["live_probability_engine"].keys()
    assert result["live_probability_engine"]["audit"]["passed"] is True


def test_zero_zero_minute_seventy_reduces_remaining_goal_mass() -> None:
    early = _predict(_request(
        snapshot_ts="2026-08-09T20:10:00+00:00",
        match_clock_seconds=600.0,
    ))
    late = _predict(_request(
        snapshot_ts="2026-08-09T21:10:00+00:00",
        match_clock_seconds=4200.0,
        period=2,
    ))
    early_total = sum(
        early["official_live_prediction"]["remaining_intensities"].values()
    )
    late_total = sum(
        late["official_live_prediction"]["remaining_intensities"].values()
    )

    assert late_total < early_total
    assert (
        late["official_live_prediction"]["markets"]["probability_draw"]
        > early["official_live_prediction"]["markets"]["probability_draw"]
    )


def test_recent_home_pressure_increases_home_hazard_and_intensity() -> None:
    events = tuple({
        "event_id": f"s{index}",
        "event_type": "shot_on_target" if index < 3 else "corner",
        "team_id": 10,
        "period": 1,
        "match_clock_seconds": 1500.0 + index * 45.0,
    } for index in range(6))
    neutral = _predict(_request())
    pressured = _predict(_request(events=events))

    neutral_engine = neutral["live_probability_engine"]
    pressure_engine = pressured["live_probability_engine"]
    assert pressure_engine["hazard"]["multipliers"]["home"] > 1.0
    assert pressure_engine["ctmc"]["dominant"] == "home_pressure"
    assert (
        pressured["official_live_prediction"]["remaining_intensities"]["home"]
        > neutral["official_live_prediction"]["remaining_intensities"]["home"]
    )


def test_red_card_penalizes_team_and_changes_ctmc_inputs() -> None:
    events = ({
        "event_id": "red-home",
        "event_type": "red",
        "team_id": 10,
        "period": 1,
        "match_clock_seconds": 1700.0,
    },)
    neutral = _predict(_request())
    red = _predict(_request(events=events))

    assert red["live_probability_engine"]["dynamic_poisson"]["red_cards"] == {
        "home": 1, "away": 0,
    }
    assert (
        red["official_live_prediction"]["remaining_intensities"]["home"]
        < neutral["official_live_prediction"]["remaining_intensities"]["home"]
    )


def test_hawkes_rho_zero_reproduces_analytical_baseline_exactly() -> None:
    events = ({
        "event_id": "shot",
        "event_type": "shot_on_target",
        "team_id": 10,
        "period": 1,
        "match_clock_seconds": 1750.0,
    },)
    result = _predict(_request(events=events), rho_goal=0.0)
    engine = result["live_probability_engine"]
    dynamic = engine["dynamic_poisson"]
    combined = engine["composed_output"]

    assert combined["fallback_exact_markov_live"] is True
    assert combined["lambda_remaining_home"] == dynamic["lambda_remaining_home"]
    assert combined["lambda_remaining_away"] == dynamic["lambda_remaining_away"]
    assert combined["markets"] == dynamic["markets"]


def test_monte_carlo_is_non_blocking_and_deterministic() -> None:
    result = _predict(_request())
    runner = MonteCarloDiagnosticRunner(20_000, max_workers=1)
    try:
        first = runner.submit(
            900001, "snapshot-source-hash",
            result["official_live_prediction"],
        )
        completed = runner.wait(first["diagnostic_key"])
        replay = runner.submit(
            900001, "snapshot-source-hash",
            result["official_live_prediction"],
        )
    finally:
        runner.shutdown()

    assert first["blocking"] is False
    assert completed == replay
    assert completed["status"] == "complete"
    assert completed["simulations"] == 20_000
    assert completed["used_for_official_output"] is False
    assert completed["maximum_absolute_error"] < 0.03


# Version: 1.0.0
# Created: 2026-08-09
