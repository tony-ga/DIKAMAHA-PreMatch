"""Prueba pooling jerárquico suave de transiciones Markov.

El clasificador y las emisiones permanecen iguales a Fase 65. Sólo cambia la
forma de combinar los tiers de transición. La especificidad se selecciona en
la primera mitad del bloque walk-forward y se evalúa en la segunda.

Requirements:
    - joblib
    - numpy

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import _entropy, _profile, _probabilities, _state_rows, _time
from scripts.run_phase_63_initial_state_calibration import _matrix, _records
from scripts.run_phase_65_markov_position_audit import _add_stats, _group, _loss, _rate, _state_rate, _bootstrap
from src.markov_transition_soft_v1 import SoftTransitionModel, build_transitions

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
MODEL = ROOT / "artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib"
OUTPUT = ROOT / "artifacts/phase_66_soft_transition_recalibration_v1"
SPECIFICITY_VALUES = (2.0, 4.0, 8.0, 16.0, 32.0)
SMOOTHING = 2.0
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Separa desarrollo y objetivos posteriores."""

    grouped = _group(rows)
    ordered = sorted((str(values[0]["match_date"]), match_id) for match_id, values in grouped.items())
    development_count = int(len(ordered) * 0.60)
    development = ordered[:development_count]
    targets = []
    for kickoff, match_id in ordered[development_count:]:
        values = grouped[match_id]
        home = next(row for row in values if bool(row["is_home"]))
        away = next(row for row in values if not bool(row["is_home"]))
        targets.append({"match_id": match_id, "league_slug": str(home["league_slug"]), "kickoff_ts": kickoff, "home_team_id": int(home["team_id"]), "away_team_id": int(away["team_id"]), "provider_status": "post"})
    history = [row for row in rows if (str(row["match_date"]), int(row["match_id"])) <= development[-1]]
    return history, targets, development[-1][0]


def _trajectory(fixture: dict[str, Any], home: dict[str, float], away: dict[str, float], totals: dict[tuple[str, bool, int], list[float]], states: dict[tuple[str, bool, int, str], list[float]], venues: dict[tuple[bool, int], list[float]], model: SoftTransitionModel) -> tuple[float, float]:
    """Calcula probabilidad Markov y tasa baseline para una configuración."""

    alive = {(home_state, away_state): home[home_state] * away[away_state] for home_state in home for away_state in away}
    baseline_rate = 0.0
    for window in range(3):
        home_key = (str(fixture["league_slug"]), True, window)
        away_key = (str(fixture["league_slug"]), False, window)
        baseline_rate += _rate(totals, venues, home_key) + _rate(totals, venues, away_key)
        next_alive: dict[tuple[str, str], float] = defaultdict(float)
        for (home_state, away_state), weight in alive.items():
            home_rate = _state_rate(totals, states, venues, home_key, home_state)
            away_rate = _state_rate(totals, states, venues, away_key, away_state)
            survived = weight * math.exp(-(home_rate + away_rate))
            if window == 2:
                next_alive[(home_state, away_state)] += survived
                continue
            home_query = {"team_id": fixture["home_team_id"], "league_slug": fixture["league_slug"], "is_home": True, "window_index": window, "score_bucket": "level", "state": home_state, "opponent_state": away_state}
            away_query = {"team_id": fixture["away_team_id"], "league_slug": fixture["league_slug"], "is_home": False, "window_index": window, "score_bucket": "level", "state": away_state, "opponent_state": home_state}
            home_next, _ = model.predict(home_query)
            away_next, _ = model.predict(away_query)
            for next_home in home:
                for next_away in away:
                    next_alive[(next_home, next_away)] += survived * home_next[next_home] * away_next[next_away]
        alive = next_alive
    return 1.0 - sum(alive.values()), 1.0 - math.exp(-baseline_rate)


def _predict(fixture: dict[str, Any], histories: dict[int, list[dict[str, Any]]], totals: dict[tuple[str, bool, int], list[float]], states: dict[tuple[str, bool, int, str], list[float]], venues: dict[tuple[bool, int], list[float]], model: Any, candidates: dict[str, SoftTransitionModel]) -> dict[str, Any]:
    """Genera todas las variantes antes de observar el target."""

    home_features = {"is_home": 1.0, **_profile(histories[int(fixture["home_team_id"])][-5:])}
    away_features = {"is_home": 0.0, **_profile(histories[int(fixture["away_team_id"])][-5:])}
    home = _probabilities(model, home_features)
    away = _probabilities(model, away_features)
    outputs = {}
    baseline = None
    for name, transition_model in candidates.items():
        markov, baseline = _trajectory(fixture, home, away, totals, states, venues, transition_model)
        outputs[name] = markov
    return {"fixture": fixture, "soft_probabilities": outputs, "baseline_probability": baseline, "state_0_home": home, "state_0_away": away, "entropy_home": _entropy(home), "entropy_away": _entropy(away), "target_used_before_prediction": False}


