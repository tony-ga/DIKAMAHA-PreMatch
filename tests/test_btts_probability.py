"""Pruebas del estimador causal BTTS de Fase 106."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.btts_probability import ArtifactBttsProbabilityProvider


def _matches() -> list[dict[str, int]]:
    """Construye historia mínima con dos outcomes BTTS positivos."""

    return [
        {"home_goals": 1, "away_goals": 1},
        {"home_goals": 2, "away_goals": 0},
        {"home_goals": 3, "away_goals": 2},
    ]


def test_artifact_provider_applies_frozen_league_pooling() -> None:
    """Calcula exactamente la tasa suavizada sellada."""

    probability, provenance = ArtifactBttsProbabilityProvider().predict(
        _matches())
    expected = (2.0 + 500.0 * 0.5) / 503.0
    assert probability == pytest.approx(expected)
    assert provenance["version"] == "btts_league_rate_v1"
    assert provenance["history_matches"] == 3


def test_artifact_provider_rejects_invalid_hash(tmp_path: Path) -> None:
    """Impide usar parámetros manipulados."""

    (tmp_path / "calibrator.json").write_text(
        json.dumps({"version": "btts_league_rate_v1"}),
        encoding="utf-8")
    (tmp_path / "hashes.json").write_text(
        json.dumps({"calibrator.json": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="btts_calibrator_hash_mismatch"):
        ArtifactBttsProbabilityProvider(tmp_path).predict(_matches())


# Version: 1.0.0
# Created: 2026-07-29
