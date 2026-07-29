"""Pruebas del selector temporal por target."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_router_selection_is_calibration_only() -> None:
    """El selector cubre confirmación y no usa resultados para elegir."""

    output = ROOT / "artifacts/phase_21_target_model_router"
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert audit["selection_source"] == "phase20_calibration_only"
    assert audit["confirmation_outcomes_used_for_selection"] is False
    assert audit["coverage_complete"] is True
    assert audit["markets_promoted"] is False


def test_router_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a las salidas."""

    output = ROOT / "artifacts/phase_21_target_model_router"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
