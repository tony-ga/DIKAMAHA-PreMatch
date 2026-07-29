"""Pruebas de contrato de Fase 23."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase_23_excludes_postmatch_inputs() -> None:
    """La captura no usa estadísticas objetivo ni cuotas live."""

    audit = json.loads((ROOT / "artifacts/phase_23_prematch_context_fetch/audit.json").read_text(encoding="utf-8"))
    assert audit["target_match_statistics_used"] is False
    assert audit["forbidden_current_or_close_odds_used"] is False
    assert audit["live_odds_excluded"] is True


def test_phase_23_covers_clean_phase_22_rows() -> None:
    """La captura conserva la cobertura de la cohorte limpia."""

    coverage = json.loads((ROOT / "artifacts/phase_23_prematch_context_fetch/coverage.json").read_text(encoding="utf-8"))
    assert coverage["input_matches"] == 1140
    assert coverage["summary_ok"] == 1140
    assert coverage["identity_ok"] == 1140


def test_phase_23_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a los entregables."""

    output = ROOT / "artifacts/phase_23_prematch_context_fetch"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
