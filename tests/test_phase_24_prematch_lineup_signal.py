"""Pruebas de Fase 24."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_24_is_causal_and_does_not_use_odds() -> None:
    """Las features son pre-match y las cuotas quedan fuera."""

    audit = json.loads((ROOT / "artifacts/phase_24_prematch_lineup_signal/audit.json").read_text(encoding="utf-8"))
    assert audit["temporal_causality_pass"] is True
    assert audit["target_match_data_used"] is False
    assert audit["odds_used"] is False


def test_phase_24_partitions_are_complete() -> None:
    """La fase conserva los cortes temporales congelados."""

    calibration = json.loads((ROOT / "artifacts/phase_24_prematch_lineup_signal/calibration.json").read_text(encoding="utf-8"))
    confirmation = json.loads((ROOT / "artifacts/phase_24_prematch_lineup_signal/confirmation.json").read_text(encoding="utf-8"))
    assert calibration["partition"]["evaluation_count"] == 44
    assert confirmation["partition"]["evaluation_count"] == 241
    assert calibration["partition"]["overlap"] == []
    assert confirmation["partition"]["overlap"] == []


def test_phase_24_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a las salidas."""

    output = ROOT / "artifacts/phase_24_prematch_lineup_signal"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
