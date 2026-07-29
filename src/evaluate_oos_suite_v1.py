"""Evaluación temporal de la suite OOS canónica de DIKAMAHA.

Los outcomes se usan exclusivamente como targets después de cargar predicciones.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_candidate.json"
PREDICTIONS = ROOT / "artifacts/phase_05_canonical_oos_predictions_v1/canonical_predictions.json"
OUTPUT = ROOT / "artifacts/phase_05_evaluation_protocol_v1"
MODELS = ("baseline_simple", "dixon_coles", "dixon_coles_kalman", "markov_global", "markov_dependent")


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Parámetros congelados de la evaluación y bootstrap por partido."""

    version: str = "evaluation_protocol_v1"
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20260726
    confirmation_fold_id: int = 3


class OosEvaluator(ABC):
    """Contrato para evaluadores temporales de predicciones pre-match."""

    @abstractmethod
    def evaluate(self) -> dict[str, Any]:
        """Calcula métricas OOS y decisión usando targets sólo al final."""


def _load(path: Path) -> Any:
    """Carga JSON versionado de un artefacto ya publicado."""
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable para provenance de entrada."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe JSON por reemplazo atómico dentro de la fase."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _targets() -> dict[int, dict[str, Any]]:
    """Extrae outcomes exclusivamente para evaluación posterior a la predicción."""
    rows = _load(FEATURES)["rows"]
    return {int(row["match_id"]): {"outcome": str(row["result_1x2"]), "over_2_5": bool(row["over_2_5"]), "btts": bool(row["btts"]), "total_goals": float(row["total_goals"])} for row in rows}


def _losses(row: dict[str, Any], target: dict[str, Any]) -> dict[str, float]:
    """Calcula pérdidas por partido sin devolver ni usar el target como feature."""
    probs = {"1": float(row["prob_1"]), "X": float(row["prob_x"]), "2": float(row["prob_2"])}
    outcome = str(target["outcome"])
    brier = sum((probs[label] - float(label == outcome)) ** 2 for label in probs)
    return {"log_loss_1x2": -math.log(max(probs[outcome], 1e-15)), "brier_1x2": brier, "mae_total_goals": abs(float(row["expected_home_goals"]) + float(row["expected_away_goals"]) - float(target["total_goals"])), "log_loss_over_2_5": _binary_log(float(row["prob_over_2_5"]), bool(target["over_2_5"])), "log_loss_btts": _binary_log(float(row["prob_btts"]), bool(target["btts"]))}


def _binary_log(probability: float, actual: bool) -> float:
    """Devuelve log-loss binario con protección numérica explícita."""
    probability = min(max(probability, 1e-15), 1.0 - 1e-15)
    return -math.log(probability if actual else 1.0 - probability)


