"""Pruebas del lock y gate ciego prospectivo v3.

Version: 1.0.0
Created: 2026-07-28
"""
import hashlib
import json

from scripts import gate_phase_76_v3_prospective as gate
from scripts import lock_phase_76_v3_prospective as lock


def test_existing_lock_verifies_frozen_model_hash() -> None:
    """Repetir el sellado conserva exactamente el cutoff original."""

    before = json.loads(lock.LOCK.read_text(encoding="utf-8"))
    after = lock.run()
    assert after == before
    assert after["model_parameters_sha256"] == hashlib.sha256(
        lock.MODEL.read_bytes()
    ).hexdigest()


def test_blind_gate_does_not_read_outcomes_before_coverage() -> None:
    """Mantiene selladas las métricas mientras no alcanza 200/10."""

    result = gate.run()
    if result["classification"] == "insufficient_coverage":
        assert result["metrics_sealed"] is True
        assert result["outcomes_read"] is False
    assert result["model_hash_verified"] is True

# Version: 1.0.0 - 2026-07-28
