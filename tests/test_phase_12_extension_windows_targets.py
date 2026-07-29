"""Pruebas contractuales de la extensión histórica de Fase 12."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_12_validated_and_complete() -> None:
    """La extensión publicada pasa los gates de cobertura y marcador."""

    output = ROOT / "artifacts/phase_12_extension_windows_targets"
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
    assert audit["classification"] == "validated_for_target_revision"
    assert coverage["match_count"] == 241
    assert coverage["window_count"] == 2892


def test_phase_12_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a los artefactos de la fase."""

    output = ROOT / "artifacts/phase_12_extension_windows_targets"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
