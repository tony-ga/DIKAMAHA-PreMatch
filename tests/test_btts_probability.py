"""Pruebas del estimador causal BTTS de Fase 106."""

from __future__ import annotations

import json
import hashlib
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


def test_artifact_provider_rejects_invalid_numeric_config_with_valid_hash(
    tmp_path: Path,
) -> None:
    """Un prior NaN sellado sigue siendo matemáticamente inválido."""

    calibrator = tmp_path / "calibrator.json"
    calibrator.write_text(json.dumps({
        "version": "btts_league_rate_v1",
        "model": "causal_league_btts_rate",
        "market": "btts",
        "prior_rate": float("nan"),
        "league_shrinkage": 500,
    }), encoding="utf-8")
    (tmp_path / "hashes.json").write_text(json.dumps({
        "calibrator.json": hashlib.sha256(
            calibrator.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="prior_rate_invalid"):
        ArtifactBttsProbabilityProvider(tmp_path).predict(_matches())


def test_runtime_accepts_git_line_endings_and_ignores_unshipped_evidence(
    tmp_path: Path,
) -> None:
    """Valida sólo el calibrador ejecutable y tolera CRLF frente a LF."""

    payload = {
        "version": "btts_league_rate_v1",
        "model": "causal_league_btts_rate",
        "market": "btts",
        "prior_rate": 0.5,
        "league_shrinkage": 500,
    }
    serialized_lf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    (tmp_path / "calibrator.json").write_bytes(serialized_lf)
    serialized_crlf = serialized_lf.replace(b"\n", b"\r\n")
    (tmp_path / "hashes.json").write_text(json.dumps({
        "calibrator.json": hashlib.sha256(serialized_crlf).hexdigest(),
        "audit.json": "0" * 64,
    }), encoding="utf-8")

    probability, provenance = ArtifactBttsProbabilityProvider(
        tmp_path).predict(_matches())

    assert probability == pytest.approx((2.0 + 250.0) / 503.0)
    assert provenance["version"] == "btts_league_rate_v1"


# Version: 1.0.0
# Created: 2026-07-29
