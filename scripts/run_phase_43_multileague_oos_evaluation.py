"""Evalúa OOS la fusión estructural Markov multi-liga por partido completo.

Las predicciones se generaron antes de leer los targets. Esta fase sólo ahora
lee los goles observados, calcula log-loss/Brier y bootstrap por partido. No
entrena, modifica el router ni promueve mercados.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGGER = logging.getLogger(__name__)
PREDICTIONS = ROOT / "artifacts/phase_42_multileague_structural_fusion_v1/predictions.json"
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transitions.json"
OUTPUT = ROOT / "artifacts/phase_43_multileague_oos_evaluation_v1"
TARGETS = ("first_half_goal", "second_half_goal", "home_comeback", "away_comeback")
MARKETS = ("1x2", "over_2_5", "btts", *TARGETS)


def _load(path: Path) -> Any:
    """Carga un artefacto JSON congelado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula hash estable de datos serializables."""

    raw = json.dumps(_json_keys(value), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_keys(value: Any) -> Any:
    """Convierte claves compuestas a texto."""

    if isinstance(value, dict):
        return {str(key): _json_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_keys(item) for item in value]
    return value


def _development_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Reproduce el bloque temporal de desarrollo de Fase 40."""

    ordered = sorted({(str(row["match_date"]), int(row["match_id"])) for row in rows})
    return {match_id for _, match_id in ordered[: int(len(ordered) * 0.60)]}


