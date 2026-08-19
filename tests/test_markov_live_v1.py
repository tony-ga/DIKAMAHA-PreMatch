"""Pruebas del filtro causal Markov Live v1."""

from __future__ import annotations

import math

import pytest

from src.markov_live_v1 import (
    MarkovLiveConfig,
    MarkovLiveInput,
    MarkovLiveV1,
)


def _request(**changes: object) -> MarkovLiveInput:
    values = {
        "match_id": 900001,
        "home_team_id": 1,
        "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "snapshot_ts": "2025-01-10T20:10:00+00:00",
        "match_clock_seconds": 600.0,
        "period": 1,
        "score_home": 0,
        "score_away": 0,
        "lambda_base_home": 1.5,
        "lambda_base_away": 1.1,
        "events": (),
        "league_slug": "esp.1",
        "source_hash": "raw-live-v1",
    }
    values.update(changes)
    return MarkovLiveInput(**values)


def test_kickoff_reproduces_frozen_prematch_intensities() -> None:
    """En t=0 el filtro no debe alterar el prior pre-match."""

    output = MarkovLiveV1().predict(_request(
        snapshot_ts="2025-01-10T20:00:00+00:00",
        match_clock_seconds=0.0,
    ))
    assert output["lambda_remaining_home"] == pytest.approx(1.5)
    assert output["lambda_remaining_away"] == pytest.approx(1.1)
    assert output["audit"]["passed"] is True
    assert sum(output["state"]["posterior"].values()) == pytest.approx(1.0)


def test_home_pressure_updates_state_and_remaining_goal_risk() -> None:
    """Una secuencia ofensiva local incrementa el riesgo local relativo."""

    model = MarkovLiveV1()
    baseline = model.predict(_request())
    event = ({
        "event_id": "shot-1",
        "event_type": "shot_on_target",
        "team_id": 1,
        "period": 1,
        "match_clock_seconds": 540.0,
    },)
    pressured = model.predict(_request(events=event))
    assert pressured["state"]["posterior"]["home_pressure"] > baseline["state"]["posterior"]["home_pressure"]
    assert pressured["lambda_remaining_home"] > baseline["lambda_remaining_home"]
    assert pressured["events_audit"]["accepted"] == 1


def test_final_snapshot_has_no_remaining_goals_and_respects_score() -> None:
    """Al final no queda masa de gol y el mercado refleja el marcador."""

    output = MarkovLiveV1().predict(_request(
        snapshot_ts="2025-01-10T21:30:00+00:00",
        match_clock_seconds=5400.0,
        period=2,
        score_home=1,
        score_away=0,
        events=({
            "event_id": "g1", "event_type": "goal", "team_id": 1,
            "period": 1, "match_clock_seconds": 600.0,
        },),
    ))
    assert output["lambda_remaining_home"] == 0.0
    assert output["lambda_remaining_away"] == 0.0
    assert output["markets"]["probability_home"] == pytest.approx(1.0)
    assert output["next_event"]["probability_no_event"] == pytest.approx(1.0)


def test_future_events_fail_closed() -> None:
    """El motor no puntúa un snapshot contaminado por eventos futuros."""

    event = ({
        "event_id": "future",
        "event_type": "goal",
        "team_id": 1,
        "period": 1,
        "match_clock_seconds": 601.0,
    },)
    with pytest.raises(ValueError, match="future_event"):
        MarkovLiveV1().predict(_request(events=event))


def test_period_and_clock_must_be_coherent() -> None:
    with pytest.raises(ValueError, match="period_aware"):
        MarkovLiveV1().predict(_request(period=2, match_clock_seconds=600.0))
    with pytest.raises(ValueError, match="future_event_period"):
        MarkovLiveV1().predict(_request(events=({
            "event_id": "future-period",
            "event_type": "shot_on_target",
            "team_id": 1,
            "period": 2,
            "match_clock_seconds": 590.0,
        },)))


def test_markets_and_next_event_probabilities_are_normalized() -> None:
    output = MarkovLiveV1().predict(_request())
    markets = output["markets"]
    assert math.fsum(markets[key] for key in ("probability_home", "probability_draw", "probability_away")) == pytest.approx(1.0)
    next_event = output["next_event"]
    assert math.fsum(next_event["probabilities"].values()) + next_event["probability_no_event"] == pytest.approx(1.0)


