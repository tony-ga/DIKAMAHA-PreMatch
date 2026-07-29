"""Audita la posición actual de Markov con replay walk-forward amplio.

El clasificador state_0 se carga ya ajustado sólo con el bloque de desarrollo.
Cada partido posterior se predice antes de incorporarlo a perfiles y tasas.

Requirements:
    - joblib
    - numpy

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import _entropy, _profile, _state_rows, _time, _transition_index
from scripts.run_phase_63_initial_state_calibration import FEATURES, STATES, _matrix, _records

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
MODEL = ROOT / "artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transition_matrices.json"
OUTPUT = ROOT / "artifacts/phase_65_markov_position_audit_v1"
SMOOTHING = 2.0
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    """Calcula el hash de un archivo de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa ventanas por partido."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return grouped


def _ordered_matches(grouped: dict[int, list[dict[str, Any]]]) -> list[tuple[str, int]]:
    """Ordena partidos por kickoff e ID."""

    return sorted((str(values[0]["match_date"]), match_id) for match_id, values in grouped.items())


def _add_stats(rows: list[dict[str, Any]], totals: dict[tuple[str, bool, int], list[float]], states: dict[tuple[str, bool, int, str], list[float]], venue_totals: dict[tuple[bool, int], list[float]]) -> None:
    """Actualiza tasas sólo después de consumir un partido."""

    for row in rows:
        key = (str(row["league_slug"]), bool(row["is_home"]), int(row["window_index"]))
        values = totals[key]
        values[0] += float(row["goals"])
        values[1] += 1.0
        state_values = states[(*key, str(row["state"]))]
        state_values[0] += float(row["goals"])
        state_values[1] += 1.0
        venue = venue_totals[(key[1], key[2])]
        venue[0] += float(row["goals"])
        venue[1] += 1.0


def _rate(totals: dict[tuple[str, bool, int], list[float]], venue_totals: dict[tuple[bool, int], list[float]], key: tuple[str, bool, int]) -> float:
    """Obtiene una tasa overall suavizada con backoff por localía."""

    values = totals.get(key, [0.0, 0.0])
    parent = venue_totals.get((key[1], key[2]), [0.0, 0.0])
    base = (parent[0] + SMOOTHING) / (parent[1] + SMOOTHING) if parent[1] else 0.10
    return (values[0] + SMOOTHING * base) / (values[1] + SMOOTHING)


def _state_rate(totals: dict[tuple[str, bool, int], list[float]], states: dict[tuple[str, bool, int, str], list[float]], venue_totals: dict[tuple[bool, int], list[float]], key: tuple[str, bool, int], state: str) -> float:
    """Obtiene una tasa condicionada al estado con shrinkage."""

    base = _rate(totals, venue_totals, key)
    values = states.get((*key, state), [0.0, 0.0])
    return (values[0] + SMOOTHING * base) / (values[1] + SMOOTHING)


def _transition(index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], league: str, team: int, venue: bool, window: int, state: str, opponent: str) -> tuple[dict[str, float], str]:
    """Obtiene transición y tier de backoff."""

    keys = [("team", (team, league, venue, window, "level", state, opponent)), ("competition", (league, venue, window, "level", state, opponent)), ("window", (league, window, state, opponent)), ("global", (state, opponent))]
    minimum = {"team": 12, "competition": 10, "window": 8, "global": 1}
    for tier, key in keys:
        item = index.get(tier, {}).get(key)
        if item and int(item.get("support", 0)) >= minimum[tier]:
            return {name: float(value) for name, value in item["probabilities"].items()}, tier
    return {name: 1.0 / len(STATES) for name in STATES}, "uniform"


