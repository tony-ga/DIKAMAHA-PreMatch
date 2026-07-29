"""Pruebas del gate de cohorte prospectiva."""

from __future__ import annotations

from src.phase_31_prospective_cohort_gate import _classification, _eligible


def _row(match_id: int, *, reused: bool = False) -> dict:
    """Construye un partido sintético para el gate."""

    return {"match_id": match_id, "kickoff_ts": "2026-01-01T12:00:00+00:00", "complete": True, "provider_status": "post", "home_score": 1, "away_score": 0, "home_team_id": 1, "away_team_id": 2, "event_count": 5, "reused": reused}


def test_model_reuse_is_rejected() -> None:
    """Los partidos usados por el modelo no entran en la cohorte."""

    candidates, rejected = _eligible([_row(1)], {"1"})
    assert candidates == []
    assert rejected[0]["reasons"] == ["model_reuse"]


def test_thirty_independent_matches_open_the_gate() -> None:
    """El mínimo de partidos completos habilita el siguiente gate."""

    assert _classification(29) == "waiting_for_new_independent_cohort"
    assert _classification(30) == "cohort_ready_for_confirmatory_evaluation"

# Version: 1.0.0
# Created: 2026-07-26
