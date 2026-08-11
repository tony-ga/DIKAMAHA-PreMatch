"""Pruebas de la conexión fail-open de calibración de mercados en el runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.market_calibration import ArtifactMarketCalibrationProvider
from src.team_count_market_runtime import (
    ArtifactTeamCountMarketProvider,
    _market_calibration_counts,
)


def _matches(counts: list[int]) -> list[dict[str, dict[str, float]]]:
    """Construye partidos con un solo valor de córners local por partido."""

    return [{"home": {"corners": float(value)}} for value in counts]


def test_market_calibration_counts_matches_threshold_crossings() -> None:
    """Cuenta exactamente cuántos partidos superan la línea del mercado."""

    matches = _matches([2, 5, 6, 4, 7])  # línea 4.5 -> 3 de 5 superan

    positives, totals = _market_calibration_counts(
        "home_corners_over_4_5", matches)

    assert positives == 3.0
    assert totals == 5.0


def _write_calibrator(directory: Path, market: str) -> None:
    payload = {
        "version": "market_league_shrinkage_v1", "market": market,
        "prior_rate": 0.6, "shrinkage": 200.0,
    }
    serialized = json.dumps(payload).encode("utf-8")
    (directory / f"{market}.json").write_bytes(serialized)
    (directory / "hashes.json").write_text(json.dumps({
        f"{market}.json": hashlib.sha256(serialized).hexdigest(),
    }), encoding="utf-8")


def test_apply_market_calibration_overrides_the_configured_market(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Con un calibrador válido y el mercado habilitado, sustituye la probabilidad."""

    import src.team_count_market_runtime as runtime
    _write_calibrator(tmp_path, "home_corners_over_4_5")
    monkeypatch.setattr(
        runtime, "PHASE119_CORRECTED_MARKETS", frozenset({"home_corners_over_4_5"}))
    provider = ArtifactTeamCountMarketProvider(
        calibration_provider=ArtifactMarketCalibrationProvider(tmp_path))
    matches = _matches([2, 5, 6, 4, 7])
    probabilities = {"home_corners_over_4_5": 0.111}
    request = type("Request", (), {"league_slug": "esp.1"})()

    applied = provider._apply_market_calibration(request, matches, probabilities)

    assert applied == ["home_corners_over_4_5"]
    expected = (3.0 + 200.0 * 0.6) / (5.0 + 200.0)
    assert probabilities["home_corners_over_4_5"] == pytest.approx(expected)


def test_apply_market_calibration_falls_back_when_artifact_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Sin calibrador congelado, conserva exactamente la probabilidad original."""

    import src.team_count_market_runtime as runtime
    monkeypatch.setattr(
        runtime, "PHASE119_CORRECTED_MARKETS", frozenset({"home_corners_over_4_5"}))
    provider = ArtifactTeamCountMarketProvider(
        calibration_provider=ArtifactMarketCalibrationProvider(tmp_path))
    matches = _matches([2, 5, 6, 4, 7])
    probabilities = {"home_corners_over_4_5": 0.111}
    request = type("Request", (), {"league_slug": "esp.1"})()

    applied = provider._apply_market_calibration(request, matches, probabilities)

    assert applied == []
    assert probabilities["home_corners_over_4_5"] == 0.111


def test_apply_market_calibration_falls_back_on_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Un calibrador manipulado nunca se aplica; el valor original se conserva."""

    import src.team_count_market_runtime as runtime
    (tmp_path / "home_corners_over_4_5.json").write_text(
        json.dumps({"version": "market_league_shrinkage_v1"}), encoding="utf-8")
    (tmp_path / "hashes.json").write_text(
        json.dumps({"home_corners_over_4_5.json": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(
        runtime, "PHASE119_CORRECTED_MARKETS", frozenset({"home_corners_over_4_5"}))
    provider = ArtifactTeamCountMarketProvider(
        calibration_provider=ArtifactMarketCalibrationProvider(tmp_path))
    matches = _matches([2, 5, 6, 4, 7])
    probabilities = {"home_corners_over_4_5": 0.111}
    request = type("Request", (), {"league_slug": "esp.1"})()

    applied = provider._apply_market_calibration(request, matches, probabilities)

    assert applied == []
    assert probabilities["home_corners_over_4_5"] == 0.111


def test_no_markets_configured_leaves_probabilities_untouched() -> None:
    """El frozenset vacío por defecto no aplica ninguna corrección."""

    provider = ArtifactTeamCountMarketProvider()
    matches = _matches([2, 5, 6, 4, 7])
    probabilities = {"home_corners_over_4_5": 0.111}
    request = type("Request", (), {"league_slug": "esp.1"})()

    applied = provider._apply_market_calibration(request, matches, probabilities)

    assert applied == []
    assert probabilities["home_corners_over_4_5"] == 0.111


# Version: 1.0.0
# Created: 2026-08-10
