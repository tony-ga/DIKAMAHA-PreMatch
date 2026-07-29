"""Pruebas contractuales de la evaluación OOS ampliada."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_13_is_complete_but_not_promoted() -> None:
    """La evaluación cubre toda la extensión y mantiene bloqueada la promoción."""

    output = ROOT / "artifacts/phase_13_temporal_target_evaluation_extension"
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
    assert audit["classification"] == "rejected_for_revision"
    assert audit["prior_coverage_complete"] is True
    assert audit["prediction_coverage_complete"] is True
    assert audit["markets_promoted"] is False
    assert coverage["confirmation_match_count"] == 241


def test_phase_13_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a los archivos de salida."""

    output = ROOT / "artifacts/phase_13_temporal_target_evaluation_extension"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
