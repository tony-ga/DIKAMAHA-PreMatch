"""Evalúa un residual directo de state_0 para first_half_goal."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import _state_rows
from scripts.run_phase_65_markov_position_audit import _bootstrap, _group, _loss

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
PREDICTIONS = ROOT / "artifacts/phase_65_markov_position_audit_v1/predictions.json"
OUTPUT = ROOT / "artifacts/phase_69_direct_state_emission_candidate_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _development_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Reproduce el bloque temporal de desarrollo."""

    grouped = _group(rows)
    ordered = sorted((str(values[0]["match_date"]), match_id) for match_id, values in grouped.items())
    return {match_id for _, match_id in ordered[: int(len(ordered) * 0.60)]}


def _emission(rows: list[dict[str, Any]], development: set[int]) -> dict[str, Any]:
    """Ajusta emisión por par state_0 con pooling global→liga→par."""

    grouped = _group(rows)
    global_counts = [0.0, 0.0]
    league_counts: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    pair_counts: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for match_id in development:
        values = grouped[match_id]
        home = next(row for row in values if bool(row["is_home"]) and int(row["window_index"]) == 0)
        away = next(row for row in values if not bool(row["is_home"]) and int(row["window_index"]) == 0)
        target = float(sum(float(row["goals"]) for row in values if int(row["window_index"]) < 3) > 0)
        league = str(home["league_slug"])
        global_counts[0] += target
        global_counts[1] += 1.0
        league_counts[league][0] += target
        league_counts[league][1] += 1.0
        pair_counts[(league, str(home["state"]), str(away["state"]))][0] += target
        pair_counts[(league, str(home["state"]), str(away["state"]))][1] += 1.0
    global_rate = (global_counts[0] + 1.0) / (global_counts[1] + 2.0)
    league_rate = {league: (values[0] + 8.0 * global_rate) / (values[1] + 8.0) for league, values in league_counts.items()}
    probabilities = {}
    for league in set(league_rate) | {key[0] for key in pair_counts}:
        parent = league_rate.get(league, global_rate)
        for home_state in STATES:
            for away_state in STATES:
                values = pair_counts[(league, home_state, away_state)]
                probabilities[(league, home_state, away_state)] = (values[0] + 32.0 * parent) / (values[1] + 32.0)
    return {"global_rate": global_rate, "league_rate": league_rate, "pair_probabilities": {"|".join(key): value for key, value in probabilities.items()}, "pair_support": {"|".join(key): int(value[1]) for key, value in pair_counts.items()}}


def _state_probability(row: dict[str, Any], emission: dict[str, Any]) -> float:
    """Integra la emisión por par de estados con P(state_0)."""

    league = str(row["fixture"]["league_slug"])
    total = 0.0
    for home_state in STATES:
        for away_state in STATES:
            key = "|".join((league, home_state, away_state))
            fallback = emission["league_rate"].get(league, emission["global_rate"])
            probability = float(emission["pair_probabilities"].get(key, fallback))
            total += row["state_0_home"][home_state] * row["state_0_away"][away_state] * probability
    return total


def _metrics(rows: list[dict[str, Any]], alpha: float, start: int = 0, end: int | None = None) -> dict[str, Any]:
    """Calcula métricas de fusión directa en un bloque temporal."""

    selected = rows[start:end]
    losses, baseline, briers, baseline_briers, deltas = [], [], [], [], []
    for row in selected:
        probability = (1.0 - alpha) * row["baseline_probability"] + alpha * row["state_emission_probability"]
        actual = bool(row["actual_first_half_goal"])
        loss, brier = _loss(probability, actual)
        base_loss, base_brier = _loss(row["baseline_probability"], actual)
        losses.append(loss)
        baseline.append(base_loss)
        briers.append(brier)
        baseline_briers.append(base_brier)
        deltas.append(base_loss - loss)
    return {"matches": len(selected), "alpha": alpha, "log_loss": float(np.mean(losses)), "baseline_log_loss": float(np.mean(baseline)), "brier": float(np.mean(briers)), "baseline_brier": float(np.mean(baseline_briers)), "improvement": _bootstrap(deltas)}


def run() -> dict[str, Any]:
    """Aísla y evalúa la señal pre-match de state_0."""

    windows = _state_rows(_load(WINDOWS))
    development = _development_ids(windows)
    emission = _emission(windows, development)
    predictions = _load(PREDICTIONS)
    for row in predictions:
        row["actual_first_half_goal"] = bool(row["actual_first_half_goal"])
        row["state_emission_probability"] = _state_probability(row, emission)
    split = len(predictions) // 2
    candidates = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0)
    validation = {str(alpha): _metrics(predictions, alpha, 0, split) for alpha in candidates}
    selected = min(candidates, key=lambda alpha: (validation[str(alpha)]["log_loss"], alpha))
    holdout = _metrics(predictions, selected, split)
    all_metrics = _metrics(predictions, selected)
    metrics = {"development_matches": len(development), "audit_matches": len(predictions), "validation": validation, "selected_alpha": selected, "holdout": holdout, "all": all_metrics, "emission": emission}
    audit = {"classification": "direct_state_emission_candidate_requires_confirmation" if holdout["log_loss"] < holdout["baseline_log_loss"] else "direct_state_emission_no_incremental_value", "development_only_emission_fit": True, "target_used_before_prediction": False, "predictions_source_walk_forward": True, "router_modified": False, "markov_promoted": False}
    result = {"config": {"market": "first_half_goal", "alpha_candidates": list(candidates), "pair_smoothing": 32.0}, "metrics": metrics, "predictions": predictions, "audit": audit}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 69 — emisión directa state_0", "", f"**Clasificación:** `{audit['classification']}`", "", f"- alpha seleccionado: `{selected}`", f"- holdout candidato: `{holdout['log_loss']}`", f"- holdout baseline: `{holdout['baseline_log_loss']}`", f"- mejora: `{holdout['improvement']['mean']}`", f"- IC: `{holdout['improvement']['ci_95']}`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 69 state_0 emisión: %s", audit["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["audit"]["classification"].startswith("direct_state_emission_") else 1)