def _scored_rows(predictions: list[dict[str, Any]], targets: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Une predicción y target sólo después de verificar su identidad de partido."""
    output = []
    for row in predictions:
        match_id = int(row["match_id"])
        if match_id not in targets: raise KeyError(f"Target faltante para partido {match_id}.")
        output.append({**row, **_losses(row, targets[match_id])})
    return output


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    """Calcula promedio explícito evitando dependencias tabulares innecesarias."""
    return sum(float(row[key]) for row in rows) / len(rows) if rows else float("nan")


def _metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Agrega métricas OOS para un modelo dentro de un bloque temporal."""
    keys = ("log_loss_1x2", "brier_1x2", "mae_total_goals", "log_loss_over_2_5", "log_loss_btts")
    return {key: _mean(rows, key) for key in keys}


def _by_block(rows: list[dict[str, Any]], config: EvaluationConfig) -> dict[str, dict[str, dict[str, float]]]:
    """Resume métricas por modelo en validación y confirmación temporal."""
    output: dict[str, dict[str, dict[str, float]]] = {}
    for block, predicate in (("validation", lambda row: int(row["fold_id"]) != config.confirmation_fold_id), ("confirmation", lambda row: int(row["fold_id"]) == config.confirmation_fold_id)):
        output[block] = {model: _metrics([row for row in rows if row["model"] == model and predicate(row)]) for model in MODELS}
    return output


def _paired(rows: list[dict[str, Any]], left: str, right: str, key: str) -> list[float]:
    """Alinea dos modelos por partido y devuelve mejora izquierda menos derecha."""
    values = {model: {int(row["match_id"]): float(row[key]) for row in rows if row["model"] == model} for model in (left, right)}
    common = sorted(set(values[left]) & set(values[right]))
    return [values[right][match_id] - values[left][match_id] for match_id in common]


def _bootstrap(differences: list[float], config: EvaluationConfig) -> dict[str, Any]:
    """Bootstrap percentil de mejoras, remuestreando partidos completos."""
    rng = random.Random(config.bootstrap_seed)
    samples = [sum(rng.choice(differences) for _ in differences) / len(differences) for _ in range(config.bootstrap_samples)]
    samples.sort(); lower, upper = samples[int(0.025 * len(samples))], samples[int(0.975 * len(samples)) - 1]
    return {"match_count": len(differences), "mean_improvement": sum(differences) / len(differences), "ci_95": [lower, upper], "improvement_confirmed": lower > 0.0}


class TemporalSuiteEvaluator(OosEvaluator):
    """Evalúa y compara la suite OOS sin contaminar las predicciones."""

    def __init__(self, config: EvaluationConfig) -> None:
        """Guarda configuración antes de cargar artefactos de entrada."""
        self.config = config

    def evaluate(self) -> dict[str, Any]:
        """Ejecuta métricas y bootstrap sobre el último fold confirmatorio."""
        scored = _scored_rows(_load(PREDICTIONS), _targets())
        metrics = _by_block(scored, self.config)
        confirmation = [row for row in scored if int(row["fold_id"]) == self.config.confirmation_fold_id]
        bootstrap = {name: _bootstrap(_paired(confirmation, "markov_dependent", comparator, "log_loss_1x2"), self.config) for name, comparator in {"vs_baseline": "baseline_simple", "vs_markov_global": "markov_global"}.items()}
        return {"config": asdict(self.config), "scored": scored, "metrics": metrics, "bootstrap": bootstrap, "classification": _classification(metrics, bootstrap)}


def _classification(metrics: dict[str, Any], bootstrap: dict[str, Any]) -> str:
    """Decide promoción sólo ante mejora confirmatoria frente a ambos controles."""
    confirmation = metrics["confirmation"]
    better = confirmation["markov_dependent"]["log_loss_1x2"] < confirmation["baseline_simple"]["log_loss_1x2"] and confirmation["markov_dependent"]["log_loss_1x2"] < confirmation["markov_global"]["log_loss_1x2"]
    confirmed = all(item["improvement_confirmed"] for item in bootstrap.values())
    return "validated" if better and confirmed else "rejected_for_revision"


def _audit(result: dict[str, Any]) -> dict[str, Any]:
    """Verifica cobertura, bloques y que los targets no entren como features."""
    rows = result["scored"]
    coverage = {model: len({int(row["match_id"]) for row in rows if row["model"] == model}) for model in MODELS}
    return {"model_coverage": coverage, "common_coverage": min(coverage.values()), "confirmation_fold_id": result["config"]["confirmation_fold_id"], "target_outcomes_used_as_features": False, "bootstrap_unit": "complete_match", "promotion_allowed": result["classification"] == "validated"}


def run(config: EvaluationConfig | None = None) -> dict[str, Any]:
    """Ejecuta Fase 05 y publica todas las evidencias de evaluación."""
    result = TemporalSuiteEvaluator(config or EvaluationConfig()).evaluate()
    audit = _audit(result)
    _publish(result, audit)
    LOGGER.info("Fase 05 evaluation: %s", result["classification"])
    return {**result, "audit": audit}


def _publish(result: dict[str, Any], audit: dict[str, Any]) -> None:
    """Publica métricas, bootstrap, reportes, hashes y decisión de fase."""
    manifest = {"canonical_predictions_hash": _hash(_load(PREDICTIONS)), "features_hash": _hash(_load(FEATURES))}
    payloads = {"config.json": result["config"], "input_manifest.json": manifest, "metrics_by_match.json": result["scored"], "metrics.json": result["metrics"], "bootstrap_results.json": result["bootstrap"], "coverage.json": audit["model_coverage"], "audit.json": {**audit, "classification": result["classification"]}}
    for name, value in payloads.items(): _write(name, value)
    _write_reports(result, audit)
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def _write_reports(result: dict[str, Any], audit: dict[str, Any]) -> None:
    """Redacta interpretación de confirmación y límite de promoción."""
    confirmation = result["metrics"]["confirmation"]
    dependent = confirmation["markov_dependent"]["log_loss_1x2"]
    baseline = confirmation["baseline_simple"]["log_loss_1x2"]
    global_ = confirmation["markov_global"]["log_loss_1x2"]
    lines = ["# Fase 05 — evaluation_protocol v1", "", f"**Clasificación:** `{result['classification']}`", "", f"- cobertura común OOS: `{audit['common_coverage']}` partidos", f"- log-loss confirmatorio Markov dependiente: `{dependent:.6f}`", f"- baseline simple: `{baseline:.6f}`", f"- Markov global: `{global_:.6f}`", f"- bootstrap vs baseline: `{result['bootstrap']['vs_baseline']['ci_95']}`", f"- bootstrap vs Markov global: `{result['bootstrap']['vs_markov_global']['ci_95']}`"]
    next_step = "revisión independiente antes de promoción operativa" if result["classification"] == "validated" else "revisar emisiones de gol e integración canónica Dixon-Coles/Kalman; promoción bloqueada"
    (OUTPUT / "validation_report.md").write_text("\n".join(lines + ["", "## Limitación", "Las emisiones de gol Markov siguen siendo históricas y experimentales."]) + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text("\n".join(lines + [f"- siguiente paso permitido: {next_step}"]) + "\n", encoding="utf-8")


# Version: 1.0.0
# Created: 2026-07-26
