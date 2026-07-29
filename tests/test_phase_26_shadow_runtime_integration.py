"""Pruebas de la integración runtime del catálogo shadow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.prematch_shadow_catalog import build_shadow_observation, load_shadow_catalog

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_26_shadow_runtime_integration"


def test_phase26_audit_is_ready_and_read_only() -> None:
    """La evidencia publicada confirma el gate de sólo lectura."""

    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    assert audit["classification"] == "ready_for_next_phase"
    assert audit["official_prediction_values_unchanged"] is True
    assert audit["candidate_outputs_computed"] is False
    assert audit["target_match_data_used"] is False


def test_runtime_observation_does_not_expose_candidate_metrics() -> None:
    """La respuesta runtime sólo incluye estado y trazabilidad sanitizada."""

    observation = build_shadow_observation(load_shadow_catalog())
    assert observation["mode"] == "read_only"
    assert all("metrics" not in candidate for candidate in observation["candidates"])
    assert all(candidate["official_output_allowed"] is False for candidate in observation["candidates"])


def test_phase26_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a todos los artefactos de salida."""

    hashes = json.loads((OUTPUT / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())


# Version: 1.0.0
# Created: 2026-07-26