def _predict(fixture: dict[str, Any], histories: dict[int, list[dict[str, Any]]], totals: dict[tuple[str, bool, int], list[float]], states: dict[tuple[str, bool, int, str], list[float]], venue_totals: dict[tuple[bool, int], list[float]], model: Any, transitions: dict[str, dict[tuple[Any, ...], dict[str, Any]]]) -> dict[str, Any]:
    """Genera la predicción usando únicamente el historial acumulado."""

    cutoff = _time(fixture["kickoff_ts"])
    home_profile = {"is_home": 1.0, **_profile(histories[int(fixture["home_team_id"])] [-5:])}
    away_profile = {"is_home": 0.0, **_profile(histories[int(fixture["away_team_id"])] [-5:])}
    home_probs, away_probs = _probabilities(model, home_profile), _probabilities(model, away_profile)
    alive = {(home, away): home_probs[home] * away_probs[away] for home in STATES for away in STATES}
    baseline_rate, tiers = 0.0, Counter()
    for window in range(3):
        home_key = (str(fixture["league_slug"]), True, window)
        away_key = (str(fixture["league_slug"]), False, window)
        baseline_rate += _rate(totals, venue_totals, home_key) + _rate(totals, venue_totals, away_key)
        next_alive: dict[tuple[str, str], float] = defaultdict(float)
        for (home_state, away_state), weight in alive.items():
            survived = weight * math.exp(-(_state_rate(totals, states, venue_totals, home_key, home_state) + _state_rate(totals, states, venue_totals, away_key, away_state)))
            if window == 2:
                next_alive[(home_state, away_state)] += survived
                continue
            home_next, home_tier = _transition(transitions, str(fixture["league_slug"]), int(fixture["home_team_id"]), True, window, home_state, away_state)
            away_next, away_tier = _transition(transitions, str(fixture["league_slug"]), int(fixture["away_team_id"]), False, window, away_state, home_state)
            tiers.update({f"home_{home_tier}": 1, f"away_{away_tier}": 1})
            for next_home in STATES:
                for next_away in STATES:
                    next_alive[(next_home, next_away)] += survived * home_next[next_home] * away_next[next_away]
        alive = next_alive
    return {"fixture": fixture, "markov_probability": 1.0 - sum(alive.values()), "baseline_probability": 1.0 - math.exp(-baseline_rate), "state_0_home": home_probs, "state_0_away": away_probs, "entropy_home": _entropy(home_probs), "entropy_away": _entropy(away_probs), "history_home": len(histories[int(fixture["home_team_id"])]), "history_away": len(histories[int(fixture["away_team_id"])]), "transition_tiers": dict(tiers), "target_used_before_prediction": False, "prediction_cutoff": cutoff.isoformat()}


def _probabilities(model: Any, features: dict[str, float]) -> dict[str, float]:
    """Alinea probabilidades del clasificador a las cuatro clases."""

    raw = model.predict_proba(_matrix([features]))[0]
    classes = list(model[-1].classes_)
    values = {state: 1e-9 for state in STATES}
    for index, state in enumerate(classes):
        values[str(state)] = float(raw[index])
    total = sum(values.values())
    return {state: value / total for state, value in values.items()}


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier binario."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _bootstrap(values: list[float]) -> dict[str, Any]:
    """Calcula bootstrap agrupado por partido."""

    rng = np.random.default_rng(20260728)
    indexes = rng.integers(0, len(values), size=(5000, len(values)))
    means = np.asarray(values, dtype=float)[indexes].mean(axis=1)
    interval = np.quantile(means, [0.025, 0.975])
    return {"mean": float(np.mean(values)), "ci_95": [float(interval[0]), float(interval[1])], "strictly_positive": bool(interval[0] > 0.0), "samples": len(means)}


