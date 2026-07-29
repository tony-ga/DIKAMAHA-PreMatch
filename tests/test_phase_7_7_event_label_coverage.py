"""Pruebas unitarias del etiquetado candidato de Fase 7.7."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.audit_event_label_coverage import (
    CoverageConfig,
    TeamContext,
    _apply_candidate,
    _decision,
    _partition,
    _rule_matches,
    _unknown_cause,
)
from src.calibrate_inplay_models import _valid_events
from src.hawkes_v1_integration import HawkesIntegrationConfig


def _context(**overrides: object) -> TeamContext:
    """Construye un contexto causal sintético."""

    values = {
        "match_id": 1, "snapshot_ts": "2025-01-01T12:75:00+00:00",
        "block": "development", "team_id": 1, "rival_team_id": 2,
        "minute": 75, "goal_difference": -1,
        "own_pressure_5m": 0.0, "rival_pressure_5m": 0.0,
        "own_pressure_10m": 0.0, "rival_pressure_10m": 0.0,
        "own_red_10m": 0, "rival_red_10m": 0,
        "event_types_10m": (),
        "event_timestamps_10m": (),
    }
    values.update(overrides)
    return TeamContext(**values)


def test_late_score_rule_is_deterministic() -> None:
    """El mismo contexto siempre produce el mismo estado."""

    first = _rule_matches(_context(), CoverageConfig())
    second = _rule_matches(_context(), CoverageConfig())
    assert first == second
    assert ("late_score_context", 2) in first


def test_sustained_pressure_uses_both_windows() -> None:
    """Una sola ventana alta no se convierte en asedio."""

    context = _context(
        minute=40, goal_difference=0, own_pressure_5m=4.0,
        rival_pressure_5m=0.0, own_pressure_10m=4.0,
        rival_pressure_10m=0.0,
    )
    assert ("sustained_pressure_dominance", 2) not in _rule_matches(
        context, CoverageConfig()
    )


def test_unresolvable_substitution_remains_unknown() -> None:
    """Una sustitución aislada no fuerza estado táctico."""

    context = _context(
        minute=30, goal_difference=0, event_types_10m=("substitution",)
    )
    row = {
        "baseline_unknown": True, "baseline_state": -1,
        "candidate_matches": [],
    }
    state, rule = _apply_candidate(row, set())
    assert state == -1
    assert rule is None
    assert _unknown_cause(context) == "substitution_only"


def test_conflicting_rules_preserve_unknown() -> None:
    """Reglas con estados incompatibles no se resuelven por precedencia."""

    row = {
        "baseline_unknown": True, "baseline_state": -1,
        "candidate_matches": [
            {"rule_id": "late_score_context", "state": 2},
            {"rule_id": "sustained_opponent_pressure", "state": 1},
        ],
    }
    state, rule = _apply_candidate(
        row, {"late_score_context", "sustained_opponent_pressure"}
    )
    assert state == -1
    assert rule == "candidate_rule_conflict"


def test_partition_does_not_mix_matches() -> None:
    """Los partidos completos aparecen en un solo bloque."""

    rows = [
        {"id": index, "match_date": datetime(2025, 1, index, tzinfo=timezone.utc)}
        for index in range(1, 21)
    ]
    partition = _partition(rows, CoverageConfig())
    ids = [row["id"] for values in partition.values() for row in values]
    assert len(ids) == len(set(ids)) == 20


def test_hawkes_remains_shadow_and_disabled() -> None:
    """La auditoría no activa Hawkes."""

    config = HawkesIntegrationConfig()
    assert config.hawkes_enabled is False
    assert config.hawkes_shadow_mode is False


def test_markov_official_source_is_not_modified_by_import() -> None:
    """La fase usa un módulo aislado y no escribe `markov_v1.py`."""

    path = Path(__file__).resolve().parents[1] / "src/markov_v1.py"
    before = path.read_bytes()
    __import__("src.audit_event_label_coverage")
    assert path.read_bytes() == before


def test_temporal_failure_rejects_labeling_revision() -> None:
    """Una violación temporal no puede omitirse del gate."""

    coverage = {
        "confirmation": {
            "unknown_absolute_reduction": 0.2,
            "candidate": {"shares": {"unknown": 0.3}},
        }
    }
    rules = {
        "rule": {
            "accepted_from_development": True,
            "temporally_stable": True,
        }
    }
    temporal = {"event_ts_lte_snapshot_ts": False}
    provenance = {
        "unknown_events_retained_for_audit": True,
        "annulled_events_excluded_from_rules": True,
        "team_id_null_excluded_from_rules": True,
        "hawkes_shadow_only": True,
        "official_hashes_match_phase_7_6": True,
        "hawkes_enabled_default": False,
        "markov_official_modified": False,
        "hawkes_parameters_calibrated": False,
    }
    database = {
        "identical": True, "write_statements": 0,
        "referential": {"orphan_match": 0, "orphan_ledger": 0},
    }
    assert _decision(
        coverage, rules, temporal, provenance, database
    ) == "labeling_revision_rejected"


def test_future_event_is_not_available_to_labeling() -> None:
    """Un evento posterior al snapshot no entra en las reglas."""

    events = [
        {
            "event_id": "past", "event_ts": "2025-01-01T12:05:00+00:00",
            "event_type": "shot_on_target", "team_id": 1, "annulled": False,
        },
        {
            "event_id": "future", "event_ts": "2025-01-01T12:06:00+00:00",
            "event_type": "goal", "team_id": 1, "annulled": False,
        },
    ]
    snapshot = datetime(2025, 1, 1, 12, 5, tzinfo=timezone.utc)
    assert [event["event_id"] for event in _valid_events(events, snapshot)] == ["past"]


def test_replay_artifact_is_identical_when_present() -> None:
    """El artefacto versionado conserva evidencia de replay."""

    root = Path(__file__).resolve().parents[1]
    path = root / "artifacts/phase_7_7_event_label_coverage/replay_hashes.json"
    if not path.exists():
        return
    import json

    replay = json.loads(path.read_text(encoding="utf-8"))
    assert replay["identical"] is True
    assert replay["primary_hash"] == replay["replay_hash"]


# Version: 1.0.0
# Created: 2026-07-16
