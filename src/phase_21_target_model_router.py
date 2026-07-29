"""Selector temporal por target para evitar que Markov degrade el baseline.

La selección se congela con la cohorte de calibración de 44 partidos y se
aplica una sola vez sobre los 241 partidos de confirmación. No se selecciona
con resultados de confirmación.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any

from src.phase_10_temporal_target_evaluation import DIAGNOSTIC_TARGETS, TARGETS

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/phase_20_full_preconfirmation_retraining"
OUTPUT = ROOT / "artifacts/phase_21_target_model_router"


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _selection(calibration: dict[str, Any], minimum_improvement: float = 0.02) -> dict[str, str]:
    """Selecciona Markov sólo con una mejora mínima predeclarada."""

    return {name: "markov_dependent_v2" if calibration["metrics"][name]["baseline_log_loss"] - calibration["metrics"][name]["model_log_loss"] >= minimum_improvement else "baseline_temporal_prevalence" for name in TARGETS + DIAGNOSTIC_TARGETS}


def _route(rows: list[dict[str, Any]], selected: dict[str, str]) -> list[dict[str, Any]]:
    """Aplica la decisión congelada y recalcula pérdidas en confirmación."""

    output = []
    for source in rows:
        row = dict(source)
        for name, model in selected.items():
            probability = float(source[f"prob_{name}"]) if model.startswith("markov") else float(source[f"baseline_{name}"])
            row[f"selected_model_{name}"] = model; row[f"routed_probability_{name}"] = probability; row[f"routed_loss_{name}"] = _loss(probability, bool(source[f"target_{name}"]))
        output.append(row)
    return output


def _metrics(rows: list[dict[str, Any]], selected: dict[str, str]) -> dict[str, Any]:
    """Resume desempeño del selector por target."""

    output = {}
    for name in selected:
        values = [float(row[f"routed_loss_{name}"]) for row in rows]; baseline = [float(row[f"baseline_loss_{name}"]) for row in rows]
        output[name] = {"selected_model": selected[name], "match_count": len(rows), "routed_log_loss": sum(values) / len(values), "baseline_log_loss": sum(baseline) / len(baseline), "mean_improvement": sum(baseline) / len(baseline) - sum(values) / len(values)}
    return output


def _publish(result: dict[str, Any]) -> None:
    """Publica router, métricas, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "selected_models", "metrics", "predictions", "audit"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Construye y evalúa el selector congelado por target."""

    calibration = _load(SOURCE / "calibration.json"); confirmation = _load(SOURCE / "confirmation.json")
    minimum_improvement = 0.02; selected = _selection(calibration, minimum_improvement); routed = _route(confirmation["predictions"], selected); metrics = _metrics(routed, selected)
    markov_targets = [name for name, model in selected.items() if model.startswith("markov")]
    audit = {"classification": "promising_unconfirmed" if any(item["mean_improvement"] > 0.0 for item in metrics.values()) else "rejected_for_revision", "selection_source": "phase20_calibration_only", "confirmation_outcomes_used_for_selection": False, "coverage_complete": len(routed) == confirmation["partition"]["confirmation_count"], "markets_promoted": False, "markov_targets_selected": markov_targets, "target_outcomes_used_as_features": False}
    config = {"version": "target_model_router_v1", "selection_rule": "Markov sólo si la mejora de calibración supera el umbral predeclarado", "minimum_calibration_improvement": minimum_improvement, "confirmation_match_count": len(routed)}
    manifest = {"phase20_calibration_hash": hashlib.sha256((SOURCE / "calibration.json").read_bytes()).hexdigest(), "phase20_confirmation_hash": hashlib.sha256((SOURCE / "confirmation.json").read_bytes()).hexdigest()}
    validation = f"# Validation report — Fase 21\n\n- selección basada sólo en calibración: `{True}`\n- targets servidos por Markov: `{markov_targets}`\n- cobertura confirmatoria: `{len(routed)}` partidos."
    lines = ["# Fase 21 — selector de modelo por target", "", f"**Clasificación:** `{audit['classification']}`", "", f"- targets Markov: `{markov_targets}`", "- mercados promovidos: `False`"]
    for name, item in metrics.items(): lines.append(f"- `{name}`: `{item['selected_model']}`, log-loss `{item['routed_log_loss']:.6f}`, baseline `{item['baseline_log_loss']:.6f}`")
    result = {"config": config, "input_manifest": manifest, "selected_models": selected, "metrics": metrics, "predictions": routed, "audit": audit, "validation_report": validation, "final_report": "\n".join(lines)}
    _publish(result); LOGGER.info("Fase 21 selector temporal: %s", audit["classification"]); return result


# Version: 1.0.0
# Created: 2026-07-26
