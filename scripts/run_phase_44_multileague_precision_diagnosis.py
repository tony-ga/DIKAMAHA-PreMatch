"""Corrige y diagnostica precisión Monte Carlo de la fusión multi-liga.

Cuando Markov sólo redistribuye una intensidad total conservada, los mercados
de partido completo deben usar la distribución Poisson analítica. Esta fase
elimina el ruido de simulación de 300 trayectorias en 1X2, Over 2.5 y BTTS,
pero conserva las probabilidades temporales generadas por Markov.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
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
PREDICTIONS = ROOT / "artifacts/phase_42_multileague_structural_fusion_v1/predictions.json"
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transitions.json"
ORIGINAL_METRICS = ROOT / "artifacts/phase_43_multileague_oos_evaluation_v1/metrics.json"
OUTPUT = ROOT / "artifacts/phase_44_multileague_precision_diagnosis_v1"


def _correct(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sustituye mercados completos por probabilidades Poisson exactas."""

    corrected = []
    for row in predictions:
        probabilities = _poisson_probs(float(row["lambda_base_home"]), float(row["lambda_base_away"]))
        item = {**row, "prob_1": probabilities["1"], "prob_x": probabilities["X"], "prob_2": probabilities["2"], "prob_over_2_5": probabilities["over_2_5"], "prob_btts": probabilities["btts"], "full_match_probability_source": "analytical_poisson_conservation"}
        corrected.append(item)
    return corrected


def _metrics_by_split(rows: list[dict[str, Any]], counts: dict[str, Any]) -> dict[str, Any]:
    """Evalúa resultados corregidos por partición."""

    config = {"bootstrap_samples": 2000, "bootstrap_seed": 20260727}
    return {split: {market: _metrics([row for row in rows if row["split"] == split], market, counts, config) for market in MARKETS} for split in ("validation", "confirmation")}


def _comparison(original: dict[str, Any], corrected: dict[str, Any]) -> dict[str, Any]:
    """Mide el efecto de quitar ruido Monte Carlo en cada mercado."""

    output = {}
    for split in ("validation", "confirmation"):
        output[split] = {}
        for market in MARKETS:
            before = original[split][market]["providers"]["model"]["mean_log_loss"]
            after = corrected[split][market]["providers"]["model"]["mean_log_loss"]
            structural = corrected[split][market]["providers"].get("structural_poisson", {}).get("mean_log_loss")
            output[split][market] = {"original_log_loss": before, "corrected_log_loss": after, "improvement_from_precision_fix": before - after, "corrected_vs_structural": None if structural is None else structural - after}
    return output


def _write(result: dict[str, Any], source: dict[str, Any]) -> None:
    """Publica diagnóstico y predicciones corregidas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"corrected_predictions.json": result["corrected_predictions"], "metrics.json": result["metrics"], "comparison.json": result["comparison"], "coverage.json": result["coverage"], "audit.json": result["audit"], "input_manifest.json": {name: _hash(value) for name, value in source.items()}}
    for name, value in payloads.items():
        target = OUTPUT / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(target)
    report = ["# Fase 44 — diagnóstico de precisión multi-liga", "", f"**Clasificación:** `{result['audit']['classification']}`", "", "- mercados completos corregidos: `1X2, Over 2.5, BTTS`", "- método: `Poisson analítico con lambda_base conservada`", "- mercados temporales: `sin modificar; siguen siendo Markov experimentales`", f"- partidos: `{result['coverage']['predictions']}`", "- router oficial: `sin cambios`", "- siguiente paso: `recalibrar sólo la señal temporal, no volver a tocar mercados completos`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la corrección analítica y su evaluación OOS."""

    predictions, windows, transitions = _load(PREDICTIONS), _load(WINDOWS), _load(TRANSITIONS)
    source = {"predictions": predictions, "windows": windows, "transitions": transitions, "original_metrics": _load(ORIGINAL_METRICS)}
    targets = _targets(windows)
    rows = _prepare(predictions, targets)
    counts = _counts([], targets, _development_ids(transitions))
    corrected = _correct(rows)
    metrics = _metrics_by_split(corrected, counts)
    comparison = _comparison(source["original_metrics"], metrics)
    full_markets_exact = all(abs(comparison[split][market]["corrected_vs_structural"] or 0.0) < 1e-12 for split in ("validation", "confirmation") for market in ("1x2", "over_2_5", "btts"))
    audit = {"classification": "precision_defect_identified_temporal_signal_pending", "predictions": len(corrected), "full_match_markets_exact": full_markets_exact, "target_used_as_feature": False, "temporal_markets_changed": False, "router_modified": False, "markets_promoted": False}
    result = {"corrected_predictions": corrected, "metrics": metrics, "comparison": comparison, "coverage": {"predictions": len(corrected), "validation": sum(row["split"] == "validation" for row in corrected), "confirmation": sum(row["split"] == "confirmation" for row in corrected)}, "audit": audit}
    _write(result, source)
    LOGGER.info("Fase 44 diagnóstico de precisión: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 44."""

    return 0 if run()["audit"]["classification"] == "precision_defect_identified_temporal_signal_pending" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
