"""Evaluación de señal Markov residual para mercados temporales.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.evaluate_oos_suite_v1 import EvaluationConfig, _bootstrap

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
FOLDS = ROOT / "artifacts/phase_3_8_common_protocol/common_temporal_folds_v1.json"
PREDICTIONS = ROOT / "artifacts/phase_06_markov_v2_oos_predictions/predictions.json"
OUTPUT = ROOT / "artifacts/phase_07_markov_temporal_residual"
MARKETS = ("first_half_goal", "second_half_goal", "home_comeback", "away_comeback")


@dataclass(frozen=True, slots=True)
class ResidualEvaluationConfig:
    """Parámetros congelados de evaluación de mercados temporales."""

    version: str = "markov_temporal_residual_v1"
    confirmation_fold_id: int = 3
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20260726


def _load(path: Path) -> Any:
    """Carga un JSON versionado sin modificar el origen."""
    return json.loads(path.read_text(encoding="utf-8"))


def _target_rows() -> dict[int, dict[str, bool]]:
    """Construye targets de medio tiempo y remontada desde ventanas observadas."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _load(WINDOWS): grouped[int(row["match_id"])].append(row)
    output = {}
    for match_id, rows in grouped.items():
        home = [row for row in rows if bool(row["is_home"])]
        away = [row for row in rows if not bool(row["is_home"])]
        home_by = {int(row["window_index"]): int(row["goals"]) for row in home}; away_by = {int(row["window_index"]): int(row["goals"]) for row in away}
        home_half, away_half = sum(home_by.get(i, 0) for i in range(3)), sum(away_by.get(i, 0) for i in range(3))
        home_final, away_final = sum(home_by.values()), sum(away_by.values())
        output[match_id] = {"first_half_goal": home_half + away_half > 0, "second_half_goal": home_final + away_final - home_half - away_half > 0, "home_comeback": home_half < away_half and home_final > away_final, "away_comeback": away_half < home_half and away_final > home_final}
    return output


def _baseline(fold: dict[str, Any], targets: dict[int, dict[str, bool]]) -> dict[str, float]:
    """Estima baseline temporal sólo desde el train fold."""
    train = [targets[int(match_id)] for match_id in fold["train_ids"]]
    return {market: sum(bool(row[market]) for row in train) / len(train) for market in MARKETS}


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario con clipping finito."""
    probability = min(max(float(probability), 1e-15), 1.0 - 1e-15)
    return -math.log(probability if actual else 1.0 - probability)


def _rows() -> list[dict[str, Any]]:
    """Une predicciones v2 con targets y baseline temporal de su propio fold."""
    targets, predictions = _target_rows(), _load(PREDICTIONS)
    folds = {int(fold["fold_id"]): fold for fold in _load(FOLDS)["folds"]}
    baselines = {fold_id: _baseline(fold, targets) for fold_id, fold in folds.items()}
    output = []
    for row in predictions:
        target = targets[int(row["match_id"])]
        base = baselines[int(row["fold_id"])]
        output.append({**row, **{f"target_{market}": target[market] for market in MARKETS}, **{f"baseline_{market}": base[market] for market in MARKETS}, **{f"loss_{market}": _loss(row[f"prob_{market}"], target[market]) for market in MARKETS}, **{f"baseline_loss_{market}": _loss(base[market], target[market]) for market in MARKETS}})
    return output


def _metrics(rows: list[dict[str, Any]], config: ResidualEvaluationConfig) -> dict[str, Any]:
    """Resume log-loss Markov y baseline por bloque temporal."""
    output = {}
    for block, predicate in (("validation", lambda row: int(row["fold_id"]) != config.confirmation_fold_id), ("confirmation", lambda row: int(row["fold_id"]) == config.confirmation_fold_id)):
        subset = [row for row in rows if predicate(row)]
        output[block] = {market: {"markov": sum(row[f"loss_{market}"] for row in subset) / len(subset), "baseline": sum(row[f"baseline_loss_{market}"] for row in subset) / len(subset)} for market in MARKETS}
    return output


def _bootstrap_markets(rows: list[dict[str, Any]], config: ResidualEvaluationConfig) -> dict[str, Any]:
    """Bootstrap de mejora temporal agrupado por partido completo."""
    subset = [row for row in rows if int(row["fold_id"]) == config.confirmation_fold_id]
    base = EvaluationConfig(bootstrap_samples=config.bootstrap_samples, bootstrap_seed=config.bootstrap_seed)
    return {market: _bootstrap([row[f"baseline_loss_{market}"] - row[f"loss_{market}"] for row in subset], base) for market in MARKETS}


def run(config: ResidualEvaluationConfig | None = None) -> dict[str, Any]:
    """Evalúa señal temporal Markov sin habilitar mercados automáticamente."""
    active, rows = config or ResidualEvaluationConfig(), _rows()
    metrics, bootstrap = _metrics(rows, active), _bootstrap_markets(rows, active)
    classification = "promising_unconfirmed" if any(item["improvement_confirmed"] for item in bootstrap.values()) else "rejected_for_revision"
    result = {"config": asdict(active), "metrics": metrics, "bootstrap": bootstrap, "rows": rows, "classification": classification, "audit": {"match_count": len({row["match_id"] for row in rows}), "markets": MARKETS, "target_outcomes_used_as_features": False, "bootstrap_unit": "complete_match", "promotion_allowed": False}}
    _publish(result)
    LOGGER.info("Markov temporal residual: %s", classification)
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica targets, métricas, bootstrap y reporte de límites."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "metrics.json": result["metrics"], "bootstrap_results.json": result["bootstrap"], "metrics_by_match.json": result["rows"], "audit.json": result["audit"], "input_manifest.json": {"windows_hash": hashlib.sha256(WINDOWS.read_bytes()).hexdigest(), "predictions_hash": hashlib.sha256(PREDICTIONS.read_bytes()).hexdigest()}}
    for name, value in payloads.items(): (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Markov temporal residual v1", "", f"**Clasificación:** `{result['classification']}`", "", "- mercados: primer tiempo, segundo tiempo y remontadas.", "- bootstrap agrupado por partido.", "- promoción bloqueada; cada mercado requiere validación propia."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


# Version: 1.0.0
# Created: 2026-07-26
