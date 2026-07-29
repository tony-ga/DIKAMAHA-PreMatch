"""Pruebas del colector prospectivo in-play."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.run_prospective_signal_shadow import (
    ProspectiveConfig,
    _frozen_config,
    _identity_complete,
    _normalized,
    _observe_snapshot,
    _prior_match_ids,
)
from src.dikamaha_inference import DikamahaInferenceEngine

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_10_prospective_signal_shadow"


def test_prior_universe_excludes_historical_matches() -> None:
    """El universo previo cubre todos los partidos históricos conocidos."""

    ids, sources = _prior_match_ids()
    assert set(range(1, 382)) <= ids
    assert sources


def test_promotion_criteria_are_frozen() -> None:
    """Los umbrales no dependen de resultados prospectivos."""

    payload = _frozen_config(ProspectiveConfig())
    assert payload["recalibration_during_observation"] is False
    assert payload["hawkes_default"]["hawkes_enabled"] is False
    assert payload["promotion_criteria"]["minimum_matches"] == 30


def test_identity_requires_complete_target_and_orientation() -> None:
    """Un partido incompleto no entra silenciosamente en evaluación."""

    row = {"home_team_id": 1, "away_team_id": 2, "match_date": datetime.now(timezone.utc), "home_score": 1, "away_score": 0}
    assert _identity_complete(row)
    row["away_score"] = None
    assert not _identity_complete(row)
    row["away_score"] = 0
    row["away_team_id"] = 1
    assert not _identity_complete(row)


def test_replay_excludes_only_operational_latency() -> None:
    """La telemetría no rompe determinismo matemático."""

    payload = {"observations": [{"service_latency_ms": 1.2, "match_id": 1}], "audit": {"database": {"identical": True}}, "decision": "x"}
    normalized = _normalized(payload)
    assert "service_latency_ms" not in normalized["observations"][0]
    assert "database" not in normalized["audit"]


def test_hawkes_shadow_does_not_replace_markov_output() -> None:
    """Activar shadow explícito conserva exactamente lambda_markov."""

    match = {
        "id": 900001, "home_team_id": 1, "away_team_id": 2,
        "match_date": "2025-11-01T12:00:00+00:00", "home_score": 1, "away_score": 0,
    }
    snapshot = {
        "match_id": 900001, "snapshot_ts": "2025-11-01T12:10:00+00:00",
        "minute": 10, "score_home": 1, "score_away": 0,
    }
    event = {
        "event_id": "e1", "event_ts": "2025-11-01T12:05:00+00:00",
        "event_type": "goal", "team_id": 1, "annulled": False,
    }
    contexts = {
        (900001, snapshot["snapshot_ts"], side): {
            "candidate_state": 2 if side == "home" else 1,
            "resolution_rule": "late_score_context",
        } for side in ("home", "away")
    }
    lambdas = {"lambda_base_home": 1.4, "lambda_base_away": 1.0, "source_hash": "synthetic"}
    engine = DikamahaInferenceEngine()
    disabled = _observe_snapshot(snapshot, match, [event], contexts, lambdas, engine, ProspectiveConfig())
    enabled = _observe_snapshot(snapshot, match, [event], contexts, lambdas, engine, ProspectiveConfig(hawkes_shadow_requested=True))
    assert disabled["lambda_official_home"] == enabled["lambda_official_home"]
    assert disabled["lambda_official_away"] == enabled["lambda_official_away"]
    assert disabled["hawkes_shadow"] is None
    assert enabled["hawkes_shadow"] is not None


def test_empty_prospective_cohort_is_not_reused_history() -> None:
    """La falta de partidos nuevos produce cobertura insuficiente, no backfill."""

    selection = json.loads((OUTPUT / "prospective_selection.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    assert selection["prior_match_count"] == 381
    assert selection["historical_exclusion_id_count"] == 382
    assert selection["new_match_ids"] == []
    assert selection["selected_match_ids"] == []
    assert manifest["classification"] == "insufficient_prospective_coverage"


def test_generated_artifacts_are_reproducible_and_read_only() -> None:
    """El replay coincide y PostgreSQL permanece sin escrituras."""

    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    assert manifest["replay_identical"] is True
    assert manifest["output_hash"] == manifest["replay_hash"]
    assert audit["database"]["before"] == audit["database"]["after"]
    assert audit["database"]["select_only"] is True
    assert audit["database"]["write_statements"] == 0
    assert audit["layers"]["official_output_modified"] is False


# Version: 1.0.0
# Created: 2026-07-16
