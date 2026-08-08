"""Pruebas de complementariedad Hawkes Live v2."""

from __future__ import annotations

import pytest

from src.hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
from src.markov_live_v1 import MarkovLiveInput, MarkovLiveV1


def _markov(events: tuple[dict[str, object], ...] = ()) -> dict[str, object]:
    request = MarkovLiveInput(
        match_id=900001,
        home_team_id=1,
        away_team_id=2,
        kickoff_ts="2025-01-10T20:00:00+00:00",
        snapshot_ts="2025-01-10T20:10:00+00:00",
        match_clock_seconds=600.0,
        period=1,
        score_home=0,
        score_away=0,
        lambda_base_home=1.5,
        lambda_base_away=1.1,
        events=events,
    )
    return MarkovLiveV1().predict(request)


def test_rho_zero_is_exact_markov_live_fallback() -> None:
    event = {"event_id": "s1", "event_type": "shot_on_target", "team_id": 1, "match_clock_seconds": 590.0}
    markov = _markov((event,))
    output = HawkesLiveV2(HawkesLiveConfig(rho=0.0)).combine(
        markov, [event], home_team_id=1, away_team_id=2, score_home=0, score_away=0,
    )
    combined = output["combined_live"]
    assert combined["fallback_exact_markov_live"] is True
    assert combined["lambda_remaining_home"] == markov["lambda_remaining_home"]
    assert combined["lambda_remaining_away"] == markov["lambda_remaining_away"]
    assert combined["markets"] == markov["markets"]
    assert combined["next_event"] == markov["next_event"]


def test_recent_home_shot_adds_only_a_bounded_residual() -> None:
    event = {"event_id": "s1", "event_type": "shot_on_target", "team_id": 1, "match_clock_seconds": 590.0}
    markov = _markov((event,))
    output = HawkesLiveV2().combine(
        markov, [event], home_team_id=1, away_team_id=2, score_home=0, score_away=0,
    )
    residual = output["hawkes_residual"]
    combined = output["combined_live"]
    assert residual["log_residuals"]["home:goal"] > residual["log_residuals"]["away:goal"]
    assert combined["lambda_remaining_home"] > markov["lambda_remaining_home"]
    assert combined["lambda_remaining_home"] <= markov["lambda_remaining_home"] * HawkesLiveConfig().maximum_multiplier
    assert residual["stability"]["subcritical"] is True


def test_target_specific_rho_can_improve_goals_without_changing_next_event() -> None:
    event = {"event_id": "s1", "event_type": "shot_on_target", "team_id": 1, "match_clock_seconds": 590.0}
    markov = _markov((event,))
    output = HawkesLiveV2(HawkesLiveConfig(
        rho=0.0, rho_goal=0.5, rho_next_event=0.0,
    )).combine(
        markov, [event], home_team_id=1, away_team_id=2,
        score_home=0, score_away=0,
    )
    combined = output["combined_live"]
    assert combined["lambda_remaining_home"] > markov["lambda_remaining_home"]
    assert combined["next_event"] == markov["next_event"]
    assert output["hawkes_residual"]["rho_by_target"] == {
        "goal_markets": 0.5, "next_event": 0.0,
    }


def test_red_card_excites_the_opponent_side() -> None:
    event = {"event_id": "r1", "event_type": "red", "team_id": 1, "match_clock_seconds": 590.0}
    markov = _markov((event,))
    residual = HawkesLiveV2().combine(
        markov, [event], home_team_id=1, away_team_id=2, score_home=0, score_away=0,
    )["hawkes_residual"]["log_residuals"]
    assert residual["away:goal"] > residual["home:goal"]


def test_hawkes_rejects_future_events() -> None:
    markov = _markov()
    future = {"event_id": "future", "event_type": "goal", "team_id": 1, "match_clock_seconds": 601.0}
    with pytest.raises(ValueError, match="future_event"):
        HawkesLiveV2().combine(
            markov, [future], home_team_id=1, away_team_id=2, score_home=0, score_away=0,
        )