def _targets(windows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Agrega targets post-partido desde las doce ventanas observadas."""

    grouped: dict[int, dict[str, Any]] = {}
    for row in windows:
        item = grouped.setdefault(int(row["match_id"]), {"match_id": int(row["match_id"]), "league_slug": str(row["league_slug"]), "home_goals": 0, "away_goals": 0, "home_half_goals": 0, "away_half_goals": 0})
        goals = int(row["goals"])
        side = "home" if bool(row["is_home"]) else "away"
        item[f"{side}_goals"] += goals
        if int(row["window_index"]) < 3:
            item[f"{side}_half_goals"] += goals
    for item in grouped.values():
        home, away = item["home_goals"], item["away_goals"]
        half_home, half_away = item["home_half_goals"], item["away_half_goals"]
        item.update({"result_1x2": "1" if home > away else "2" if home < away else "X", "over_2_5": home + away > 2, "btts": home > 0 and away > 0, "first_half_goal": half_home + half_away > 0, "second_half_goal": home + away - half_home - half_away > 0, "home_comeback": half_home < half_away and home > away, "away_comeback": half_away < half_home and away > home})
    return grouped


def _counts(predictions: list[dict[str, Any]], targets: dict[int, dict[str, Any]], development: set[int]) -> dict[str, Any]:
    """Estima prevalencias de baseline usando sólo desarrollo."""

    rows = [row for match_id, row in targets.items() if int(match_id) in development]
    binary_names = ("over_2_5", "btts", *TARGETS)
    global_binary = {name: sum(bool(row[name]) for row in rows) / max(len(rows), 1) for name in binary_names}
    league_binary: dict[str, dict[str, float]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(row)
    for league, values in grouped.items():
        league_binary[league] = {name: (sum(bool(row[name]) for row in values) + 1.0) / (len(values) + 2.0) for name in binary_names}
    outcomes = Counter(str(row["result_1x2"]) for row in rows)
    global_1x2 = {outcome: (outcomes[outcome] + 1.0) / (len(rows) + 3.0) for outcome in ("1", "X", "2")}
    league_1x2 = {league: {outcome: (sum(row["result_1x2"] == outcome for row in values) + 1.0) / (len(values) + 3.0) for outcome in ("1", "X", "2")} for league, values in grouped.items()}
    return {"development_match_count": len(rows), "global_binary": global_binary, "league_binary": league_binary, "global_1x2": global_1x2, "league_1x2": league_1x2}


def _poisson_probs(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """Calcula baseline Poisson estructural para mercados generales."""

    grid = np.zeros((11, 11), dtype=float)
    for home in range(11):
        for away in range(11):
            grid[home, away] = math.exp(-lambda_home) * lambda_home**home / math.factorial(home) * math.exp(-lambda_away) * lambda_away**away / math.factorial(away)
    grid /= grid.sum()
    return {"1": float(sum(grid[h, a] for h in range(11) for a in range(11) if h > a)), "X": float(np.trace(grid)), "2": float(sum(grid[h, a] for h in range(11) for a in range(11) if h < a)), "over_2_5": float(sum(grid[h, a] for h in range(11) for a in range(11) if h + a > 2)), "btts": float(grid[1:, 1:].sum()), "first_half_goal": 1.0 - math.exp(-(lambda_home + lambda_away) / 2.0), "second_half_goal": 1.0 - math.exp(-(lambda_home + lambda_away) / 2.0)}


def _probability(row: dict[str, Any], market: str, baseline: str, counts: dict[str, Any]) -> float:
    """Obtiene una probabilidad de baseline sin usar el target actual."""

    league = str(row["league_slug"])
    if market == "1x2":
        if baseline == "structural_poisson":
            return float(_poisson_probs(float(row["lambda_base_home"]), float(row["lambda_base_away"]))[row["actual_result"]])
        values = counts["global_1x2"] if baseline == "global" else counts["league_1x2"].get(league, counts["global_1x2"])
        return float(values[row["actual_result"]])
    if baseline == "structural_poisson":
        return float(_poisson_probs(float(row["lambda_base_home"]), float(row["lambda_base_away"]))[market])
    values = counts["global_binary"] if baseline == "global" else counts["league_binary"].get(league, counts["global_binary"])
    return float(values[market])


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier de una probabilidad binaria."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _score(rows: list[dict[str, Any]], market: str, provider: str, counts: dict[str, Any]) -> list[float]:
    """Obtiene pérdidas por partido para un mercado/proveedor."""

    losses = []
    for row in rows:
        if market == "1x2":
            probability = row["prob_1" if row["actual_result"] == "1" else "prob_x" if row["actual_result"] == "X" else "prob_2"] if provider == "model" else _probability(row, market, provider, counts)
            losses.append(-math.log(max(float(probability), 1e-12)))
        else:
            actual = bool(row[market])
            probability = float(row[f"prob_{market}"]) if provider == "model" else _probability(row, market, provider, counts)
            losses.append(_loss(probability, actual)[0])
    return losses


def _metrics(rows: list[dict[str, Any]], market: str, counts: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Calcula métricas e intervalos bootstrap de mejora."""

    providers = ("model", "global", "league", "structural_poisson") if market != "home_comeback" and market != "away_comeback" else ("model", "global", "league")
    scores = {provider: _score(rows, market, provider, counts) for provider in providers}
    model_losses = np.asarray(scores["model"], dtype=float)
    output = {"match_count": len(rows), "positive_count": sum(bool(row[market]) for row in rows) if market != "1x2" else None, "providers": {provider: {"mean_log_loss": float(np.mean(values))} for provider, values in scores.items()}, "improvement_vs": {}}
    market_seed = int(hashlib.sha256(market.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(int(config["bootstrap_seed"]) + market_seed)
    for provider in providers[1:]:
        baseline_losses = np.asarray(scores[provider], dtype=float)
        deltas = np.empty(int(config["bootstrap_samples"]), dtype=float)
        for index in range(len(deltas)):
            sample = rng.integers(0, len(rows), len(rows))
            deltas[index] = float(np.mean(baseline_losses[sample] - model_losses[sample]))
        output["improvement_vs"][provider] = {"mean": float(np.mean(baseline_losses - model_losses)), "ci_95": [float(np.quantile(deltas, 0.025)), float(np.quantile(deltas, 0.975))], "bootstrap_samples": len(deltas)}
    if market == "1x2":
        output["actual_rate"] = {outcome: sum(row["actual_result"] == outcome for row in rows) / max(len(rows), 1) for outcome in ("1", "X", "2")}
    return output


def _classification(metrics: dict[str, Any]) -> tuple[str, bool]:
    """Determina si la fusión supera el baseline estructural principal."""

    primary = ("1x2", "over_2_5", "btts", "first_half_goal", "second_half_goal")
    deltas = [metrics["confirmation"][market]["improvement_vs"]["structural_poisson"]["mean"] for market in primary]
    beats = all(delta > 0.0 for delta in deltas)
    return ("evaluated_no_promotion" if beats else "rejected_for_promotion", beats)


def _prepare(predictions: list[dict[str, Any]], targets: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Alinea cada predicción con su target post-partido."""

    rows = []
    for prediction in predictions:
        target = targets.get(int(prediction["match_id"]))
        if target is None:
            continue
        rows.append({**prediction, **target, "actual_result": target["result_1x2"]})
    return rows


def _write(result: dict[str, Any], source: dict[str, Any]) -> None:
    """Publica resultados, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "metrics.json": result["metrics"], "coverage.json": result["coverage"], "audit.json": result["audit"], "input_manifest.json": {name: _hash(value) for name, value in source.items()}}
    for name, value in payloads.items():
        target = OUTPUT / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(target)
    next_step = "revisar evidencia y mantener baseline" if result["audit"]["classification"] == "rejected_for_promotion" else "revisión de evidencia y decisión de router"
    report = ["# Fase 43 — evaluación OOS multi-liga", "", f"**Clasificación:** `{result['audit']['classification']}`", "", f"- partidos evaluados: `{result['coverage']['predictions_scored']}`", f"- validación/confirmación: `{result['coverage']['validation']}/{result['coverage']['confirmation']}`", "- unidad estadística: `partido completo`", "- bootstrap: `por partido, no por ventana`", "- promoción automática: `False`", f"- siguiente paso: `{next_step}`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la evaluación post-partido sin alterar modelos."""

    predictions, windows, transitions = _load(PREDICTIONS), _load(WINDOWS), _load(TRANSITIONS)
    source = {"predictions": predictions, "windows": windows, "transitions": transitions}
    targets = _targets(windows)
    development = _development_ids(transitions)
    rows = _prepare(predictions, targets)
    counts = _counts([], targets, development)
    config = {"version": "multileague_oos_evaluation_v1", "bootstrap_samples": 2000, "bootstrap_seed": 20260727, "development_only_baselines": True, "unit": "match"}
    metrics = {split: {market: _metrics([row for row in rows if row["split"] == split], market, counts, config) for market in MARKETS} for split in ("validation", "confirmation")}
    classification, primary_beats = _classification(metrics)
    audit = {"classification": classification, "predictions_loaded": len(predictions), "predictions_scored": len(rows), "targets_read_after_prediction": True, "target_used_as_feature": False, "development_baseline_matches": counts["development_match_count"], "match_level_bootstrap": True, "primary_markets_beat_structural_poisson": primary_beats, "router_modified": False, "markets_promoted": False, "official_model_modified": False}
    result = {"config": config, "metrics": metrics, "coverage": {"predictions_loaded": len(predictions), "predictions_scored": len(rows), "validation": sum(row["split"] == "validation" for row in rows), "confirmation": sum(row["split"] == "confirmation" for row in rows), "targets_available": len(targets)}, "audit": audit}
    _write(result, source)
    LOGGER.info("Fase 43 evaluación OOS multi-liga: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 43."""

    return 0 if run()["audit"]["classification"] in {"evaluated_no_promotion", "rejected_for_promotion"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
