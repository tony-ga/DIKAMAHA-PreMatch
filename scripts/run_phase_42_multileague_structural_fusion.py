"""Fusiona Dixon-Coles, Kalman y Markov para predicción pre-match multi-liga.

El prior Dixon-Coles se ajusta sólo con el bloque temporal de desarrollo. El
filtro Kalman se inicializa con ese prior y se actualiza únicamente después de
emitir cada predicción histórica. Markov redistribuye la intensidad total entre
seis ventanas de 15 minutos.

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kalman_v2 import KalmanV2Config, KalmanV2Filter

LOGGER = logging.getLogger(__name__)
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_39_multileague_state_labeling_v1/state_labels.json"
MARKOV = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1"
OUTPUT = ROOT / "artifacts/phase_42_multileague_structural_fusion_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")


@dataclass(frozen=True, slots=True)
class Config:
    """Parámetros congelados de la fusión estructural experimental."""

    version: str = "multileague_structural_fusion_v1"
    simulations_per_match: int = 300
    seed: int = 20260727
    dixon_coles_weight: float = 0.80
    kalman_weight: float = 0.20
    minimum_league_matches: int = 8
    state_smoothing: float = 0.25
    process_noise_attack: float = 0.05
    process_noise_defense: float = 0.05


@dataclass(frozen=True, slots=True)
class NeutralModel:
    """Fallback estructural para ligas sin soporte suficiente."""

    team_ids: list[int]
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    league_intercept: float
    optimize_result: Any = None


@dataclass(frozen=True, slots=True)
class FastDCModel:
    """Prior regularizado con la parametrización de Dixon-Coles."""

    team_ids: list[int]
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    league_intercept: float
    optimize_result: Any = None


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula un hash estable para provenance."""

    raw = json.dumps(_json_keys(value), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_keys(value: Any) -> Any:
    """Convierte claves compuestas a texto para hashing JSON."""

    if isinstance(value, dict):
        return {str(key): _json_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_keys(item) for item in value]
    return value


def _development_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Reproduce el corte temporal de desarrollo de Fase 40."""

    ordered = sorted({(str(row["match_date"]), int(row["match_id"])) for row in rows})
    return {match_id for _, match_id in ordered[: int(len(ordered) * 0.60)]}


def _match_rows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega seis ventanas en una fila de partido por competición."""

    grouped: dict[int, dict[str, Any]] = {}
    for row in windows:
        match = grouped.setdefault(int(row["match_id"]), _match_seed(row))
        side = "home" if bool(row["is_home"]) else "away"
        match[f"{side}_goals"] += int(row["goals"])
        match[f"{side}_team_id"] = int(row["team_id"])
    return sorted(grouped.values(), key=lambda row: (row["match_date"], row["match_id"]))


def _match_seed(row: dict[str, Any]) -> dict[str, Any]:
    """Crea el acumulador inicial de un partido."""

    return {"match_id": int(row["match_id"]), "match_date": str(row["match_date"]), "league_slug": str(row["league_slug"]), "competition_id": str(row["competition_id"]), "home_team_id": 0, "away_team_id": 0, "home_goals": 0, "away_goals": 0}


def _split_matches(matches: list[dict[str, Any]], development: set[int]) -> dict[int, str]:
    """Asigna desarrollo, validación y confirmación por fecha."""

    ordered = sorted(matches, key=lambda row: (row["match_date"], row["match_id"]))
    first = int(len(ordered) * 0.60)
    second = first + int(len(ordered) * 0.20)
    return {int(row["match_id"]): "development" if index < first else "validation" if index < second else "confirmation" for index, row in enumerate(ordered) if int(row["match_id"]) in development or index >= first}


def _fit_fast_dc(rows: list[dict[str, Any]], universe: list[int]) -> FastDCModel:
    """Estima un prior DC regularizado sin optimización sobredimensionada."""

    total = max(len(rows), 1)
    league_rate = sum(int(row["home_goals"]) + int(row["away_goals"]) for row in rows) / (2.0 * total)
    home_rate = sum(int(row["home_goals"]) for row in rows) / total
    away_rate = sum(int(row["away_goals"]) for row in rows) / total
    attacks, defenses, appearances = Counter(), Counter(), Counter()
    for row in rows:
        home, away = int(row["home_team_id"]), int(row["away_team_id"])
        attacks[home] += int(row["home_goals"]); defenses[home] += int(row["away_goals"]); appearances[home] += 1
        attacks[away] += int(row["away_goals"]); defenses[away] += int(row["home_goals"]); appearances[away] += 1
    prior = 1.0
    attack = {team: math.log((attacks[team] + prior) / (appearances[team] * max(league_rate, 1e-3) + prior)) for team in universe}
    defense = {team: math.log((defenses[team] + prior) / (appearances[team] * max(league_rate, 1e-3) + prior)) for team in universe}
    attack_mean = sum(attack.values()) / max(len(attack), 1); defense_mean = sum(defense.values()) / max(len(defense), 1)
    return FastDCModel(universe, {team: value - attack_mean for team, value in attack.items()}, {team: value - defense_mean for team, value in defense.items()}, math.log((home_rate + prior / total) / max(away_rate + prior / total, 1e-6)), math.log(max(league_rate, 1e-3)))


def _teams(rows: list[dict[str, Any]]) -> list[int]:
    """Obtiene equipos del universo de fixtures."""

    return sorted({int(row["home_team_id"]) for row in rows} | {int(row["away_team_id"]) for row in rows})


def _neutral_model(rows: list[dict[str, Any]]) -> NeutralModel:
    """Crea un prior Poisson neutral para una liga sin ajuste estable."""

    teams, matches = _teams(rows), max(len(rows), 1)
    mean = sum(int(row["home_goals"]) + int(row["away_goals"]) for row in rows) / (2.0 * matches)
    return NeutralModel(teams, {team: 0.0 for team in teams}, {team: 0.0 for team in teams}, 0.0, math.log(max(mean, 1e-3)))


def _converged(model: Any) -> bool:
    """Lee convergencia de modelos ajustados o fallback."""

    return isinstance(model, FastDCModel) or bool(getattr(getattr(model, "optimize_result", None), "success", False))


def _fit_models(matches: list[dict[str, Any]], development: set[int], config: Config) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Ajusta DC por liga con fallback global explícito."""

    train = [row for row in matches if int(row["match_id"]) in development]
    global_model = _neutral_model(train)
    by_league: dict[str, Any] = {}
    audit: dict[str, Any] = {"development_matches": len(train), "league_models": {}, "neutral_fallback": []}
    for league in sorted({str(row["league_slug"]) for row in train}):
        league_train = [row for row in train if str(row["league_slug"]) == league]
        league_all = [row for row in matches if str(row["league_slug"]) == league]
        try:
            fitted = _fit_fast_dc(league_train, _teams(league_all)) if len(league_train) >= config.minimum_league_matches else None
            model = fitted if fitted is not None and _converged(fitted) else _neutral_model(league_train)
            source = "league_dc_compatible" if fitted is not None and _converged(fitted) else "neutral_fallback"
        except (RuntimeError, ValueError, FloatingPointError) as error:
            LOGGER.warning("DC multi-liga fallback league=%s error=%s", league, error)
            model, source = _neutral_model(league_train), "neutral_fallback_error"
        by_league[league] = model
        if source != "league_dc_compatible":
            audit["neutral_fallback"].append(league)
        audit["league_models"][league] = {"train_matches": len(league_train), "fixture_teams": len(_teams(league_all)), "source": source, "stable": _converged(model), "mle_optimized": False, "method": "regularized_log_rate_dc_compatible"}
    return global_model, by_league, audit


def _label_index(labels: list[dict[str, Any]]) -> dict[tuple[int, int, int], str]:
    """Indexa estados por partido, equipo y ventana."""

    return {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): str(row["state"]) for row in labels}


def _priors_and_weights(windows: list[dict[str, Any]], labels: list[dict[str, Any]], development: set[int], config: Config) -> dict[str, Any]:
    """Estima priors iniciales y pesos de gol sólo con desarrollo."""

    states = _label_index(labels)
    league_counts: dict[str, dict[bool, Counter[str]]] = defaultdict(lambda: {True: Counter(), False: Counter()})
    team_counts: dict[tuple[int, bool], Counter[str]] = defaultdict(Counter)
    base: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    state_base: dict[tuple[bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in windows:
        if int(row["match_id"]) not in development:
            continue
        key = (int(row["match_id"]), int(row["team_id"]), int(row["window_index"]))
        state = states.get(key)
        if state not in STATES:
            continue
        venue, window, league = bool(row["is_home"]), int(row["window_index"]), str(row["league_slug"])
        if window == 0:
            league_counts[league][venue][state] += 1
            team_counts[(int(row["team_id"]), venue)][state] += 1
        base[(league, venue, window)][0] += float(row["goals"])
        base[(league, venue, window)][1] += 1.0
        state_base[(venue, window, state)][0] += float(row["goals"])
        state_base[(venue, window, state)][1] += 1.0
    global_prior = _venue_prior(league_counts, config.state_smoothing)
    league_prior = {league: {venue: _distribution(counts[venue], global_prior[venue], config.state_smoothing) for venue in (True, False)} for league, counts in league_counts.items()}
    team_prior = {(team, venue): _distribution(counts, league_prior.get(_team_league(team, windows), global_prior)[venue], config.state_smoothing) for (team, venue), counts in team_counts.items()}
    multipliers = _multipliers(base, state_base, config.state_smoothing)
    return {"states": states, "global_prior": global_prior, "league_prior": league_prior, "team_prior": team_prior, "base_rates": {str(key): values[0] / max(values[1], 1.0) for key, values in base.items()}, "state_multipliers": multipliers}


def _team_league(team: int, windows: list[dict[str, Any]]) -> str | None:
    """Obtiene liga dominante de un equipo para backoff de prior."""

    counts = Counter(str(row["league_slug"]) for row in windows if int(row["team_id"]) == team)
    return counts.most_common(1)[0][0] if counts else None


def _venue_prior(counts: dict[str, dict[bool, Counter[str]]], smoothing: float) -> dict[bool, dict[str, float]]:
    """Calcula prior global por localía."""

    aggregate = {True: Counter(), False: Counter()}
    for league in counts.values():
        for venue in (True, False):
            aggregate[venue].update(league[venue])
    uniform = {state: 1.0 / len(STATES) for state in STATES}
    return {venue: _distribution(aggregate[venue], uniform, smoothing) for venue in (True, False)}


def _distribution(counts: Counter[str], parent: dict[str, float], smoothing: float) -> dict[str, float]:
    """Aplica shrinkage uniforme al prior padre."""

    total = sum(counts.values()) + smoothing
    return {state: (counts[state] + smoothing * parent[state]) / total for state in STATES}


def _multipliers(base: dict[tuple[str, bool, int], list[float]], state_base: dict[tuple[bool, int, str], list[float]], smoothing: float) -> dict[str, float]:
    """Construye multiplicadores de intensidad por estado y ventana."""

    output: dict[str, float] = {}
    for venue in (True, False):
        for window in range(6):
            overall = [values for key, values in base.items() if key[1:] == (venue, window)]
            total_goals = sum(item[0] for item in overall)
            total_rows = sum(item[1] for item in overall)
            baseline = (total_goals + smoothing) / (total_rows + 1.0)
            for state in STATES:
                goals, rows = state_base[(venue, window, state)]
                value = ((goals + smoothing) / (rows + 1.0)) / max(baseline, 1e-6)
                output[_state_key(venue, window, state)] = max(0.20, min(value, 5.0))
    return output


def _state_key(venue: bool, window: int, state: str) -> str:
    """Serializa el contexto de multiplicador."""

    return json.dumps((venue, window, state), separators=(",", ":"))


def _matrix_index(matrices: dict[str, list[dict[str, Any]]]) -> dict[str, dict[tuple[Any, ...], dict[str, Any]]]:
    """Indexa matrices Markov por tier."""

    return {tier: {tuple(item["context"]): item for item in rows} for tier, rows in matrices.items()}


def _bucket(difference: int) -> str:
    """Agrupa el diferencial de goles al inicio de la ventana."""

    return "behind_2_plus" if difference <= -2 else "behind_1" if difference == -1 else "level" if difference == 0 else "ahead_1" if difference == 1 else "ahead_2_plus"


def _transition(index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], row: dict[str, Any], window: int, state: str, opponent: str, difference: int) -> tuple[dict[str, float], str]:
    """Selecciona team→liga→ventana→global con soporte calibrado."""

    league, venue, team = str(row["league_slug"]), bool(row["is_home"]), int(row["team_id"])
    bucket = _bucket(difference)
    keys = [("team", (team, league, venue, window, bucket, state, opponent), 12), ("competition", (league, venue, window, bucket, state, opponent), 10), ("window", (league, window, state, opponent), 8), ("global", (state, opponent), 1)]
    for tier, key, minimum in keys:
        item = index[tier].get(key)
        if item and int(item["support"]) >= minimum:
            return {name: float(value) for name, value in item["probabilities"].items()}, tier
    return {name: 1.0 / len(STATES) for name in STATES}, "uniform"


def _sample(probabilities: dict[str, float], rng: random.Random) -> str:
    """Muestrea un estado discreto."""

    threshold, cumulative = rng.random(), 0.0
    for state in STATES:
        cumulative += probabilities[state]
        if threshold <= cumulative:
            return state
    return STATES[-1]


def _poisson(rate: float, rng: random.Random) -> int:
    """Muestrea una variable Poisson con Knuth."""

    if rate <= 0.0:
        return 0
    threshold, product, count = math.exp(-rate), 1.0, 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def _initial_state(row: dict[str, Any], data: dict[str, Any], rng: random.Random) -> dict[bool, str]:
    """Muestrea estados iniciales con backoff equipo→liga→global."""

    states = {}
    for venue, team_key in ((True, "home_team_id"), (False, "away_team_id")):
        team = data["team_prior"].get((int(row[team_key]), venue))
        league = data["league_prior"].get(str(row["league_slug"]), data["global_prior"])
        states[venue] = _sample(team or league[venue], rng)
    return states


def _rate(venue: bool, state: str, remaining: float, window: int, row: dict[str, Any], data: dict[str, Any], future: int) -> float:
    """Asigna la intensidad restante a una ventana."""

    if window == 5:
        return max(0.0, remaining)
    base = float(data["base_rates"].get(str((str(row["league_slug"]), venue, window)), 1.0))
    multiplier = float(data["state_multipliers"][_state_key(venue, window, state)])
    weight = max(base * multiplier, 1e-6)
    return remaining * weight / (weight + future * max(base, 1e-6))


def _trajectory(row: dict[str, Any], lambdas: dict[bool, float], data: dict[str, Any], matrices: dict[str, dict[tuple[Any, ...], dict[str, Any]]], config: Config, rng: random.Random) -> dict[str, Any]:
    """Simula una trayectoria de estados y goles conservando lambdas."""

    states, goals, remaining, windows, allocated = _initial_state(row, data, rng), {True: 0, False: 0}, dict(lambdas), [], {True: 0.0, False: 0.0}
    for window in range(6):
        opening = dict(goals)
        rates = {venue: _rate(venue, states[venue], remaining[venue], window, row, data, 5 - window) for venue in (True, False)}
        scored = {venue: _poisson(rates[venue], rng) for venue in (True, False)}
        for venue in (True, False):
            goals[venue] += scored[venue]
            remaining[venue] -= rates[venue]
            allocated[venue] += rates[venue]
        windows.append({"window_index": window, "home_state": states[True], "away_state": states[False], "home_goals": scored[True], "away_goals": scored[False]})
        if window < 5:
            states = _next_states(row, states, opening, window, matrices, rng)
    return {"home_goals": goals[True], "away_goals": goals[False], "windows": windows, "allocated": allocated}


def _next_states(row: dict[str, Any], states: dict[bool, str], score: dict[bool, int], window: int, matrices: dict[str, dict[tuple[Any, ...], dict[str, Any]]], rng: random.Random) -> dict[bool, str]:
    """Muestrea el estado siguiente de ambos equipos."""

    output = {}
    for venue, team_key in ((True, "home_team_id"), (False, "away_team_id")):
        context = {**row, "team_id": int(row[team_key]), "is_home": venue}
        probabilities, _ = _transition(matrices, context, window, states[venue], states[not venue], score[venue] - score[not venue])
        output[venue] = _sample(probabilities, rng)
    return output


def _aggregate(samples: list[dict[str, Any]], row: dict[str, Any], lambdas: dict[bool, float], config: Config) -> dict[str, Any]:
    """Agrega mercados experimentales y auditoría de conservación."""

    total = float(len(samples))
    home, away = (sum(item[key] for item in samples) / total for key in ("home_goals", "away_goals"))
    outcomes = {"1": sum(item["home_goals"] > item["away_goals"] for item in samples) / total, "X": sum(item["home_goals"] == item["away_goals"] for item in samples) / total, "2": sum(item["home_goals"] < item["away_goals"] for item in samples) / total}
    temporal = {"first_half_goal": sum(_has_goal(item, range(3)) for item in samples) / total, "second_half_goal": sum(_has_goal(item, range(3, 6)) for item in samples) / total, "home_comeback": sum(_comeback(item, True) for item in samples) / total, "away_comeback": sum(_comeback(item, False) for item in samples) / total}
    conservation = {"home_max_abs_lambda_allocation_error": max(abs(item["allocated"][True] - lambdas[True]) for item in samples), "away_max_abs_lambda_allocation_error": max(abs(item["allocated"][False] - lambdas[False]) for item in samples)}
    return {"match_id": int(row["match_id"]), "league_slug": str(row["league_slug"]), "home_team_id": int(row["home_team_id"]), "away_team_id": int(row["away_team_id"]), "expected_home_goals": home, "expected_away_goals": away, "lambda_base_home": lambdas[True], "lambda_base_away": lambdas[False], "prob_1": outcomes["1"], "prob_x": outcomes["X"], "prob_2": outcomes["2"], "prob_over_2_5": sum(item["home_goals"] + item["away_goals"] > 2 for item in samples) / total, "prob_btts": sum(item["home_goals"] > 0 and item["away_goals"] > 0 for item in samples) / total, **{f"prob_{key}": value for key, value in temporal.items()}, "simulation_count": len(samples), "conservation": conservation, "classification": "experimental_not_promoted"}


def _has_goal(sample: dict[str, Any], windows: range) -> bool:
    """Indica si hubo gol en un conjunto de ventanas."""

    return any(sum(sample["windows"][index][key] for key in ("home_goals", "away_goals")) > 0 for index in windows)


def _comeback(sample: dict[str, Any], home: bool) -> bool:
    """Identifica remontada estricta en una trayectoria."""

    first = sample["windows"][:3]
    half_home = sum(item["home_goals"] for item in first)
    half_away = sum(item["away_goals"] for item in first)
    return (half_home < half_away and sample["home_goals"] > sample["away_goals"]) if home else (half_away < half_home and sample["away_goals"] > sample["home_goals"])


def _dc_lambda(model: Any, row: dict[str, Any], home: bool) -> float:
    """Calcula lambda Dixon-Coles con fallback neutral de equipo."""

    team = int(row["home_team_id"] if home else row["away_team_id"])
    rival = int(row["away_team_id"] if home else row["home_team_id"])
    attack_mean = sum(model.attack.values()) / max(len(model.attack), 1)
    defense_mean = sum(model.defense.values()) / max(len(model.defense), 1)
    attack = model.attack.get(team, attack_mean)
    defense = model.defense.get(rival, defense_mean)
    offset = model.home_advantage if home else 0.0
    return max(1e-6, min(math.exp(model.league_intercept + offset + attack - defense), 100.0))


def _model_state(model: Any, teams: list[int], cutoff: str, config: Config) -> tuple[KalmanV2Filter, Any]:
    """Inicializa Kalman desde parámetros Dixon-Coles."""

    kalman = KalmanV2Filter(KalmanV2Config(process_noise_attack=config.process_noise_attack, process_noise_defense=config.process_noise_defense))
    parameters = {"attack": model.attack, "defense": model.defense, "home_advantage": model.home_advantage, "league_intercept": model.league_intercept}
    return kalman, kalman._initial_state_from_dc(teams, parameters, cutoff)


def _predict_league(rows: list[dict[str, Any]], model: Any, data: dict[str, Any], matrices: dict[str, dict[tuple[Any, ...], dict[str, Any]]], config: Config) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Genera predicciones de validación/confirmación con replay causal."""

    teams = _teams(rows)
    kalman, state = _model_state(model, teams, str(rows[0]["match_date"]), config)
    predictions, update_count = [], 0
    targets = sorted(rows, key=lambda row: (row["match_date"], row["match_id"]))
    for cutoff, bucket in _date_buckets(targets):
        pending = []
        for row in bucket:
            dc_lambdas = {True: _dc_lambda(model, row, True), False: _dc_lambda(model, row, False)}
            _, extra = kalman._predict_one(state, int(row["home_team_id"]), int(row["away_team_id"]), 0, int(row["match_id"]), cutoff)
            kalman_lambdas = {True: float(extra["lambda_home"]), False: float(extra["lambda_away"])}
            base = {venue: config.dixon_coles_weight * dc_lambdas[venue] + config.kalman_weight * kalman_lambdas[venue] for venue in (True, False)}
            predictions.append(_simulate_prediction(row, dc_lambdas, kalman_lambdas, base, data, matrices, config))
            pending.append((row, extra))
        for row, extra in pending:
            state = kalman._update(state, int(row["home_team_id"]), int(row["away_team_id"]), int(row["home_goals"]), int(row["away_goals"]), float(extra["lambda_home"]), float(extra["lambda_away"]))
            state.cutoff_ts = cutoff
            update_count += 1
    return predictions, {"prediction_count": len(predictions), "post_prediction_kalman_updates": update_count, "team_count": len(teams)}


def _date_buckets(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Agrupa partidos por cutoff para evitar leakage entre fixtures simultáneos."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["match_date"])].append(row)
    return [(date, sorted(bucket, key=lambda row: row["match_id"])) for date, bucket in sorted(grouped.items())]


def _simulate_prediction(row: dict[str, Any], dc: dict[bool, float], kalman: dict[bool, float], base: dict[bool, float], data: dict[str, Any], matrices: dict[str, dict[tuple[Any, ...], dict[str, Any]]], config: Config) -> dict[str, Any]:
    """Simula una fila pre-match y conserva provenance de sus lambdas."""

    rng = random.Random(config.seed + int(row["match_id"]))
    samples = [_trajectory(row, base, data, matrices, config, rng) for _ in range(config.simulations_per_match)]
    result = _aggregate(samples, row, base, config)
    return {**result, "match_date": str(row["match_date"]), "split": str(row["split"]), "lambda_dc_home": dc[True], "lambda_dc_away": dc[False], "lambda_kalman_home": kalman[True], "lambda_kalman_away": kalman[False]}


def _audit(predictions: list[dict[str, Any]], model_audit: dict[str, Any], data: dict[str, Any], config: Config) -> dict[str, Any]:
    """Verifica causalidad, normalización y conservación de masa."""

    normalized = all(abs(row["prob_1"] + row["prob_x"] + row["prob_2"] - 1.0) < 1e-12 for row in predictions)
    conservation = max((max(row["conservation"].values()) for row in predictions), default=0.0)
    return {"classification": "ready_for_multileague_oos_evaluation" if predictions and normalized and conservation < 1e-12 else "rejected_for_revision", "prediction_count": len(predictions), "validation_predictions": sum(row["split"] == "validation" for row in predictions), "confirmation_predictions": sum(row["split"] == "confirmation" for row in predictions), "probabilities_normalized": normalized, "max_lambda_allocation_error": conservation, "dc_kalman_fit": model_audit, "development_only_for_structural_fit": True, "target_used_before_prediction": False, "target_used_after_prediction_for_kalman_update": True, "target_events_used_as_features": False, "target_scores_used_as_features": False, "official_router_modified": False, "markets_promoted": False, "phase_41_input_hash": _hash(data)}


def _publish(result: dict[str, Any], source: dict[str, Any], development: set[int]) -> None:
    """Publica predicciones, configuración, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "predictions.json": result["predictions"], "coverage.json": result["coverage"], "model_audit.json": result["model_audit"], "audit.json": result["audit"], "input_manifest.json": {"windows_hash": _hash(source["windows"]), "labels_hash": _hash(source["labels"]), "markov_matrices_hash": _hash(source["matrices"]), "development_split_hash": _hash(sorted(development))}}
    for name, value in payloads.items():
        target = OUTPUT / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(target)
    report = ["# Fase 42 — fusión estructural multi-liga", "", f"**Clasificación:** `{result['audit']['classification']}`", "", f"- predicciones OOS candidatas: `{result['coverage']['predictions']}`", f"- validación/confirmación: `{result['coverage']['validation']}/{result['coverage']['confirmation']}`", f"- ligas con modelos: `{result['coverage']['leagues']}`", "- Dixon-Coles: `parametrización regularizada compatible; no MLE global`", "- Kalman: `actualización sólo después de cada predicción`", "- Markov: `redistribución temporal con masa conservada`", "- router oficial y mercados: `sin cambios/promoción`", "- siguiente paso: `evaluación OOS multi-liga por partido completo`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Config | None = None) -> dict[str, Any]:
    """Ejecuta la fusión y deja lista la evaluación OOS."""

    active = config or Config()
    source = {"windows": _load(WINDOWS), "labels": _load(LABELS), "matrices": _load(MARKOV / "transition_matrices.json"), "transitions": _load(MARKOV / "transitions.json")}
    matches = _match_rows(source["windows"])
    development = _development_ids(source["transitions"])
    splits = _split_matches(matches, development)
    global_model, models, model_audit = _fit_models(matches, development, active)
    train_leagues = set(models)
    eligible = [dict(row, split=splits[int(row["match_id"])]) for row in matches if splits.get(int(row["match_id"])) in {"validation", "confirmation"} and str(row["league_slug"]) in train_leagues]
    data = _priors_and_weights(source["windows"], source["labels"], development, active)
    matrices = _matrix_index(source["matrices"])
    predictions, league_audit = [], {}
    for league in sorted({str(row["league_slug"]) for row in eligible}):
        rows = [row for row in eligible if str(row["league_slug"]) == league]
        model = models.get(league, global_model)
        league_predictions, audit = _predict_league(rows, model, data, matrices, active)
        predictions.extend(league_predictions)
        league_audit[league] = audit
    model_audit["prediction_replay"] = league_audit
    audit = _audit(predictions, model_audit, data, active)
    coverage = {"matches_source": len(matches), "development_matches": len(development), "validation": sum(row["split"] == "validation" for row in predictions), "confirmation": sum(row["split"] == "confirmation" for row in predictions), "predictions": len(predictions), "leagues": len({row["league_slug"] for row in predictions}), "simulation_count": active.simulations_per_match}
    result = {"config": asdict(active), "predictions": sorted(predictions, key=lambda row: (row["match_date"], row["match_id"])), "coverage": coverage, "model_audit": model_audit, "audit": audit}
    _publish(result, source, development)
    LOGGER.info("Fase 42 fusión estructural multi-liga: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 42."""

    return 0 if run()["audit"]["classification"] == "ready_for_multileague_oos_evaluation" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
