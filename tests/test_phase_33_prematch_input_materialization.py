"""Pruebas de materialización causal para candidatos prospectivos."""

from __future__ import annotations

from src.phase_33_prematch_input_materialization import (
    _context_usable,
    _feature_audit,
    build_candidate_feature_rows,
)


def _history() -> list[dict]:
    """Construye dos partidos históricos mínimos."""

    metrics = {name: 0 for name in ("goals", "goals_against", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards", "event_count")}
    return [
        {"match_id": 10, "match_date": "2025-01-01T12:00:00+00:00", "home_team_id": 1, "away_team_id": 2, "home": metrics.copy(), "away": metrics.copy()},
        {"match_id": 11, "match_date": "2025-01-08T12:00:00+00:00", "home_team_id": 2, "away_team_id": 1, "home": metrics.copy(), "away": metrics.copy()},
    ]


def test_candidate_features_use_only_history() -> None:
    """La fila nueva no incluye al objetivo entre sus precedentes."""

    candidate = [{"match_id": 99, "kickoff_ts": "2025-01-15T12:00:00+00:00", "home_team_id": 1, "away_team_id": 2}]
    rows = build_candidate_feature_rows(_history(), candidate)
    audit = _feature_audit(rows, _history(), {99})
    assert len(rows) == 1
    assert rows[0]["target_match_data_used"] is False
    assert audit["temporal_causality_pass"] is True


def test_candidate_team_mapping_is_applied() -> None:
    """Los IDs provider se convierten antes de construir features."""

    candidate = [{"match_id": 99, "kickoff_ts": "2025-01-15T12:00:00+00:00", "home_team_id": 101, "away_team_id": 102}]
    rows = build_candidate_feature_rows(_history(), candidate, {101: 1, 102: 2})
    assert rows[0]["home_history_count"] == 1.0
    assert rows[0]["away_history_count"] == 1.0


def test_context_requires_identity_and_kickoff_alignment() -> None:
    """El contexto desalineado queda fuera del paquete preparado."""

    row = {"status": "ok", "identity_pass": True, "summary_kickoff_ts": "2025-01-15T12:01:00+00:00", "target_match_statistics_used": False}
    usable, reasons = _context_usable(row, "2025-01-15T12:00:00+00:00")
    assert usable is False
    assert "context_kickoff_mismatch" in reasons

# Version: 1.0.0
# Created: 2026-07-26
