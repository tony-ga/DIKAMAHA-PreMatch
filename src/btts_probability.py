"""Proveedor causal de BTTS con pooling conservador por liga.

# Requirements:
#   Python>=3.10

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import math
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
        outcomes = []
        for row in matches:
            goals = (row["home_goals"], row["away_goals"])
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) or float(value) < 0.0
                   or float(value) % 1 != 0 for value in goals):
                raise ValueError("btts_history_goals_invalid")
            outcomes.append(float(goals[0] > 0 and goals[1] > 0))
        shrinkage = float(config["league_shrinkage"])
        prior = float(config["prior_rate"])
        probability = (sum(outcomes) + shrinkage * prior) / (
            len(outcomes) + shrinkage)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise FloatingPointError("btts_probability_invalid")
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
        if not isinstance(hashes, dict) or calibrator.name not in hashes:
            raise ValueError("btts_hash_manifest_incomplete")
        for name, expected in hashes.items():
            path = self._path / str(name)
            if Path(str(name)).name != str(name) or not path.is_file() or (
                    not isinstance(expected, str) or _sha(path) != expected):
                if str(name) == calibrator.name:
                    raise ValueError("btts_calibrator_hash_mismatch")
                raise ValueError(f"btts_artifact_hash_mismatch:{name}")
        self._config = json.loads(calibrator.read_text(encoding="utf-8"))
        if self._config.get("version") != "btts_league_rate_v1":
            raise ValueError("btts_calibrator_version_mismatch")
        if self._config.get("model") != "causal_league_btts_rate" or (
                self._config.get("market") != "btts"):
            raise ValueError("btts_calibrator_model_mismatch")
        prior = self._config.get("prior_rate")
        shrinkage = self._config.get("league_shrinkage")
        if (isinstance(prior, bool) or not isinstance(prior, (int, float))
                or not math.isfinite(float(prior))
                or not 0.0 <= float(prior) <= 1.0):
            raise ValueError("btts_prior_rate_invalid")
        if (isinstance(shrinkage, bool)
                or not isinstance(shrinkage, (int, float))
                or not math.isfinite(float(shrinkage))
                or float(shrinkage) <= 0.0):
            raise ValueError("btts_shrinkage_invalid")
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