def test_second_half_stoppage_keeps_only_a_short_live_tail() -> None:
    """El minuto 95 mantiene un minuto móvil, no treinta minutos ficticios."""

    output = MarkovLiveV1().predict(_request(
        snapshot_ts="2025-01-10T21:35:00+00:00",
        match_clock_seconds=5700.0,
        period=2,
    ))
    assert output["remaining_seconds"] == 60.0
    assert 0.0 < output["lambda_remaining_home"] < 0.05
    assert output["next_event"]["horizon_minutes"] == 1.0


def test_score_must_reconcile_with_observed_goals() -> None:
    """Un marcador no explicado por el timeline se rechaza sin imputación."""

    with pytest.raises(ValueError, match="score_play_by_play_mismatch"):
        MarkovLiveV1().predict(_request(score_home=1, score_away=0))


def test_transition_count_is_invariant_to_event_fragmentation() -> None:
    """Un evento no debe eliminar pasos Markov al partir el intervalo."""

    model = MarkovLiveV1()
    baseline = model.predict(_request())
    auxiliary = ({
        "event_id": "aux",
        "event_type": "auxiliary",
        "team_id": 1,
        "match_clock_seconds": 240.0,
    },)
    fragmented = model.predict(_request(events=auxiliary))
    assert fragmented["state"] == baseline["state"]
    assert fragmented["lambda_remaining_home"] == baseline["lambda_remaining_home"]


# --- Señal defensiva `save` (`DEC-217`, Fases 116G/116H) -------------------
#
# `save` no es una parada de portero -7.06 jugadores distintos por partido lo
# registran, incluidos delanteros- así que proyectarlo a `shot_on_target` fue
# rechazado en 116F. Pero sí aporta información incremental sobre los goles de
# la ventana siguiente, con coeficiente NEGATIVO: quien acumula paradas está
# defendiendo, no atacando. Entra como tipo propio con peso negativo.


def test_a_save_is_accepted_as_its_own_defensive_signal() -> None:
    """Llega como `auxiliary` + `event_type_raw`, la forma real del follower."""

    events = ({
        "event_id": "s1",
        "event_type": "auxiliary",
        "event_type_raw": "save",
        "team_id": 2,
        "match_clock_seconds": 300.0,
    },)

    result = MarkovLiveV1().predict(_request(events=events))

    assert result["events_audit"]["defensive_signals"] == 1
    used = result["events_used"]
    assert len(used) == 1
    assert used[0]["event_type"] == "save"
    # No se reasigna al rival: la señal describe a quien defiende.
    assert used[0]["team_id"] == 2


def test_the_defensive_weight_is_negative() -> None:
    """El signo lo fijó la medición, no el criterio: defender no es atacar."""

    assert MarkovLiveV1.EVENT_WEIGHTS["save"] < 0.0


def test_disabling_the_signal_restores_the_previous_behaviour() -> None:
    events = (
        {"event_id": "a", "event_type": "shot_on_target", "team_id": 1,
         "match_clock_seconds": 120.0},
        {"event_id": "s", "event_type": "auxiliary", "event_type_raw": "save",
         "team_id": 2, "match_clock_seconds": 300.0},
    )
    request = _request(events=events)

    disabled = MarkovLiveV1(
        MarkovLiveConfig(enable_defensive_save_signal=False)).predict(request)

    assert disabled["events_audit"]["unmodelled"] == 1
    assert disabled["events_audit"]["accepted"] == 1
    assert disabled["events_audit"].get("defensive_signals", 0) == 0


def test_other_auxiliaries_are_still_discarded_with_the_signal_on() -> None:
    """Sólo `save` entra; el resto de auxiliares siguen fuera del modelo."""

    events = (
        {"event_id": "t", "event_type": "auxiliary", "event_type_raw": "throw_in",
         "team_id": 1, "match_clock_seconds": 200.0},
        {"event_id": "f", "event_type": "auxiliary", "event_type_raw": "free_kick",
         "team_id": 2, "match_clock_seconds": 250.0},
    )

    result = MarkovLiveV1().predict(_request(events=events))

    assert result["events_used"] == []
    assert result["events_audit"]["unmodelled"] == 2
