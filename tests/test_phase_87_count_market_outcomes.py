"""Pruebas de outcomes prospectivos reconciliados."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.run_phase_87_count_market_outcomes import _eligible
from src.count_market_outcomes import EspnCountOutcomeParser
from src.count_market_prospective import FrozenCountPrediction


def _prediction() -> FrozenCountPrediction:
    """Construye una predicción congelada."""

    kickoff = datetime(2030, 1, 10, 20, tzinfo=timezone.utc)
    probabilities = {"home_corners_over_4_5": 0.5,
                     "away_corners_over_4_5": 0.4,
                     "away_shots_over_10_5": 0.3,
                     "first_half_cards_over_1_5": 0.6}
    return FrozenCountPrediction(
        "esp.1", 99, kickoff, kickoff - timedelta(days=1), 10, 20,
        probabilities, probabilities, "a" * 64, "b" * 64,
        "experimental_shadow_not_promoted")


def _summary() -> dict[str, object]:
    """Construye summary final con boxscore orientado."""

    return {
        "header": {"id": "99", "competitions": [{
            "id": "199", "date": "2030-01-10T20:00:00Z",
            "status": {"type": {"completed": True, "state": "post"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "10"}},
                {"homeAway": "away", "team": {"id": "20"}},
            ]}]},
        "boxscore": {"teams": [
            {"team": {"id": "10"}, "statistics": [
                {"name": "wonCorners", "displayValue": "6"},
                {"name": "totalShots", "displayValue": "12"},
                {"name": "yellowCards", "displayValue": "1"}]},
            {"team": {"id": "20"}, "statistics": [
                {"name": "wonCorners", "displayValue": "4"},
                {"name": "totalShots", "displayValue": "11"},
                {"name": "yellowCards", "displayValue": "1"}]},
        ]},
    }


def _plays() -> dict[str, object]:
    """Construye dos amarillas reconciliadas de primera mitad."""

    return {"items": [
        {"id": "1", "type": {"type": "yellow-card"},
         "team": {"id": "10"}, "clock": {"value": 600}},
        {"id": "2", "type": {"type": "yellow-card"},
         "team": {"id": "20"}, "clock": {"value": 2400}},
    ]}


def test_parser_reconciles_all_four_outcomes() -> None:
    """Deriva targets sólo con identidad y tarjetas consistentes."""

    prediction = _prediction()
    outcome = EspnCountOutcomeParser().parse(
        prediction, _summary(), _plays(),
        prediction.kickoff_ts + timedelta(hours=3))
    assert outcome.counts["home_corners"] == 6
    assert outcome.counts["first_half_yellow_cards"] == 2
    assert outcome.outcomes["home_corners_over_4_5"] is True
    assert outcome.outcomes["away_corners_over_4_5"] is False
    assert outcome.outcomes["away_shots_over_10_5"] is True
    assert outcome.outcomes["first_half_cards_over_1_5"] is True


def test_parser_rejects_card_inconsistency() -> None:
    """No acepta una partición temporal incompleta."""

    prediction = _prediction()
    summary = _summary()
    summary["boxscore"]["teams"][0]["statistics"][2]["displayValue"] = "2"
    try:
        EspnCountOutcomeParser().parse(
            prediction, summary, _plays(),
            prediction.kickoff_ts + timedelta(hours=3))
    except ValueError as error:
        assert str(error) == "outcome_yellow_reconciliation_failed"
    else:
        raise AssertionError("La discrepancia de tarjetas debe rechazarse.")


def test_settlement_waits_three_hours_after_kickoff() -> None:
    """Impide llamadas post-match prematuras."""

    prediction = _prediction()
    before = prediction.kickoff_ts + timedelta(hours=2, minutes=59)
    after = prediction.kickoff_ts + timedelta(hours=3)
    assert _eligible([prediction], [], before) == []
    assert _eligible([prediction], [], after) == [prediction]


# Version: 1.0.0
# Created: 2026-07-28
