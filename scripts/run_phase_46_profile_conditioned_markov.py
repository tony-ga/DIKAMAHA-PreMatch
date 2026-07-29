"""Evalúa Markov con estado inicial condicionado por perfil pre-match.

La hipótesis es que la identidad del equipo no basta para elegir S0. Se usa el
promedio reciente de ritmo, presión y disciplina, calculado sólo con partidos
anteriores al kickoff. Los perfiles y priors se ajustan en desarrollo; los
targets de validación/confirmación sólo se leen después de predecir.

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_42_multileague_structural_fusion import (
    Config as FusionConfig,
    _aggregate,
    _matrix_index,
    _priors_and_weights,
    _trajectory,
)
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
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_39_multileague_state_labeling_v1/state_labels.json"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transitions.json"
BASE_PREDICTIONS = ROOT / "artifacts/phase_44_multileague_precision_diagnosis_v1/corrected_predictions.json"
OUTPUT = ROOT / "artifacts/phase_46_profile_conditioned_markov_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
FEATURES = ("tempo", "pressure", "discipline")


def _team_matches(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega estadísticas observadas por equipo y partido."""

    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    metrics = ("goals", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards")
    for row in windows:
        key = (int(row["match_id"]), int(row["team_id"]))
        item = grouped.setdefault(key, {"match_id": key[0], "team_id": key[1], "league_slug": str(row["league_slug"]), "match_date": str(row["match_date"]), "is_home": bool(row["is_home"]), **{name: 0.0 for name in metrics}})
        for name in metrics:
            item[name] += float(row[name])
    return sorted(grouped.values(), key=lambda row: (row["match_date"], row["match_id"], row["team_id"]))


def _profiles(rows: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Calcula el perfil previo sin mezclar equipos del mismo kickoff."""

    history: dict[int, list[dict[str, Any]]] = defaultdict(list)
    output: dict[tuple[int, int], dict[str, Any]] = {}
    dates = sorted({str(row["match_date"]) for row in rows})
    for date in dates:
        bucket = [row for row in rows if str(row["match_date"]) == date]
        for row in bucket:
            prior = history[int(row["team_id"])][-5:]
            output[(int(row["match_id"]), int(row["team_id"]))] = _profile(prior)
        for row in bucket:
            history[int(row["team_id"])].append(row)
    return output


def _profile(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume ritmo, presión y disciplina de los últimos cinco partidos."""

    if not history:
        return {"history_count": 0, **{feature: None for feature in FEATURES}}
    tempo = sum(row["shots"] + row["shots_on_target"] + row["corners"] for row in history) / len(history)
    pressure = sum(row["pressure"] + row["shots_on_target"] for row in history) / len(history)
    discipline = sum(row["fouls"] + 2.0 * row["yellow_cards"] + 4.0 * row["red_cards"] for row in history) / len(history)
    return {"history_count": len(history), "tempo": tempo, "pressure": pressure, "discipline": discipline}


def _thresholds(profiles: dict[tuple[int, int], dict[str, Any]], rows: list[dict[str, Any]], development: set[int]) -> dict[str, tuple[float, float]]:
    """Fija terciles de perfil usando únicamente desarrollo."""

    values = {feature: sorted(float(profile[feature]) for (match_id, _), profile in profiles.items() if match_id in development and profile["history_count"] >= 3 and profile[feature] is not None) for feature in FEATURES}
    return {feature: (_quantile(values[feature], 1 / 3), _quantile(values[feature], 2 / 3)) for feature in FEATURES}


def _quantile(values: list[float], fraction: float) -> float:
    """Calcula cuantiles lineales sin depender de pandas."""

    if not values:
        return 0.0
    index = (len(values) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return values[low] if low == high else values[low] + (values[high] - values[low]) * (index - low)


def _profile_key(profile: dict[str, Any], thresholds: dict[str, tuple[float, float]]) -> tuple[int, int, int] | None:
    """Codifica el perfil en tres niveles discretos."""

    if profile["history_count"] < 3:
        return None
    return tuple(0 if profile[name] <= thresholds[name][0] else 1 if profile[name] <= thresholds[name][1] else 2 for name in FEATURES)


def _distribution(counts: Counter[str], parent: dict[str, float], alpha: float = 8.0) -> dict[str, float]:
    """Aplica shrinkage hacia el prior padre."""

    total = sum(counts.values()) + alpha
    return {state: (counts[state] + alpha * parent[state]) / total for state in STATES}


def _build_priors(windows: list[dict[str, Any]], labels: list[dict[str, Any]], profiles: dict[tuple[int, int], dict[str, Any]], development: set[int], thresholds: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """Ajusta priors por perfil y sus backoffs jerárquicos."""

    labels_index = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): str(row["state"]) for row in labels}
    global_counts: dict[bool, Counter[str]] = {True: Counter(), False: Counter()}
    league_counts: dict[tuple[str, bool], Counter[str]] = defaultdict(Counter)
    team_counts: dict[tuple[int, bool], Counter[str]] = defaultdict(Counter)
    profile_counts: dict[tuple[str, bool, tuple[int, int, int]], Counter[str]] = defaultdict(Counter)
    profile_support: Counter[str] = Counter()
    for row in windows:
        match_id, team, window = int(row["match_id"]), int(row["team_id"]), int(row["window_index"])
        if match_id not in development or window != 0:
            continue
        state = labels_index.get((match_id, team, window))
        if state not in STATES:
            continue
        venue, league = bool(row["is_home"]), str(row["league_slug"])
        global_counts[venue][state] += 1; league_counts[(league, venue)][state] += 1; team_counts[(team, venue)][state] += 1
        key = _profile_key(profiles[(match_id, team)], thresholds)
        if key is not None:
            profile_counts[(league, venue, key)][state] += 1; profile_support[str((league, venue, key))] += 1
    uniform = {state: 1.0 / len(STATES) for state in STATES}
    global_prior = {venue: _distribution(global_counts[venue], uniform) for venue in (True, False)}
    league_prior = {(league, venue): _distribution(counts, global_prior[venue]) for (league, venue), counts in league_counts.items()}
    team_prior = {(team, venue): _distribution(counts, league_prior.get((_team_league(team, windows), venue), global_prior[venue])) for (team, venue), counts in team_counts.items()}
    profile_prior = {key: _distribution(counts, league_prior.get((key[0], key[1]), global_prior[key[1]])) for key, counts in profile_counts.items() if sum(counts.values()) >= 8}
    return {"global": global_prior, "league": league_prior, "team": team_prior, "profile": profile_prior, "profile_support": dict(profile_support)}


def _team_league(team: int, rows: list[dict[str, Any]]) -> str | None:
    """Obtiene la liga dominante del equipo para fallback."""

    counts = Counter(str(row["league_slug"]) for row in rows if int(row["team_id"]) == team)
    return counts.most_common(1)[0][0] if counts else None


def _prior(row: dict[str, Any], venue: bool, priors: dict[str, Any], profiles: dict[tuple[int, int], dict[str, Any]], thresholds: dict[str, tuple[float, float]]) -> tuple[dict[str, float], str]:
    """Selecciona perfil→equipo→liga→global para un equipo objetivo."""

    team = int(row["home_team_id"] if venue else row["away_team_id"])
    profile = profiles.get((int(row["match_id"]), team), {"history_count": 0})
    key = _profile_key(profile, thresholds)
    profile_key = (str(row["league_slug"]), venue, key) if key is not None else None
    if profile_key in priors["profile"]:
        return priors["profile"][profile_key], "profile"
    team_key = (team, venue)
    if team_key in priors["team"]:
        return priors["team"][team_key], "team"
    league_key = (str(row["league_slug"]), venue)
    if league_key in priors["league"]:
        return priors["league"][league_key], "league"
    return priors["global"][venue], "global"


def _simulate(row: dict[str, Any], profile_priors: dict[bool, dict[str, float]], data: dict[str, Any], matrices: dict[str, dict[tuple[Any, ...], dict[str, Any]]], config: FusionConfig) -> dict[str, Any]:
    """Simula temporalmente un partido con prior inicial condicionado."""

    dynamic = {**data, "team_prior": {**data["team_prior"], (int(row["home_team_id"]), True): profile_priors[True], (int(row["away_team_id"]), False): profile_priors[False]}}
    lambdas = {True: float(row["lambda_base_home"]), False: float(row["lambda_base_away"])}
    rng = random.Random(config.seed + int(row["match_id"]))
    samples = [_trajectory(row, lambdas, dynamic, matrices, config, rng) for _ in range(config.simulations_per_match)]
    result = _aggregate(samples, row, lambdas, config)
    exact = _poisson_probs(lambdas[True], lambdas[False])
    result.update({"prob_1": exact["1"], "prob_x": exact["X"], "prob_2": exact["2"], "prob_over_2_5": exact["over_2_5"], "prob_btts": exact["btts"], "profile_conditioned": True})
    return {**row, **result}


def _write(result: dict[str, Any], source: dict[str, Any]) -> None:
    """Publica predicciones, métricas y auditoría."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "profile_priors.json": result["profile_priors"], "predictions.json": result["predictions"], "metrics.json": result["metrics"], "coverage.json": result["coverage"], "audit.json": result["audit"], "input_manifest.json": {name: _hash(value) for name, value in source.items()}}
    for name, value in payloads.items():
        target = OUTPUT / name; temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8"); temporary.replace(target)
    report = ["# Fase 46 — Markov condicionado por perfil pre-match", "", f"**Clasificación:** `{result['audit']['classification']}`", "", f"- predicciones: `{result['coverage']['predictions']}`", f"- cobertura del prior por perfil: `{result['coverage']['profile_prior_rate']:.4f}`", "- perfiles usados: `ritmo, presión, disciplina`", "- entrenamiento: `desarrollo temporal únicamente`", "- targets usados antes de predecir: `False`", "- router oficial: `sin cambios`", "- siguiente paso: `conservar sólo si mejora confirmación con IC positivo`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta el candidato de perfil y su evaluación OOS."""

    windows, labels, transitions, base_predictions = _load(WINDOWS), _load(LABELS), _load(TRANSITIONS), _load(BASE_PREDICTIONS)
    source = {"windows": windows, "labels": labels, "transitions": transitions, "base_predictions": base_predictions}
    development = _development_ids(transitions)
    team_rows, profiles = _team_matches(windows), None
    profiles = _profiles(team_rows)
    thresholds = _thresholds(profiles, team_rows, development)
    profile_priors = _build_priors(windows, labels, profiles, development, thresholds)
    data = _priors_and_weights(windows, labels, development, FusionConfig())
    matrices = _matrix_index(_load(ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transition_matrices.json"))
    config = FusionConfig(seed=20260728, simulations_per_match=300)
    predictions = []
    for row in base_predictions:
        priors = {venue: _prior(row, venue, profile_priors, profiles, thresholds)[0] for venue in (True, False)}
        predictions.append(_simulate(row, priors, data, matrices, config))
    targets = _targets(windows); scored = _prepare(predictions, targets); counts = _counts([], targets, development)
    evaluation_config = {"bootstrap_samples": 2000, "bootstrap_seed": 20260728}
    metrics = {split: {market: _metrics([row for row in scored if row["split"] == split], market, counts, evaluation_config) for market in MARKETS} for split in ("validation", "confirmation")}
    levels = [_prior(row, venue, profile_priors, profiles, thresholds)[1] for row in predictions for venue in (True, False)]
    profile_used = sum(level == "profile" for level in levels)
    audit = {"classification": "profile_candidate_evaluated_no_promotion", "predictions": len(predictions), "profile_prior_rows": profile_used, "prior_rows": len(levels), "target_used_as_feature": False, "development_only_profile_fit": True, "router_modified": False, "markets_promoted": False}
    result = {"config": {"simulations_per_match": config.simulations_per_match, "seed": config.seed, "profile_features": FEATURES, "history_matches": 5}, "profile_priors": {"thresholds": thresholds, "support": profile_priors["profile_support"]}, "predictions": predictions, "metrics": metrics, "coverage": {"predictions": len(predictions), "validation": sum(row["split"] == "validation" for row in predictions), "confirmation": sum(row["split"] == "confirmation" for row in predictions), "profile_prior_rate": profile_used / max(len(levels), 1)}, "audit": audit}
    _write(result, source)
    LOGGER.info("Fase 46 Markov por perfil: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 46."""

    return 0 if run()["audit"]["classification"] == "profile_candidate_evaluated_no_promotion" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
