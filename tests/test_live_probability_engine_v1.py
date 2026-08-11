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


def _team_markets(request: MarkovLiveInput) -> dict[str, object]:
    return _predict(request)["experimental_live_team_markets"]


def _grid_row(markets: dict[str, object], key: str) -> dict[str, object]:
    return next(
        row for row in markets["bounded_market_grid_view"]
        if row["key"] == key
    )


def test_team_markets_intensities_are_finite_and_nonnegative() -> None:
    markets = _team_markets(_request())
    intensities = markets["remaining_intensities"]

    assert markets["status"] == "experimental_shadow_not_promoted"
    for metric in ("corners", "shots_commercial"):
        for side in ("home", "away"):
            value = intensities[metric][side]
            assert math.isfinite(value)
            assert value >= 0.0


def test_shots_commercial_never_falls_below_the_goal_intensity() -> None:
    """DEC-110: shots comerciales incluyen el gol como tiro."""

    result = _predict(_request())
    dynamic = result["live_probability_engine"]["dynamic_poisson"]

    assert (
        dynamic["lambda_remaining_shots_home"]
        >= dynamic["lambda_remaining_home"]
    )
    assert (
        dynamic["lambda_remaining_shots_away"]
        >= dynamic["lambda_remaining_away"]
    )


def test_remaining_grid_lines_are_complementary() -> None:
    markets = _team_markets(_request())

    assert markets["bounded_market_grid_view"]
    for row in markets["bounded_market_grid_view"]:
        assert len(row["lines"]) == 3
        for line in row["lines"]:
            assert math.isclose(
                line["over_probability"] + line["under_probability"],
                1.0, abs_tol=1e-9,
            )
            assert math.isclose(
                line["baseline_over_probability"]
                + line["baseline_under_probability"],
                1.0, abs_tol=1e-9,
            )


def test_remaining_lines_adapt_to_recent_pressure() -> None:
    """La línea debe moverse con el partido, no ser un umbral fijo."""

    pressure = tuple(
        {
            "event_id": str(index), "event_type": event_type,
            "team_id": 20, "match_clock_seconds": 1700.0 + index * 5.0,
            "period": 1,
        }
        for index, event_type in enumerate(
            ("corner", "corner", "shot_on_target", "corner", "shot_on_target")
        )
    )
    neutral = _team_markets(_request())
    pressed = _team_markets(_request(events=pressure))

    neutral_away = _grid_row(neutral, "away_corners_remaining")
    pressed_away = _grid_row(pressed, "away_corners_remaining")
    neutral_home = _grid_row(neutral, "home_corners_remaining")
    pressed_home = _grid_row(pressed, "home_corners_remaining")

    assert pressed_away["expected_remaining"] > neutral_away["expected_remaining"]
    assert pressed_home["expected_remaining"] < neutral_home["expected_remaining"]


def test_remaining_lines_differ_between_teams_without_events() -> None:
    """Sin eventos la línea sigue siendo específica de cada equipo."""

    markets = _team_markets(_request())
    home = _grid_row(markets, "home_corners_remaining")
    away = _grid_row(markets, "away_corners_remaining")

    assert home["expected_remaining"] != away["expected_remaining"]


def test_next_goal_probabilities_are_normalized() -> None:
    next_goal = _team_markets(_request())["next_goal"]
    total = (
        next_goal["probability_home_next_goal"]
        + next_goal["probability_away_next_goal"]
        + next_goal["probability_no_more_goals"]
    )

    assert math.isclose(total, 1.0, abs_tol=1e-10)
    assert next_goal["normalization_error"] <= 1e-9
    assert next_goal["remaining_minutes"] == 60.0


def test_next_goal_collapses_when_no_time_remains() -> None:
    next_goal = _team_markets(_request(match_clock_seconds=5400.0))["next_goal"]

    assert next_goal["probability_no_more_goals"] == 1.0
    assert next_goal["probability_home_next_goal"] == 0.0
    assert next_goal["probability_away_next_goal"] == 0.0


def test_team_market_audit_checks_are_part_of_the_official_gate() -> None:
    """Un fallo de la capa nueva debe degradar el snapshot completo."""

    checks = _predict(_request())["live_probability_engine"]["audit"]["checks"]

    assert checks["team_count_intensities_nonnegative"] is True
    assert checks["shots_commercial_includes_goal_rate"] is True
    assert checks["team_count_grid_complementary"] is True
    assert checks["next_goal_normalized"] is True


def test_neutral_match_reproduces_the_historical_team_averages() -> None:
    """Ancla las tasas base al corpus causal de Fase 74.

    Medias observadas en 9,465 partidos y 18,930 unidades equipo-partido:
    5.4175 corners, 7.3320 tiros sin gol y 1.3411 goles por equipo. Un partido
    neutro desde el minuto cero debe reproducirlas, de modo que un cambio de
    constantes que desplace el nivel absoluto falle aquí.
    """

    goals = 1.3411
    result = _predict(_request(
        match_clock_seconds=0.0, score_home=0, score_away=0,
        lambda_base_home=goals, lambda_base_away=goals, events=(),
    ))
    dynamic = result["live_probability_engine"]["dynamic_poisson"]
    corners = dynamic["lambda_remaining_corners_home"]
    shots = dynamic["lambda_remaining_shots_home"]
    goal_intensity = dynamic["lambda_remaining_home"]

    assert abs(corners - 5.4175) < 0.05
    assert abs((shots - goal_intensity) - 7.3320) < 0.05
    assert abs(shots - 8.6731) < 0.10


def test_official_live_prediction_is_unchanged_by_the_new_block() -> None:
    """Fase 116 debe conservar su salida oficial byte a byte."""

    result = _predict(_request())
    official = result["official_live_prediction"]

    assert "bounded_market_grid_view" not in official
    assert "next_goal" not in official
    assert set(official) == {
        "status", "model_version", "markets", "periods",
        "remaining_intensities", "next_event", "exact_score",
        "remaining_goals_distribution", "goal_horizons", "confidence",
        "updated_at", "fallback",
    }
    assert set(official["remaining_intensities"]) == {"home", "away"}


# Version: 1.1.0
# Created: 2026-08-09
# Updated: 2026-08-10 (Fase 117)