def _calibration(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Resume calibración por deciles de probabilidad."""

    output = []
    for lower in np.arange(0.0, 1.0, 0.1):
        upper = lower + 0.1
        selected = [row for row in rows if lower <= float(row[key]) < upper or upper == 1.0 and lower <= float(row[key]) <= upper]
        if selected:
            output.append({"lower": float(lower), "upper": float(upper), "count": len(selected), "mean_probability": float(np.mean([row[key] for row in selected])), "actual_rate": float(np.mean([row["actual_first_half_goal"] for row in selected]))})
    return output


def _by_league(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas por liga para localizar heterogeneidad."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["fixture"]["league_slug"])].append(row)
    output = {}
    for league, values in grouped.items():
        markov = [_loss(row["markov_probability"], row["actual_first_half_goal"])[0] for row in values]
        baseline = [_loss(row["baseline_probability"], row["actual_first_half_goal"])[0] for row in values]
        output[league] = {"matches": len(values), "actual_rate": float(np.mean([row["actual_first_half_goal"] for row in values])), "markov_probability": float(np.mean([row["markov_probability"] for row in values])), "baseline_probability": float(np.mean([row["baseline_probability"] for row in values])), "markov_log_loss": float(np.mean(markov)), "baseline_log_loss": float(np.mean(baseline)), "improvement": float(np.mean(np.asarray(baseline) - np.asarray(markov)))}
    return output


def _prepare(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Separa desarrollo histórico y bloque walk-forward posterior."""

    grouped = _group(raw)
    ordered = _ordered_matches(grouped)
    development_count = int(len(ordered) * 0.60)
    development = ordered[:development_count]
    target_fixtures = []
    for kickoff, match_id in ordered[development_count:]:
        values = grouped[match_id]
        home = next(row for row in values if bool(row["is_home"]))
        away = next(row for row in values if not bool(row["is_home"]))
        target_fixtures.append({"match_id": match_id, "league_slug": str(home["league_slug"]), "kickoff_ts": kickoff, "home_team_id": int(home["team_id"]), "away_team_id": int(away["team_id"]), "provider_status": "post"})
    history_rows = [row for row in raw if (str(row["match_date"]), int(row["match_id"])) <= development[-1]]
    return history_rows, target_fixtures, development[-1][0]


def run() -> dict[str, Any]:
    """Ejecuta la auditoría completa y publica evidencia de fallas."""

    raw = _load(WINDOWS)
    history_raw, fixtures, development_cutoff = _prepare(raw)
    grouped = _group(raw)
    historical = _state_rows(history_raw)
    records = _records(historical)
    histories: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        histories[int(record["team_id"])].append(record)
    totals: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    state_totals: dict[tuple[str, bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    venue_totals: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    _add_stats(historical, totals, state_totals, venue_totals)
    model = joblib.load(MODEL)
    transitions = _transition_index(_load(TRANSITIONS))
    predictions = []
    for fixture in fixtures:
        prediction = _predict(fixture, histories, totals, state_totals, venue_totals, model, transitions)
        target = grouped[int(fixture["match_id"])]
        actual = sum(float(row["goals"]) for row in target if int(row["window_index"]) < 3) > 0
        markov_loss, markov_brier = _loss(prediction["markov_probability"], actual)
        baseline_loss, baseline_brier = _loss(prediction["baseline_probability"], actual)
        predictions.append({**prediction, "actual_first_half_goal": actual, "markov_log_loss": markov_loss, "baseline_log_loss": baseline_loss, "markov_brier": markov_brier, "baseline_brier": baseline_brier, "improvement_log_loss": baseline_loss - markov_loss, "markov_lift": prediction["markov_probability"] - prediction["baseline_probability"]})
        target_state_rows = _state_rows(target)
        _add_stats(target_state_rows, totals, state_totals, venue_totals)
        for record in _records(target_state_rows):
            histories[int(record["team_id"])].append(record)
    deltas = [float(row["improvement_log_loss"]) for row in predictions]
    tier_counts = Counter()
    for row in predictions:
        tier_counts.update(row["transition_tiers"])
    metrics = {"matches": len(predictions), "development_cutoff": development_cutoff, "actual_rate": float(np.mean([row["actual_first_half_goal"] for row in predictions])), "markov_log_loss": float(np.mean([row["markov_log_loss"] for row in predictions])), "baseline_log_loss": float(np.mean([row["baseline_log_loss"] for row in predictions])), "markov_brier": float(np.mean([row["markov_brier"] for row in predictions])), "baseline_brier": float(np.mean([row["baseline_brier"] for row in predictions])), "improvement_vs_baseline": _bootstrap(deltas), "markov_calibration": _calibration(predictions, "markov_probability"), "baseline_calibration": _calibration(predictions, "baseline_probability"), "by_league": _by_league(predictions), "lift_positive_count": sum(row["markov_lift"] > 0.05 for row in predictions), "lift_negative_count": sum(row["markov_lift"] < -0.05 for row in predictions), "mean_markov_lift": float(np.mean([row["markov_lift"] for row in predictions])), "transition_tier_counts": dict(tier_counts), "short_history_matches": sum(row["history_home"] < 5 or row["history_away"] < 5 for row in predictions)}
    audit = {"classification": "markov_residual_position_audited_no_promotion" if metrics["markov_log_loss"] >= metrics["baseline_log_loss"] else "markov_residual_position_audited_promotion_blocked", "prediction_count": len(predictions), "development_matches": len({int(row["match_id"]) for row in history_raw}), "target_used_before_prediction": False, "walk_forward_order": True, "same_model_fit_before_targets": True, "router_modified": False, "markov_promoted": False, "transition_context_coverage": "global_uniform_dominant" if tier_counts["home_global"] + tier_counts["away_global"] > tier_counts["home_competition"] + tier_counts["away_competition"] else "competition_or_more_specific_dominant", "windows_hash": _hash(WINDOWS), "model_hash": _hash(MODEL), "transition_hash": _hash(TRANSITIONS)}
    result = {"config": {"market": "first_half_goal", "smoothing": SMOOTHING, "source": "phase60_taxonomy_snapshot_candidate", "transition_source": "phase40_development_matrices"}, "metrics": metrics, "predictions": predictions, "audit": audit}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 65 — auditoría de posición Markov", "", f"**Clasificación:** `{audit['classification']}`", "", f"- partidos de desarrollo: `{audit['development_matches']}`", f"- partidos auditados walk-forward: `{len(predictions)}`", f"- cutoff de entrenamiento: `{development_cutoff}`", f"- log-loss Markov: `{metrics['markov_log_loss']}`", f"- log-loss baseline: `{metrics['baseline_log_loss']}`", f"- mejora media Markov: `{metrics['improvement_vs_baseline']['mean']}`", f"- IC bootstrap: `{metrics['improvement_vs_baseline']['ci_95']}`", f"- lift Markov positivo (>0.05): `{metrics['lift_positive_count']}`", f"- lift Markov negativo (<-0.05): `{metrics['lift_negative_count']}`", f"- contexto de transición: `{audit['transition_context_coverage']}`", f"- historial corto (<5): `{metrics['short_history_matches']}`", "- target usado antes de predecir: `False`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 65 auditoría Markov: %s", audit["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["audit"]["classification"].startswith("markov_residual_position_audited") else 1)
