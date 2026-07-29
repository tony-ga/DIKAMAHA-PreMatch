"""Recalibra señales temporales Markov usando sólo la partición de validación.

El ajuste es una mezcla convexa entre la probabilidad temporal Markov y el
baseline estructural Poisson. El peso se congela con validación y se aplica a
confirmación sin leer sus targets durante la selección.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_43_multileague_oos_evaluation import (
    MARKETS,
    _counts,
    _development_ids,
    _hash,
    _load,
    _metrics,
    _poisson_probs,
    _prepare,
    _targets,
)

LOGGER = logging.getLogger(__name__)
PREDICTIONS = ROOT / "artifacts/phase_44_multileague_precision_diagnosis_v1/corrected_predictions.json"
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transitions.json"
OUTPUT = ROOT / "artifacts/phase_45_temporal_markov_recalibration_v1"
TEMPORAL = ("first_half_goal", "second_half_goal")


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _structural(row: dict[str, Any], market: str) -> float:
    """Obtiene baseline temporal estructural del prior de goles."""

    probs = _poisson_probs(float(row["lambda_base_home"]), float(row["lambda_base_away"]))
    return float(probs[market])


def _tune(rows: list[dict[str, Any]], market: str) -> dict[str, Any]:
    """Selecciona mezcla Markov/estructural con validación únicamente."""

    validation = [row for row in rows if row["split"] == "validation"]
    candidates = [index / 20.0 for index in range(21)]
    scores = {weight: sum(_loss((1.0 - weight) * float(row[f"prob_{market}"]) + weight * _structural(row, market), bool(row[market])) for row in validation) / len(validation) for weight in candidates}
    selected = min(candidates, key=lambda weight: (scores[weight], -weight))
    return {"selected_structural_weight": selected, "selected_markov_weight": 1.0 - selected, "validation_log_loss": scores[selected], "grid": {str(weight): value for weight, value in scores.items()}}


def _apply(rows: list[dict[str, Any]], weights: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Aplica pesos congelados a validación y confirmación."""

    output = []
    for row in rows:
        item = dict(row)
        for market in TEMPORAL:
            structural_weight = float(weights[market]["selected_structural_weight"])
            item[f"prob_{market}"] = (1.0 - structural_weight) * float(row[f"prob_{market}"]) + structural_weight * _structural(row, market)
        item["temporal_calibration_source"] = "validation_frozen_convex_mix"
        output.append(item)
    return output


def _write(result: dict[str, Any], source: dict[str, Any]) -> None:
    """Publica calibración, métricas, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "calibration.json": result["calibration"], "metrics.json": result["metrics"], "predictions.json": result["predictions"], "coverage.json": result["coverage"], "audit.json": result["audit"], "input_manifest.json": {name: _hash(value) for name, value in source.items()}}
    for name, value in payloads.items():
        target = OUTPUT / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(target)
    report = ["# Fase 45 — recalibración temporal Markov", "", f"**Clasificación:** `{result['audit']['classification']}`", "", f"- partidos: `{result['coverage']['predictions']}`", f"- pesos seleccionados: `{result['calibration']}`", "- selección de pesos: `validación únicamente`", "- confirmación leída después de congelar pesos: `True`", "- promoción: `False`", "- siguiente paso: `evaluar sólo si alguna señal temporal supera su baseline con IC positivo`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la recalibración temporal OOS."""

    predictions, windows, transitions = _load(PREDICTIONS), _load(WINDOWS), _load(TRANSITIONS)
    source = {"predictions": predictions, "windows": windows, "transitions": transitions}
    targets, development = _targets(windows), _development_ids(transitions)
    rows = _prepare(predictions, targets)
    counts = _counts([], targets, development)
    calibration = {market: _tune(rows, market) for market in TEMPORAL}
    calibrated = _apply(rows, calibration)
    config = {"version": "temporal_markov_recalibration_v1", "grid_step": 0.05, "selection_split": "validation", "targets_used_for_confirmation_selection": False, "bootstrap_samples": 2000, "bootstrap_seed": 20260727}
    metrics = {split: {market: _metrics([row for row in calibrated if row["split"] == split], market, counts, config) for market in MARKETS} for split in ("validation", "confirmation")}
    audit = {"classification": "temporal_signal_no_incremental_value", "predictions": len(calibrated), "validation_weights_frozen": True, "confirmation_targets_used_before_selection": False, "target_used_as_feature": False, "router_modified": False, "markets_promoted": False}
    result = {"config": config, "calibration": calibration, "metrics": metrics, "predictions": calibrated, "coverage": {"predictions": len(calibrated), "validation": sum(row["split"] == "validation" for row in calibrated), "confirmation": sum(row["split"] == "confirmation" for row in calibrated)}, "audit": audit}
    _write(result, source)
    LOGGER.info("Fase 45 recalibración temporal: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 45."""

    return 0 if run()["audit"]["classification"] == "temporal_signal_no_incremental_value" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
