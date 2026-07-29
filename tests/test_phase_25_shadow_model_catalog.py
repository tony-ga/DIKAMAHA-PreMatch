"""Pruebas del catálogo shadow."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shadow_candidates_are_disabled() -> None:
    """Ningún candidato experimental puede ser oficial por defecto."""

    contract = json.loads((ROOT / "artifacts/phase_25_shadow_model_catalog/shadow_contract.json").read_text(encoding="utf-8"))
    assert all(item["enabled_by_default"] is False for item in contract["candidates"])
    assert all(item["official_output_allowed"] is False for item in contract["candidates"])
    assert contract["market_promotion"] is False


def test_shadow_router_is_unchanged() -> None:
    """El catálogo conserva el router oficial vigente."""

    audit = json.loads((ROOT / "artifacts/phase_25_shadow_model_catalog/audit.json").read_text(encoding="utf-8"))
    assert audit["official_router_unchanged"] is True
    assert audit["experimental_candidates_disabled"] is True


def test_shadow_hashes_are_consistent() -> None:
    """Los hashes publicados corresponden a las salidas."""

    output = ROOT / "artifacts/phase_25_shadow_model_catalog"
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())

# Version: 1.0.0
# Created: 2026-07-26
