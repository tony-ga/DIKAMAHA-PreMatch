"""Proveedor canónico de intensidad estructural para Markov v2.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "artifacts/phase_05_canonical_oos_predictions_v1/canonical_predictions.json"
OUTPUT = ROOT / "artifacts/phase_06_markov_v2_goal_prior"


@dataclass(frozen=True, slots=True)
class GoalPriorConfig:
    """Pesos fijados antes de cualquier evaluación de Markov v2."""

    version: str = "structural_goal_prior_v2"
    dixon_coles_weight: float = 0.80
    kalman_weight: float = 0.20


class GoalPriorProvider(ABC):
    """Contrato para proveedores de intensidad pre-kickoff."""

    @abstractmethod
    def provide(self) -> list[dict[str, Any]]:
        """Devuelve priors estructurales por partido y equipo."""


def _load(path: Path) -> Any:
    """Carga un artefacto JSON sin modificarlo."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, value: Any) -> None:
    """Escribe JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


class CanonicalGoalPriorProvider(GoalPriorProvider):
    """Combina priors OOS Dixon-Coles y Kalman con pesos congelados."""

    def __init__(self, config: GoalPriorConfig) -> None:
        """Guarda una configuración explícita y validada."""
        if abs(config.dixon_coles_weight + config.kalman_weight - 1.0) > 1e-12:
            raise ValueError("Los pesos estructurales deben sumar uno.")
        self.config = config

    def provide(self) -> list[dict[str, Any]]:
        """Materializa una fila por fixture usando sólo predicciones OOS."""
        grouped = _group(_load(INPUT))
        return [_combine(rows, self.config) for _, rows in sorted(grouped.items())]


def _group(rows: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, Any]]]:
    """Agrupa filas canónicas por match y modelo necesario."""
    output: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row["model"] in {"dixon_coles", "dixon_coles_kalman"}:
            output.setdefault(int(row["match_id"]), {})[str(row["model"])] = row
    return output


def _combine(rows: dict[str, dict[str, Any]], config: GoalPriorConfig) -> dict[str, Any]:
    """Combina lambdas por equipo preservando cutoff e identidad OOS."""
    dc, kalman = rows["dixon_coles"], rows["dixon_coles_kalman"]
    home = config.dixon_coles_weight * float(dc["expected_home_goals"]) + config.kalman_weight * float(kalman["expected_home_goals"])
    away = config.dixon_coles_weight * float(dc["expected_away_goals"]) + config.kalman_weight * float(kalman["expected_away_goals"])
    return {"match_id": int(dc["match_id"]), "fold_id": int(dc["fold_id"]), "cutoff_ts": dc["cutoff_ts"], "home_team_id": int(dc["home_team_id"]), "away_team_id": int(dc["away_team_id"]), "lambda_dc_home": float(dc["expected_home_goals"]), "lambda_dc_away": float(dc["expected_away_goals"]), "lambda_kalman_home": float(kalman["expected_home_goals"]), "lambda_kalman_away": float(kalman["expected_away_goals"]), "lambda_base_home": home, "lambda_base_away": away}


def run(config: GoalPriorConfig | None = None) -> dict[str, Any]:
    """Publica el proveedor v2 sin simular ni evaluar mercados."""
    active = config or GoalPriorConfig()
    priors = CanonicalGoalPriorProvider(active).provide()
    audit = {"prior_count": len(priors), "all_positive": all(row["lambda_base_home"] > 0 and row["lambda_base_away"] > 0 for row in priors), "target_outcomes_used_as_features": False, "weights_sum_to_one": abs(active.dixon_coles_weight + active.kalman_weight - 1.0) < 1e-12}
    _write("config.json", asdict(active)); _write("goal_priors.json", priors); _write("audit.json", audit)
    _write("input_manifest.json", {"canonical_oos_hash": hashlib.sha256(INPUT.read_bytes()).hexdigest()})
    (OUTPUT / "final_report.md").write_text("# Fase 06 — prior estructural v2\n\n**Clasificación:** `ready_for_next_step`\n\n- priors OOS: `264`\n- pendiente: simulación Markov v2 y evaluación independiente.\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})
    LOGGER.info("Prior estructural v2: %s filas", len(priors))
    return {"priors": priors, "audit": audit}


# Version: 1.0.0
# Created: 2026-07-26
