"""Pruebas de la calibración causal genérica de Fase 119."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.market_calibration import (
    ArtifactMarketCalibrationProvider,
    league_shrinkage_probability,
)


def test_league_shrinkage_probability_matches_hand_calculation() -> None:
    """Reproduce exactamente la fórmula Beta-binomial de Fase 106."""

    probability = league_shrinkage_probability(
        positives=7.0, totals=20.0, shrinkage=100.0, prior_rate=0.5)

    assert probability == pytest.approx((7.0 + 50.0) / 120.0)


def test_league_shrinkage_probability_rejects_invalid_parameters() -> None:
    """Umbral defensivo igual que el estimador BTTS: nunca silencioso."""

    with pytest.raises(ValueError, match="shrinkage_invalid"):
        league_shrinkage_probability(1.0, 10.0, shrinkage=0.0, prior_rate=0.5)
    with pytest.raises(ValueError, match="prior_rate_invalid"):
        league_shrinkage_probability(1.0, 10.0, shrinkage=100.0, prior_rate=1.5)
    with pytest.raises(ValueError, match="history_invalid"):
        league_shrinkage_probability(1.0, -5.0, shrinkage=100.0, prior_rate=0.5)


def _write_calibrator(
    directory: Path, market: str, *, shrinkage: float = 240.0,
    prior_rate: float = 0.5,
) -> None:
    payload = {
        "version": "market_league_shrinkage_v1",
        "market": market,
        "prior_rate": prior_rate,
        "shrinkage": shrinkage,
    }
    serialized = json.dumps(payload, indent=2).encode("utf-8")
    (directory / f"{market}.json").write_bytes(serialized)
    manifest_path = directory / "hashes.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[f"{market}.json"] = hashlib.sha256(serialized).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_artifact_provider_applies_the_frozen_calibrator(tmp_path: Path) -> None:
    """Calcula exactamente la tasa suavizada sellada para el mercado pedido."""

    _write_calibrator(tmp_path, "away_shots_over_10_5")

    probability, provenance = ArtifactMarketCalibrationProvider(tmp_path).predict(
        "away_shots_over_10_5", "esp.1", positives=9.0, totals=30.0)

    assert probability == pytest.approx((9.0 + 120.0) / 270.0)
    assert provenance["version"] == "market_league_shrinkage_v1"
    assert provenance["market"] == "away_shots_over_10_5"
    assert provenance["league"] == "esp.1"


def test_artifact_provider_isolates_calibrators_by_market(tmp_path: Path) -> None:
    """Un mercado no puede leer accidentalmente el archivo de otro."""

    _write_calibrator(tmp_path, "btts_shadow_repeat", shrinkage=50.0)
    _write_calibrator(tmp_path, "home_corners_over_4_5", shrinkage=999.0)

    provider = ArtifactMarketCalibrationProvider(tmp_path)
    _, first = provider.predict(
        "btts_shadow_repeat", "mex.1", positives=5.0, totals=10.0)
    _, second = provider.predict(
        "home_corners_over_4_5", "mex.1", positives=5.0, totals=10.0)

    assert first["shrinkage"] == 50.0
    assert second["shrinkage"] == 999.0


def test_artifact_provider_rejects_invalid_hash(tmp_path: Path) -> None:
    """Impide usar un calibrador cuyo contenido no coincide con su hash."""

    (tmp_path / "away_shots_over_10_5.json").write_text(
        json.dumps({"version": "market_league_shrinkage_v1"}),
        encoding="utf-8")
    (tmp_path / "hashes.json").write_text(json.dumps({
        "away_shots_over_10_5.json": "0" * 64,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="market_calibration_hash_mismatch"):
        ArtifactMarketCalibrationProvider(tmp_path).predict(
            "away_shots_over_10_5", "esp.1", 1.0, 10.0)


def test_artifact_provider_rejects_market_not_in_manifest(tmp_path: Path) -> None:
    """Un archivo presente pero ausente del manifiesto no es de confianza."""

    _write_calibrator(tmp_path, "btts")
    (tmp_path / "hashes.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="hash_manifest_incomplete"):
        ArtifactMarketCalibrationProvider(tmp_path).predict("btts", "esp.1", 1.0, 10.0)


def test_artifact_provider_rejects_invalid_numeric_config_with_valid_hash(
    tmp_path: Path,
) -> None:
    """Un shrinkage NaN sellado sigue siendo matemáticamente inválido."""

    payload = {
        "version": "market_league_shrinkage_v1",
        "market": "over_2_5",
        "prior_rate": 0.5,
        "shrinkage": float("nan"),
    }
    serialized = json.dumps(payload).encode("utf-8")
    (tmp_path / "over_2_5.json").write_bytes(serialized)
    (tmp_path / "hashes.json").write_text(json.dumps({
        "over_2_5.json": hashlib.sha256(serialized).hexdigest(),
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="shrinkage_invalid"):
        ArtifactMarketCalibrationProvider(tmp_path).predict(
            "over_2_5", "esp.1", 1.0, 10.0)


def test_artifact_provider_rejects_mismatched_market_field(tmp_path: Path) -> None:
    """El campo `market` dentro del archivo debe coincidir con su nombre."""

    payload = {
        "version": "market_league_shrinkage_v1",
        "market": "btts", "prior_rate": 0.5, "shrinkage": 200.0,
    }
    serialized = json.dumps(payload).encode("utf-8")
    (tmp_path / "over_2_5.json").write_bytes(serialized)
    (tmp_path / "hashes.json").write_text(json.dumps({
        "over_2_5.json": hashlib.sha256(serialized).hexdigest(),
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="market_calibration_market_mismatch"):
        ArtifactMarketCalibrationProvider(tmp_path).predict(
            "over_2_5", "esp.1", 1.0, 10.0)


def test_runtime_accepts_git_line_endings(tmp_path: Path) -> None:
    """Valida sólo el calibrador ejecutable y tolera CRLF frente a LF."""

    payload = {
        "version": "market_league_shrinkage_v1",
        "market": "btts_shadow_repeat", "prior_rate": 0.5, "shrinkage": 240.0,
    }
    serialized_lf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    (tmp_path / "btts_shadow_repeat.json").write_bytes(serialized_lf)
    serialized_crlf = serialized_lf.replace(b"\n", b"\r\n")
    (tmp_path / "hashes.json").write_text(json.dumps({
        "btts_shadow_repeat.json": hashlib.sha256(serialized_crlf).hexdigest(),
    }), encoding="utf-8")

    probability, provenance = ArtifactMarketCalibrationProvider(tmp_path).predict(
        "btts_shadow_repeat", "esp.1", positives=6.0, totals=14.0)

    assert probability == pytest.approx((6.0 + 120.0) / 254.0)
    assert provenance["version"] == "market_league_shrinkage_v1"


# Version: 1.0.0
# Created: 2026-08-10
