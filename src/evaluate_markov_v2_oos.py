"""Evaluación independiente de Markov v2 contra la suite OOS común.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.evaluate_oos_suite_v1 import EvaluationConfig, _bootstrap, _losses, _metrics, _paired, _targets

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/phase_05_canonical_oos_predictions_v1/canonical_predictions.json"
V2 = ROOT / "artifacts/phase_06_markov_v2_oos_predictions/predictions.json"
OUTPUT = ROOT / "artifacts/phase_06_markov_v2_evaluation"
MODELS = ("baseline_simple", "dixon_coles", "dixon_coles_kalman", "markov_global", "markov_dependent", "markov_dependent_v2")


@dataclass(frozen=True, slots=True)
class MarkovV2EvaluationConfig:
    """Parámetros fijados para comparar v2 sobre los folds comunes."""

    version: str = "markov_v2_evaluation"
    confirmation_fold_id: int = 3
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20260726


def _load(path: Path) -> Any:
    """Carga JSON versionado sin modificar su contenido."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, value: Any) -> None:
    """Escribe JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _scored_rows() -> list[dict[str, Any]]:
    """Agrega targets sólo después de cargar todas las predicciones OOS."""
    targets, rows = _targets(), _load(BASE) + _load(V2)
    return [{**row, **_losses(row, targets[int(row["match_id"])])} for row in rows]


def _block_metrics(rows: list[dict[str, Any]], config: MarkovV2EvaluationConfig) -> dict[str, Any]:
    """Calcula métricas por modelo en validación y confirmación temporal."""
    output: dict[str, Any] = {}
    for name, valid in (("validation", False), ("confirmation", True)):
        output[name] = {model: _metrics([row for row in rows if row["model"] == model and (int(row["fold_id"]) == config.confirmation_fold_id) == valid]) for model in MODELS}
    return output


def _bootstrap_results(rows: list[dict[str, Any]], config: MarkovV2EvaluationConfig) -> dict[str, Any]:
    """Bootstrap por partido contra controles requeridos en confirmación."""
    confirmation = [row for row in rows if int(row["fold_id"]) == config.confirmation_fold_id]
    base = EvaluationConfig(bootstrap_samples=config.bootstrap_samples, bootstrap_seed=config.bootstrap_seed)
    controls = {"vs_baseline": "baseline_simple", "vs_dixon_coles": "dixon_coles", "vs_markov_v1": "markov_dependent", "vs_markov_global": "markov_global"}
    return {name: _bootstrap(_paired(confirmation, "markov_dependent_v2", model, "log_loss_1x2"), base) for name, model in controls.items()}


def _classification(metrics: dict[str, Any], bootstrap: dict[str, Any]) -> str:
    """Autoriza promoción sólo si v2 gana con certeza contra baseline y Dixon-Coles."""
    confirmation = metrics["confirmation"]
    v2 = confirmation["markov_dependent_v2"]["log_loss_1x2"]
    better = v2 < confirmation["baseline_simple"]["log_loss_1x2"] and v2 < confirmation["dixon_coles"]["log_loss_1x2"]
    confirmed = bootstrap["vs_baseline"]["improvement_confirmed"] and bootstrap["vs_dixon_coles"]["improvement_confirmed"]
    return "validated" if better and confirmed else "rejected_for_revision"


def _audit(rows: list[dict[str, Any]], classification: str) -> dict[str, Any]:
    """Verifica cobertura y bloqueo de promoción cuando corresponda."""
    coverage = {model: len({int(row["match_id"]) for row in rows if row["model"] == model}) for model in MODELS}
    return {"model_coverage": coverage, "common_coverage": min(coverage.values()), "target_outcomes_used_as_features": False, "bootstrap_unit": "complete_match", "promotion_allowed": classification == "validated"}


def run(config: MarkovV2EvaluationConfig | None = None) -> dict[str, Any]:
    """Evalúa v2 y publica decisión independiente de la evidencia v1."""
    active, rows = config or MarkovV2EvaluationConfig(), _scored_rows()
    metrics = _block_metrics(rows, active); bootstrap = _bootstrap_results(rows, active)
    classification = _classification(metrics, bootstrap); audit = _audit(rows, classification)
    result = {"config": asdict(active), "metrics": metrics, "bootstrap": bootstrap, "audit": audit, "classification": classification, "rows": rows}
    _publish(result)
    LOGGER.info("Evaluación Markov v2: %s", classification)
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica evidencia, reporte y hashes reproducibles de evaluación v2."""
    manifest = {"v1_suite_hash": hashlib.sha256(BASE.read_bytes()).hexdigest(), "v2_predictions_hash": hashlib.sha256(V2.read_bytes()).hexdigest()}
    payloads = {"config.json": result["config"], "input_manifest.json": manifest, "metrics_by_match.json": result["rows"], "metrics.json": result["metrics"], "bootstrap_results.json": result["bootstrap"], "coverage.json": result["audit"]["model_coverage"], "audit.json": {**result["audit"], "classification": result["classification"]}}
    for name, value in payloads.items(): _write(name, value)
    _write_report(result)
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def _write_report(result: dict[str, Any]) -> None:
    """Redacta conclusión confirmatoria sin promocionar por una sola métrica."""
    metrics, bootstrap = result["metrics"]["confirmation"], result["bootstrap"]
    lines = ["# Evaluación Markov v2", "", f"**Clasificación:** `{result['classification']}`", "", f"- log-loss v2: `{metrics['markov_dependent_v2']['log_loss_1x2']:.6f}`", f"- baseline: `{metrics['baseline_simple']['log_loss_1x2']:.6f}`", f"- Dixon-Coles: `{metrics['dixon_coles']['log_loss_1x2']:.6f}`", f"- bootstrap vs baseline: `{bootstrap['vs_baseline']['ci_95']}`", f"- bootstrap vs Dixon-Coles: `{bootstrap['vs_dixon_coles']['ci_95']}`"]
    next_step = "revisión independiente antes de promoción operativa" if result["classification"] == "validated" else "mantener v2 experimental y revisar especificación"
    (OUTPUT / "validation_report.md").write_text("\n".join(lines + ["", "## Limitación", "La ponderación Dixon-Coles/Kalman es fija y debe conservarse fuera de la selección confirmatoria."]) + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text("\n".join(lines + [f"- siguiente paso permitido: {next_step}"]) + "\n", encoding="utf-8")


# Version: 1.0.0
# Created: 2026-07-26
