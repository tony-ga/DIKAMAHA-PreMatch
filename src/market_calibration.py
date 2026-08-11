"""Calibración causal genérica por shrinkage bayesiano de liga.

Generaliza la fórmula de Fase 106 (`src/btts_probability.py`) a cualquier
mercado binario servido hoy, sin duplicar la clase completa de proveedor por
cada mercado nuevo. Un mercado nuevo sólo aporta un artefacto congelado
(`calibrators/<market>.json`) con su propio `shrinkage`/`prior_rate`; la
aritmética y la validación de hash son un único lugar de verdad.

Version: 1.0.0
Created: 2026-08-10
"""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.artifact_integrity import artifact_hash_matches

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts/phase_119_bias_backtest_500/calibrators"
CONTRACT_VERSION = "market_league_shrinkage_v1"


def league_shrinkage_probability(
    positives: float, totals: float, shrinkage: float, prior_rate: float,
) -> float:
    """Contrae la tasa causal de liga hacia el prior con un prior beta.

    Misma fórmula exacta que ya usa `ArtifactBttsProbabilityProvider`,
    generalizada a cualquier mercado binario.
    """

    if not math.isfinite(shrinkage) or shrinkage <= 0.0:
        raise ValueError("market_calibration_shrinkage_invalid")
    if not math.isfinite(prior_rate) or not 0.0 <= prior_rate <= 1.0:
        raise ValueError("market_calibration_prior_rate_invalid")
    if not math.isfinite(positives) or not math.isfinite(totals) or totals < 0.0:
        raise ValueError("market_calibration_history_invalid")
    probability = (positives + shrinkage * prior_rate) / (totals + shrinkage)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise FloatingPointError("market_calibration_probability_invalid")
    return probability


class MarketCalibrationPort(ABC):
    """Puerto de recalibración causal para un mercado servido."""

    @abstractmethod
    def predict(
        self, market: str, league: str, positives: float, totals: float,
    ) -> tuple[float, dict[str, Any]]:
        """Calibra la tasa de un mercado a partir de su historia de liga."""


class ArtifactMarketCalibrationProvider(MarketCalibrationPort):
    """Carga calibradores sellados por mercado, uno por archivo JSON.

    Cada archivo `<market>.json` es independiente y versionado igual que el
    calibrador BTTS de Fase 106; el hash de cada uno se valida contra el
    mismo `hashes.json` de la carpeta antes de usarse.
    """

    def __init__(self, artifact_dir: Path | None = None) -> None:
        """Configura el directorio de calibradores congelados."""

        self._dir = artifact_dir or DEFAULT_ARTIFACT_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def predict(
        self, market: str, league: str, positives: float, totals: float,
    ) -> tuple[float, dict[str, Any]]:
        """Aplica el shrinkage congelado del mercado solicitado."""

        config = self._load(market)
        shrinkage = float(config["shrinkage"])
        prior_rate = float(config["prior_rate"])
        probability = league_shrinkage_probability(
            positives, totals, shrinkage, prior_rate)
        return probability, {
            "model": "market_league_shrinkage",
            "version": str(config["version"]),
            "market": market,
            "league": league,
            "shrinkage": shrinkage,
            "prior_rate": prior_rate,
            "history_matches": totals,
        }

    def _load(self, market: str) -> dict[str, Any]:
        """Valida hash y carga el contrato congelado de un mercado."""

        if market in self._cache:
            return self._cache[market]
        hashes_path = self._dir / "hashes.json"
        calibrator_path = self._dir / f"{market}.json"
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        if not isinstance(hashes, dict) or calibrator_path.name not in hashes:
            raise ValueError("market_calibration_hash_manifest_incomplete")
        expected = hashes[calibrator_path.name]
        if not calibrator_path.is_file() or not artifact_hash_matches(
                calibrator_path, expected):
            raise ValueError("market_calibration_hash_mismatch")
        config = json.loads(calibrator_path.read_text(encoding="utf-8"))
        if config.get("version") != CONTRACT_VERSION:
            raise ValueError("market_calibration_version_mismatch")
        if config.get("market") != market:
            raise ValueError("market_calibration_market_mismatch")
        prior_rate = config.get("prior_rate")
        shrinkage = config.get("shrinkage")
        if (isinstance(prior_rate, bool)
                or not isinstance(prior_rate, (int, float))
                or not math.isfinite(float(prior_rate))
                or not 0.0 <= float(prior_rate) <= 1.0):
            raise ValueError("market_calibration_prior_rate_invalid")
        if (isinstance(shrinkage, bool)
                or not isinstance(shrinkage, (int, float))
                or not math.isfinite(float(shrinkage))
                or float(shrinkage) <= 0.0):
            raise ValueError("market_calibration_shrinkage_invalid")
        self._cache[market] = config
        return config


# Version: 1.0.0
# Created: 2026-08-10
