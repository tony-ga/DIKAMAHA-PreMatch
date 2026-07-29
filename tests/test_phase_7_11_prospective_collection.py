"""Pruebas de recolección prospectiva controlada."""

from __future__ import annotations

import json
from pathlib import Path

from src.collect_prospective_signals import (
    CollectionConfig,
    _collection_status,
    _is_complete,
    _normalized,
    _select_matches,
    _snapshot_schedule,
    _temporal_audit,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts" / "phase_7_11_prospective_collection"
PHASE_7_10_SELECTION = (
    ROOT / "artifacts" / "phase_7_10_prospective_signal_shadow"
    / "prospective_selection.json"
)


def _artifact(name: str) -> object:
    """Carga un artefacto JSON generado por la fase."""

    return json.loads((OUTPUT_DIR / name).read_text(encoding="utf-8"))


def _match(match_id: int, *, status: str = "post", scores: bool = True) -> dict:
    """Construye un partido sintético posterior al cutoff."""

    return {
        "id": match_id, "home_team_id": 101, "away_team_id": 102,
        "match_date": "2025-11-01T12:00:00+00:00", "status": status,
        "home_score": 1 if scores else None, "away_score": 0 if scores else None,
    }


def test_historical_and_704766_are_excluded() -> None:
    """No reutiliza historia ni el partido bloqueado."""

    matches = [_match(1), _match(704766), _match(900001)]
    records, selected, excluded = _select_matches(matches, [], {900001: {}}, CollectionConfig())
    assert 1 in excluded and 704766 in excluded
    assert [row["id"] for row in selected] == [900001]
    assert [row["match_id"] for row in records] == [900001]


def test_incomplete_match_never_exposes_final_result() -> None:
    """Un partido abierto permanece en captura sin target final."""

    match = _match(900001, status="in", scores=False)
    assert not _is_complete(match)
    schedule = _snapshot_schedule(match, [])
    assert len(schedule) == 1


def test_status_with_zero_and_partial_coverage() -> None:
    """Distingue cohorte vacía, captura y readiness."""

    config = CollectionConfig(minimum_complete_matches=2)
    assert _collection_status([], [], config, True)["status"] == "insufficient_prospective_coverage"
    partial = [{"collection_state": "collecting_in_play"}]
    assert _collection_status(partial, [], config, True)["status"] == "prospective_collection_in_progress"
    complete = [{"collection_state": "complete"}, {"collection_state": "complete"}]
    assert _collection_status(complete, [], config, True)["status"] == "prospective_collection_ready_for_evaluation"


def test_temporal_audit_detects_duplicates_and_future_events() -> None:
    """El gate rechaza duplicación y leakage temporal."""

    row = {
        "match_id": 900001, "snapshot_ts": "2025-11-01T12:05:00+00:00",
        "events_visible": [{"event_ts": "2025-11-01T12:06:00+00:00"}],
    }
    record = {"match_id": 900001, "complete": False, "final_result": None}
    audit = _temporal_audit([row, dict(row)], [record], {1, 704766})
    assert audit["event_ts_lte_snapshot_ts"] is False
    assert audit["duplicate_snapshot_count"] == 1


def test_replay_ignores_latency_not_mathematical_fields() -> None:
    """La normalización conserva contenido y excluye latencia/DB."""

    payload = {"snapshots": [{"match_id": 1, "service_latency_ms": 2.0}], "database": {"identical": True}}
    normalized = _normalized(payload)
    assert normalized["snapshots"] == [{"match_id": 1}]
    assert "database" not in normalized


def test_cutoff_matches_frozen_phase_7_10_selection() -> None:
    """Impide mover el cutoff después de observar partidos nuevos."""

    selection = json.loads(PHASE_7_10_SELECTION.read_text(encoding="utf-8"))
    assert CollectionConfig().cutoff_ts == selection["prospective_cutoff_ts"]


def test_collection_artifacts_are_explicit() -> None:
    """La captura no se presenta como evaluación o calibración."""

    status = _artifact("collection_status.json")
    excluded = _artifact("excluded_match_ids.json")
    assert status["evaluation_performed"] is False
    assert status["calibration_performed"] is False
    assert 704766 in excluded["match_ids"]
    assert len(_artifact("collected_matches.json")) == status["new_match_count"]
    assert len(_artifact("collected_snapshots.json")) == status["snapshot_count"]


def test_generated_artifacts_are_reproducible_and_read_only() -> None:
    """Fija replay, provenance y conteos PostgreSQL del resultado generado."""

    manifest = _artifact("manifest.json")
    database = _artifact("postgres_readonly_audit.json")
    provenance = _artifact("provenance_audit.json")
    assert manifest["replay_identical"] is True
    assert manifest["output_hash"] == manifest["replay_hash"]
    assert database["before"] == database["after"]
    assert database["select_only"] is True and database["write_statements"] == 0
    assert provenance["hawkes_enabled_default"] is False
    assert provenance["official_output"] == "markov_v1"
    assert provenance["evaluation_performed"] is False


# Version: 1.1.0
# Created: 2026-07-16
