"""Proveedor causal de BTTS con pooling conservador por liga.

# Requirements:
#   Python>=3.10

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = ROOT / "artifacts/phase_106_probability_repair"


class BttsProbabilityPort(ABC):
    """Puerto de probabilidad BTTS para el router universal."""

    @abstractmethod
    def predict(self, matches: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
        """Predice BTTS desde historia anterior al kickoff."""


class ArtifactBttsProbabilityProvider(BttsProbabilityPort):
    """Carga el estimador sellado de Fase 106."""

    def __init__(self, artifact_path: Path | None = None) -> None:
        """Configura la ubicación del artefacto."""

        self._path = artifact_path or DEFAULT_ARTIFACT
        self._config: dict[str, Any] | None = None

    def predict(self, matches: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
        """Calcula tasa BTTS de liga con prior beta congelado."""

        config = self._load()
        outcomes = [
            float(row["home_goals"] > 0 and row["away_goals"] > 0)
            for row in matches]
        shrinkage = float(config["league_shrinkage"])
        prior = float(config["prior_rate"])
        probability = (sum(outcomes) + shrinkage * prior) / (
            len(outcomes) + shrinkage)
        return probability, {
            "model": str(config["model"]),
            "version": str(config["version"]),
            "history_matches": len(outcomes),
            "league_shrinkage": int(shrinkage),
            "artifact_sha256": _sha(self._path / "calibrator.json"),
        }

    def _load(self) -> dict[str, Any]:
        """Valida hash y carga el contrato congelado."""

        if self._config is not None:
            return self._config
        hashes = json.loads(
            (self._path / "hashes.json").read_text(encoding="utf-8"))
        calibrator = self._path / "calibrator.json"
        if _sha(calibrator) != hashes.get(calibrator.name):
            raise ValueError("btts_calibrator_hash_mismatch")
        self._config = json.loads(calibrator.read_text(encoding="utf-8"))
        if self._config.get("version") != "btts_league_rate_v1":
            raise ValueError("btts_calibrator_version_mismatch")
        return self._config


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un artefacto."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    provider = ArtifactBttsProbabilityProvider()
    probability, provenance = provider.predict([])
    assert 0.0 < probability < 1.0
    assert provenance["version"] == "btts_league_rate_v1"
    assert provenance["history_matches"] == 0

# Version: 1.0.0
# Created: 2026-07-29
