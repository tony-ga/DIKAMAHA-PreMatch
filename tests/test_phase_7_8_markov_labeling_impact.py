"""Pruebas aisladas del impacto de etiquetado Markov."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.evaluate_markov_labeling_impact import (
    APPROVED_RULES,
    ImpactConfig,
    _bootstrap_stat,
    _decision,
    _next_state,
    _numeric_audit,
    _state_counts,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_8_markov_labeling_impact"


def test_only_four_audited_rules_are_enabled() -> None:
    """Las reglas de tarjeta roja permanecen fuera."""

    assert APPROVED_RULES == {
        "two_goal_context_after_60", "late_score_context",
        "sustained_pressure_dominance", "sustained_opponent_pressure",
    }
    assert all("red" not in rule for rule in APPROVED_RULES)


def test_unknown_uses_matrix_argmax_fallback() -> None:
    """Unknown no se convierte en estado por conveniencia."""

    assert _next_state(-1, 2) == (2, "matrix_argmax_fallback")
    assert _next_state(1, 2) == (1, "observable_label")


def test_transition_counts_include_both_teams() -> None:
    """Las transiciones se cuentan por equipo y snapshot."""

    rows = [{
        "baseline_before_home": 0, "baseline_after_home": 2,
        "baseline_before_away": 1, "baseline_after_away": 1,
    }]
    counts = _state_counts(rows, "baseline")
    assert counts[0, 2] == 1
    assert counts[1, 1] == 1
    assert counts.sum() == 2


def test_bootstrap_is_deterministic_by_seed() -> None:
    """El bootstrap por partido es reproducible."""

    values = np.asarray([-0.1, 0.0, 0.2, -0.2])
    first = _bootstrap_stat(values, ImpactConfig(bootstrap_replicates=100), 0)
    second = _bootstrap_stat(values, ImpactConfig(bootstrap_replicates=100), 0)
    assert first == second


def test_tactical_multiplier_cannot_exceed_contract() -> None:
    """La rama candidata no puede sobrepasar 1.25."""

    row = {
        "lambda_base_home": 1.0, "lambda_base_away": 1.0,
        "lambda_official_home": 1.0, "lambda_official_away": 1.0,
        "lambda_baseline_home": 0.75, "lambda_baseline_away": 1.25,
        "lambda_candidate_home": 1.25, "lambda_candidate_away": 0.75,
    }
    audit = _numeric_audit([row], ImpactConfig())
    assert audit["positive_finite_intensities"]
    assert audit["multiplier_within_contract"]
    assert audit["overexcitation_beyond_contract_count"] == 0


def test_temporal_failure_rejects_candidate() -> None:
    """Una violación temporal domina cualquier mejora descriptiva."""

    candidate = {"confirmation": {"match_count": 53}}
    coverage = {"blocks": {"confirmation": {"absolute_reduction": 0.20}}}
    bootstrap = {"metrics": {
        key: {"point_estimate": 0.0, "ci_95": [-0.1, 0.1]}
        for key in (
            "candidate_vs_baseline_mae", "candidate_vs_baseline_log",
            "candidate_vs_official_mae", "candidate_vs_official_log",
        )
    }}
    transitions = {"confirmation": {"candidate_sparse_cells": []}}
    audit = {
        "temporal": {"event_ts_lte_snapshot_ts": False},
        "numeric": {"positive_finite_intensities": True},
        "provenance": {
            "official_output_modified": False, "markov_matrix_modified": False,
            "hawkes_enabled_default": False, "hawkes_parameters_calibrated": False,
            "official_markov_hash_unchanged": True,
            "official_inference_hash_unchanged": True,
            "approved_rules_exact": sorted(APPROVED_RULES),
            "red_card_rules_included": False,
        },
        "database": {
            "identical": True, "write_statements": 0,
            "connection_closed": True, "select_only": True,
        },
    }
    assert _decision({}, candidate, coverage, bootstrap, transitions, audit) == (
        "labeling_rules_rejected_for_revision"
    )


def test_generated_artifacts_preserve_official_layers() -> None:
    """El resultado histórico conserva Markov oficial y Hawkes apagado."""

    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    provenance = audit["provenance"]
    assert provenance["official_output_modified"] is False
    assert provenance["markov_matrix_modified"] is False
    assert provenance["hawkes_enabled_default"] is False
    assert provenance["hawkes_parameters_calibrated"] is False
    assert all(audit["temporal"].values())
    assert audit["numeric"]["positive_finite_intensities"] is True


def test_generated_artifacts_replay_and_database_are_safe() -> None:
    """El replay es idéntico y PostgreSQL permanece SELECT-only."""

    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    database = json.loads(
        (OUTPUT / "postgres_readonly_audit.json").read_text(encoding="utf-8")
    )
    assert manifest["replay_identical"] is True
    assert manifest["output_hash"] == manifest["replay_hash"]
    assert database["before"] == database["after"]
    assert database["write_statements"] == 0
    assert database["select_only"] is True


# Version: 1.0.0
# Created: 2026-07-16
