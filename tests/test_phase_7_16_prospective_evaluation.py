"""Pruebas unitarias del contrato read-only de Fase 7.16."""
from __future__ import annotations

from src.evaluate_prospective_espn import EvaluationConfig, _classification, _is_complete, _snapshots, _temporal


def test_complete_requires_explicit_staging_flag_and_final_score() -> None:
    """Un estado final sin flag complete no debe ser evaluable."""

    row = {"complete": True, "provider_status": "post", "home_score": 1, "away_score": 0, "home_team_id": 1, "away_team_id": 2}
    assert _is_complete(row) is True
    assert _is_complete({**row, "complete": False}) is False
    assert _is_complete({**row, "away_score": None}) is False


def test_snapshots_are_not_iid_and_obey_event_time() -> None:
    """Los snapshots conservan unidad partido completo y visibilidad causal."""

    match = {"match_id": 900001, "kickoff_ts": "2026-01-01T00:00:00+00:00"}
    events = [{"event_ts": "2026-01-01T00:10:00+00:00", "annulled": False}]
    snapshots = _snapshots(match, events)
    assert all(row["evaluation_unit"] == "complete_match" for row in snapshots)
    assert max(row["visible_event_count"] for row in snapshots) == 1


def test_less_than_thirty_is_insufficient_without_significance() -> None:
    """El umbral evita bootstrap o conclusiones confirmatorias prematuras."""

    audit = {"duplicate_event_count": 0, "orphan_event_match_references": [], "stable_temporal_order": True}
    assert _classification(29, audit, EvaluationConfig()) == "insufficient_prospective_coverage"

# Version: 1.0.0
# Created: 2026-07-16
