"""Pruebas de causalidad y artefactos de Fase 22."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_22_passes_causal_gate() -> None:
    """Ningún historial de features puede contener el kickoff objetivo."""

    output = ROOT / "artifacts/phase_22_prematch_first_half_signal"
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert audit["temporal_causality_pass"] is True
    assert audit["target_match_data_used"] is False
    assert audit["markov_used_as_feature"] is False


def test_phase_22_has_complete_temporal_partitions() -> None:
    """Las cohortes están cubiertas y no se solapan."""

    output = ROOT / "artifacts/phase_22_prematch_first_half_signal"
    calibration = json.loads((output / "calibration.json").read_text(encoding="utf-8"))
    confirmation = json.loads((output / "confirmation.json").read_text(encoding="utf-8"))
    assert calibration["partition"]["evaluation_count"] == 44
    assert confirmation["partition"]["evaluation_count"] == 241
    assert calibration["partition"]["overlap"] == []
    assert confirmation["partition"]["overlap"] == []


def test_phase_22_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a todos los artefactos."""

    output = ROOT / "artifacts/phase_22_prematch_first_half_signal"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
