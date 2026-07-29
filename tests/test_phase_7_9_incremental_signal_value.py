"""Pruebas aisladas de valor incremental in-play."""

from __future__ import annotations

import json
from pathlib import Path

from src.evaluate_incremental_signal_value import (
    APPROVED_RULES,
    PRESSURE_RULES,
    SCORE_RULES,
    _auc,
    _aggregate_match_metrics,
    _future_events,
    _detection_metrics,
    _signal_definitions,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_9_incremental_signal_value"


def test_signal_definitions_are_frozen_without_combinations() -> None:
    """Las señales se congelan y no se combinan post-hoc."""

    definitions = _signal_definitions()
    assert definitions["frozen_before_confirmation"] is True
    assert definitions["combinations_evaluated"] == []
    assert definitions["probability_transformation"] is None
    assert SCORE_RULES | PRESSURE_RULES == APPROVED_RULES


def test_future_target_is_strictly_after_snapshot() -> None:
    """El target excluye eventos presentes y posteriores al horizonte."""

    events = [
        {"event_ts": "2025-01-01T12:00:00+00:00", "annulled": False, "team_id": 1},
        {"event_ts": "2025-01-01T12:05:00+00:00", "annulled": False, "team_id": 1},
        {"event_ts": "2025-01-01T12:11:00+00:00", "annulled": False, "team_id": 1},
    ]
    result = _future_events(events, "2025-01-01T12:00:00+00:00", 10)
    assert [row["event_ts"] for row in result] == ["2025-01-01T12:05:00+00:00"]


def test_auc_uses_ranking_not_probability_conversion() -> None:
    """La anticipación se evalúa como ranking reproducible."""

    assert _auc([False, True, False, True], [0.1, 0.8, 0.2, 0.9]) == 1.0
    assert _auc([False, False], [0.1, 0.2]) is None


def test_match_aggregation_weights_matches_equally() -> None:
    """El agregado no pondera partidos por cantidad de snapshots."""

    models = {
        model: {"mae_total": value, "log_score_total": value}
        for model, value in zip(("base", "official", "candidate", "hawkes"), (1, 2, 3, 4))
    }
    rows = [
        {"block": "confirmation", **models},
        {"block": "confirmation", **models},
    ]
    result = _aggregate_match_metrics(rows, "confirmation")
    assert result["official"]["mae_total"] == 2
    assert result["hawkes"]["log_score_total"] == 4


def test_detection_reports_balanced_skill() -> None:
    """El gate no favorece una señal por baja tasa de activación."""

    rows = []
    for target, active in ((True, True), (True, False), (False, False), (False, False)):
        rows.append({
            "regime_change_10m": target, "candidate_signal_active": active,
            "lambda_candidate_home": 1.25 if active else 1.0,
            "lambda_candidate_away": 1.0,
            "lambda_base_home": 1.0, "lambda_base_away": 1.0,
        })
    metrics = _detection_metrics(rows, "candidate")
    assert metrics["balanced_accuracy"] == 0.75
    assert metrics["balanced_skill_vs_no_signal"] == 0.25


def test_generated_artifacts_preserve_official_output() -> None:
    """La evaluación no promueve señales ni activa Hawkes."""

    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    provenance = audit["provenance"]
    assert provenance["official_output_modified"] is False
    assert provenance["candidate_rules_promoted"] is False
    assert provenance["hawkes_enabled_default"] is False
    assert provenance["markov_independent_of_hawkes"] is True
    assert provenance["blocked_match_704766_excluded"] is True
    assert audit["numeric"]["probabilities_generated"] is False


def test_generated_artifacts_are_reproducible_and_read_only() -> None:
    """Replay y PostgreSQL satisfacen el contrato de seguridad."""

    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    assert manifest["replay_identical"] is True
    assert manifest["output_hash"] == manifest["replay_hash"]
    assert all(audit["temporal"].values())
    assert audit["database"]["before"] == audit["database"]["after"]
    assert audit["database"]["select_only"] is True
    assert audit["database"]["write_statements"] == 0


# Version: 1.0.0
# Created: 2026-07-16
