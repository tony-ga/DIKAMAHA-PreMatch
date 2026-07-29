"""Pruebas de la evaluación temporal OOS v2."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.phase_10_temporal_target_evaluation import _loss

ROOT = Path(__file__).resolve().parents[1]


def test_binary_log_loss_is_finite_and_directional() -> None:
    """La pérdida permanece finita y premia probabilidades adecuadas."""

    assert _loss(0.9, True) < _loss(0.1, True)
    assert _loss(0.1, False) < _loss(0.9, False)
    assert _loss(0.0, True) > 20.0


def test_phase_10_artifacts_have_complete_prediction_coverage() -> None:
    """La confirmación conserva 44 partidos, hashes e integridad de leakage."""

    output = ROOT / "artifacts/phase_10_temporal_target_evaluation"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert audit["prediction_coverage_complete"] is True
    assert audit["target_outcomes_used_as_features"] is False
    assert audit["promotion_allowed"] is False


# Version: 1.0.0
# Created: 2026-07-26
