"""Pruebas de la observación read-only de Fase 27."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_27_shadow_observation"


def test_phase27_is_ready_and_complete() -> None:
    """La cohorte oficial queda completamente observada."""

    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    coverage = json.loads((OUTPUT / "coverage.json").read_text(encoding="utf-8"))
    assert audit["classification"] == "ready_for_next_phase"
    assert coverage["official_predictions"] == coverage["observations_published"] == 241
    assert audit["candidate_outputs_computed"] is False
    assert audit["target_match_data_used"] is False


def test_phase27_observations_exclude_targets_and_losses() -> None:
    """La observación no publica campos derivados del resultado final."""

    observations = json.loads((OUTPUT / "observations.json").read_text(encoding="utf-8"))
    forbidden = {"target_first_half_goal", "target_second_half_goal", "feature_loss", "markov_loss", "baseline_loss"}
    assert all(not forbidden.intersection(item["official"]) for item in observations)
    assert all(item["shadow"]["candidate_outputs_computed"] is False for item in observations)


def test_phase27_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a los artefactos."""

    hashes = json.loads((OUTPUT / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())


# Version: 1.0.0
# Created: 2026-07-26
