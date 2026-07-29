"""Pruebas de settlement Markov por equipo y mitad.

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

from datetime import timedelta

from scripts.run_phase_91_markov_market_outcomes import _eligible
from src.count_market_outcomes import EspnMarkovMarketOutcomeParser
from tests.test_phase_87_count_market_outcomes import _prediction, _summary


def _play(
    identifier: str, team_id: int, period: int, event_type: str,
) -> dict[str, object]:
    """Construye un evento temporal identificado."""

    return {
        "id": identifier, "type": {"type": event_type},
        "team": {"id": str(team_id)}, "period": {"number": period},
        "clock": {"value": 600 if period == 1 else 3300},
    }


def _markov_summary() -> dict[str, object]:
    """Reduce el boxscore a conteos controlados."""

    summary = _summary()
    home = summary["boxscore"]["teams"][0]["statistics"]
    away = summary["boxscore"]["teams"][1]["statistics"]
    home[0]["displayValue"], home[1]["displayValue"] = "3", "7"
    away[0]["displayValue"], away[1]["displayValue"] = "0", "6"
    return summary


def _markov_plays() -> dict[str, object]:
    """Crea eventos que reconcilian por equipo y mitad."""

    items = [
        _play(f"h1-{index}", 10, 1, "shot-on-target")
        for index in range(5)]
    items.append(_play("h1-goal", 10, 1, "goal"))
    items.append(_play("h2-shot", 10, 2, "shot-blocked"))
    items.extend(
        _play(f"h2-corner-{index}", 10, 2, "corner")
        for index in range(3))
    items.extend(
        _play(f"a2-{index}", 20, 2, "shot-off-target")
        for index in range(6))
    return {"items": items}


def test_parser_reconciles_four_markov_outcomes() -> None:
    """Liquida tiros/corners por mitad incluyendo goles como tiros."""

    prediction = _prediction()
    outcome = EspnMarkovMarketOutcomeParser().parse(
        prediction, _markov_summary(), _markov_plays(),
        prediction.kickoff_ts + timedelta(hours=3))
    assert outcome.counts["home_shots_first_half"] == 6
    assert outcome.outcomes["home_shots_first_half_over_5_5"] is True
    assert outcome.outcomes["home_shots_second_half_over_5_5"] is False
    assert outcome.outcomes["away_shots_second_half_over_5_5"] is True
    assert outcome.outcomes["home_corners_second_half_over_2_5"] is True


def test_parser_rejects_temporal_total_mismatch() -> None:
    """Rechaza una cronología incompleta frente al boxscore."""

    prediction = _prediction()
    plays = _markov_plays()
    plays["items"].pop()
    try:
        EspnMarkovMarketOutcomeParser().parse(
            prediction, _markov_summary(), plays,
            prediction.kickoff_ts + timedelta(hours=3))
    except ValueError as error:
        assert str(error) == "outcome_shots_temporal_reconciliation_failed"
    else:
        raise AssertionError("La discrepancia temporal debe rechazarse.")


def test_settlement_waits_three_hours() -> None:
    """No habilita endpoints post-match antes del asentamiento."""

    prediction = _prediction()
    before = prediction.kickoff_ts + timedelta(hours=2, minutes=59)
    after = prediction.kickoff_ts + timedelta(hours=3)
    assert _eligible([prediction], [], before) == []
    assert _eligible([prediction], [], after) == [prediction]


# Version: 1.0.0
# Created: 2026-07-29