def _metrics(rows: list[dict[str, Any]], name: str, start: int = 0, end: int | None = None) -> dict[str, Any]:
    """Calcula métricas de una variante en un bloque temporal."""

    selected = rows[start:end]
    markov_losses, baseline_losses, markov_briers, baseline_briers = [], [], [], []
    for row in selected:
        actual = bool(row["actual_first_half_goal"])
        markov_loss, markov_brier = _loss(row["soft_probabilities"][name], actual)
        baseline_loss, baseline_brier = _loss(row["baseline_probability"], actual)
        markov_losses.append(markov_loss)
        baseline_losses.append(baseline_loss)
        markov_briers.append(markov_brier)
        baseline_briers.append(baseline_brier)
    deltas = [baseline - markov for baseline, markov in zip(baseline_losses, markov_losses)]
    return {"matches": len(selected), "markov_log_loss": float(np.mean(markov_losses)), "baseline_log_loss": float(np.mean(baseline_losses)), "markov_brier": float(np.mean(markov_briers)), "baseline_brier": float(np.mean(baseline_briers)), "improvement": _bootstrap(deltas)}


def run() -> dict[str, Any]:
    """Ajusta pooling en desarrollo, selecciona especificidad y audita holdout."""

    raw = _load(WINDOWS)
    history_raw, fixtures, cutoff = _fixtures(raw)
    grouped = _group(raw)
    history_state = _state_rows(history_raw)
    development_ids = {int(row["match_id"]) for row in history_raw}
    all_state = _state_rows(raw)
    transition_rows = build_transitions([row for row in all_state if int(row["match_id"]) in development_ids])
    candidates = {str(value): SoftTransitionModel(alpha=32.0, specificity=value) for value in SPECIFICITY_VALUES}
    for candidate in candidates.values():
        candidate.fit(transition_rows)
    histories: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in _records(history_state):
        histories[int(record["team_id"])].append(record)
    totals: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    state_totals: dict[tuple[str, bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    venue_totals: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    _add_stats(history_state, totals, state_totals, venue_totals)
    model = joblib.load(MODEL)
    predictions = []
    for fixture in fixtures:
        prediction = _predict(fixture, histories, totals, state_totals, venue_totals, model, candidates)
        target_rows = grouped[int(fixture["match_id"])]
        actual = sum(float(row["goals"]) for row in target_rows if int(row["window_index"]) < 3) > 0
        predictions.append({**prediction, "actual_first_half_goal": actual})
        target_state = _state_rows(target_rows)
        _add_stats(target_state, totals, state_totals, venue_totals)
        for record in _records(target_state):
            histories[int(record["team_id"])].append(record)
    split = len(predictions) // 2
    validation = {name: _metrics(predictions, name, 0, split) for name in candidates}
    selected = min(validation, key=lambda name: (validation[name]["markov_log_loss"], float(name)))
    holdout = _metrics(predictions, selected, split, None)
    all_metrics = _metrics(predictions, selected)
    metrics = {"development_matches": len(development_ids), "audit_matches": len(predictions), "development_cutoff": cutoff, "transition_rows": len(transition_rows), "validation": validation, "selected_specificity": float(selected), "holdout_selected": holdout, "all_selected": all_metrics}
    audit = {"classification": "soft_transition_candidate_requires_confirmation" if holdout["markov_log_loss"] < holdout["baseline_log_loss"] else "soft_transition_recalibration_no_incremental_value", "target_used_before_prediction": False, "walk_forward_order": True, "transition_fit_development_only": True, "router_modified": False, "markov_promoted": False}
    result = {"config": {"alpha": 32.0, "specificity_candidates": list(SPECIFICITY_VALUES), "market": "first_half_goal"}, "metrics": metrics, "predictions": predictions, "audit": audit}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 66 — recalibración suave de transiciones", "", f"**Clasificación:** `{audit['classification']}`", "", f"- desarrollo: `{len(development_ids)}` partidos / `{len(transition_rows)}` transiciones", f"- auditoría walk-forward: `{len(predictions)}` partidos", f"- specificity seleccionada: `{selected}`", f"- holdout Markov: `{holdout['markov_log_loss']}`", f"- holdout baseline: `{holdout['baseline_log_loss']}`", f"- mejora holdout: `{holdout['improvement']['mean']}`", f"- IC holdout: `{holdout['improvement']['ci_95']}`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 66 pooling suave: %s", audit["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["audit"]["classification"].startswith("soft_transition_") else 1)

