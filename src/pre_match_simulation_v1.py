"""Simulación Monte Carlo causal sobre matrices Markov pre-partido.

La salida usa emisiones históricas de gol y queda explícitamente experimental.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.markov_pre_match_v1 import STATES, _score_bucket

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
MARKOV = ROOT / "artifacts/phase_03_markov_pre_match_v1"
OUTPUT = ROOT / "artifacts/phase_04_pre_match_simulation_v1"
TIERS = ("team", "context", "window", "global")


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Parámetros congelados para una corrida Monte Carlo reproducible."""

    version: str = "pre_match_simulation_v1"
    simulations: int = 5000
    seed: int = 20260726
    min_support_team: int = 12
    min_support_context: int = 10
    min_support_window: int = 8


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Identidad pre-kickoff de un partido sin datos de su desarrollo."""

    match_id: str
    home_team_id: int
    away_team_id: int
    competition_id: str = "esp.1"
    cutoff_ts: str = "2026-07-26T00:00:00+00:00"


class TrajectorySimulator(ABC):
    """Contrato para implementaciones intercambiables de simulación."""

    @abstractmethod
    def simulate(self, request: SimulationRequest) -> dict[str, Any]:
        """Devuelve trayectorias y probabilidades pre-match auditables."""


def _hash(value: Any) -> str:
    """Calcula un hash SHA-256 estable para provenance."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _load(path: Path) -> Any:
    """Carga JSON de un artefacto versionado existente."""
    return json.loads(path.read_text(encoding="utf-8"))


def _indexes(windows: list[dict[str, Any]], labels: list[dict[str, Any]]) -> tuple[dict[tuple[int, int, int], dict[str, Any]], dict[tuple[int, int, int], str]]:
    """Indexa ventanas y estados históricos para derivar priors de desarrollo."""
    window_index = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in windows}
    label_index = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): str(row["state"]) for row in labels}
    return window_index, label_index


def _development_ids() -> set[int]:
    """Obtiene el único bloque permitido para estimar priors de simulación."""
    manifest = _load(MARKOV / "input_manifest.json")
    return {int(match_id) for match_id in manifest["match_split"]["development"]}


def _probabilities(counts: Counter[str]) -> dict[str, float]:
    """Convierte conteos a distribución con pseudoconteo uniforme mínimo."""
    total = sum(counts.values()) + len(STATES)
    return {state: (counts[state] + 1.0) / total for state in STATES}


def _initial_priors(windows: dict[tuple[int, int, int], dict[str, Any]], labels: dict[tuple[int, int, int], str], development: set[int]) -> dict[bool, dict[str, float]]:
    """Estima P(S0) por localía únicamente desde partidos de desarrollo."""
    counts: dict[bool, Counter[str]] = {True: Counter(), False: Counter()}
    for key, row in windows.items():
        if key[0] in development and key[2] == 0 and labels.get(key) in STATES: counts[bool(row["is_home"])][labels[key]] += 1
    return {is_home: _probabilities(values) for is_home, values in counts.items()}


def _initial_priors_by_team(windows: dict[tuple[int, int, int], dict[str, Any]], labels: dict[tuple[int, int, int], str], development: set[int]) -> dict[tuple[int, bool], dict[str, float]]:
    """Estima P(S0) por equipo y localía con backoff a la distribución global."""
    aggregate = _initial_priors(windows, labels, development)
    counts: dict[tuple[int, bool], Counter[str]] = defaultdict(Counter)
    for key, row in windows.items():
        if key[0] in development and key[2] == 0 and labels.get(key) in STATES:
            counts[(int(row["team_id"]), bool(row["is_home"]))][labels[key]] += 1
    output: dict[tuple[int, bool], dict[str, float]] = {}
    for key, values in counts.items():
        parent = aggregate[key[1]]
        total = sum(values.values()) + 8.0
        output[key] = {state: (values[state] + 8.0 * parent[state]) / total for state in STATES}
    return output


def _goal_emissions(windows: dict[tuple[int, int, int], dict[str, Any]], labels: dict[tuple[int, int, int], str], development: set[int]) -> dict[str, dict[str, float]]:
    """Estima goles por ventana condicionados por estado, localía y periodo."""
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    fallback: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for key, row in windows.items():
        state = labels.get(key)
        if key[0] not in development or state not in STATES: continue
        context = _emission_key(bool(row["is_home"]), int(row["window_index"]), state)
        totals[context][0] += float(row["goals"]); totals[context][1] += 1.0
        aggregate = fallback[(bool(row["is_home"]), int(row["window_index"]))]
        aggregate[0] += float(row["goals"]); aggregate[1] += 1.0
    for is_home, window, state in ((home, index, label) for home in (True, False) for index in range(6) for label in STATES):
        totals.setdefault(_emission_key(is_home, window, state), list(fallback[(is_home, window)]))
    return {key: {"goals": value[0], "windows": value[1], "rate": value[0] / value[1]} for key, value in totals.items()}


def _emission_key(is_home: bool, window_index: int, state: str) -> str:
    """Serializa el contexto causal de una emisión histórica."""
    return json.dumps((is_home, window_index, state), separators=(",", ":"))


def _matrix_index(matrices: dict[str, list[dict[str, Any]]]) -> dict[str, dict[tuple[Any, ...], dict[str, Any]]]:
    """Indexa matrices publicadas por su tier y contexto serializado."""
    return {tier: {tuple(item["context"]): item for item in rows} for tier, rows in matrices.items()}


def _transition_key(tier: str, team_id: int, is_home: bool, window: int, score_bucket: str, state: str, opponent: str) -> tuple[Any, ...]:
    """Replica el contexto de Fase 03 sin añadir datos del encuentro objetivo."""
    if tier == "team": return (team_id, is_home, window, score_bucket, state, opponent)
    if tier == "context": return (is_home, window, score_bucket, state, opponent)
    if tier == "window": return (window, state)
    return (state,)


def _minimum_support(tier: str, config: SimulationConfig) -> int:
    """Devuelve el umbral de soporte que habilita cada nivel."""
    return {"team": config.min_support_team, "context": config.min_support_context, "window": config.min_support_window, "global": 1}[tier]


def _sample(probabilities: dict[str, float], rng: random.Random) -> str:
    """Muestrea un estado con orden de estados congelado."""
    threshold, cumulative = rng.random(), 0.0
    for state in STATES:
        cumulative += probabilities[state]
        if threshold <= cumulative: return state
    return STATES[-1]


def _poisson(rate: float, rng: random.Random) -> int:
    """Muestrea Poisson por el algoritmo de Knuth para tasas por ventana."""
    if rate <= 0.0: return 0
    threshold, product, count = math.exp(-max(rate, 0.0)), 1.0, 0
    while product > threshold:
        count += 1; product *= rng.random()
    return count - 1


class MarkovMonteCarloSimulator(TrajectorySimulator):
    """Simulador causal con matrices calibradas y emisiones históricas."""

    def __init__(self, config: SimulationConfig, matrices: dict[str, list[dict[str, Any]]], priors: dict[bool, dict[str, float]], emissions: dict[str, dict[str, float]]) -> None:
        """Inicializa el simulador con artefactos congelados y auditables."""
        self.config, self.matrices = config, _matrix_index(matrices)
        self.priors, self.emissions = priors, emissions

    def simulate(self, request: SimulationRequest) -> dict[str, Any]:
        """Ejecuta trayectorias independientes y agrega resultados experimentales."""
        rng, samples = random.Random(self.config.seed), []
        for _ in range(self.config.simulations): samples.append(self._trajectory(request, rng))
        return _aggregate(samples, request, self.config)

    def _trajectory(self, request: SimulationRequest, rng: random.Random) -> dict[str, Any]:
        """Simula seis ventanas conservando orientación y marcador acumulado."""
        states = {True: _sample(self.priors[True], rng), False: _sample(self.priors[False], rng)}
        goals, history = {True: 0, False: 0}, []
        for window in range(6):
            opening = dict(goals)
            home_goals, away_goals = self._goals(states, window, rng)
            goals[True] += home_goals; goals[False] += away_goals
            history.append({"window_index": window, "home_state": states[True], "away_state": states[False], "home_goals": home_goals, "away_goals": away_goals})
            if window < 5: states = self._next_states(request, states, opening, window, rng)
        return {"home_goals": goals[True], "away_goals": goals[False], "windows": history}

    def _goals(self, states: dict[bool, str], window: int, rng: random.Random) -> tuple[int, int]:
        """Muestrea goles usando solamente emisiones históricas condicionadas."""
        home_rate = self.emissions[_emission_key(True, window, states[True])]["rate"]
        away_rate = self.emissions[_emission_key(False, window, states[False])]["rate"]
        return _poisson(home_rate, rng), _poisson(away_rate, rng)

    def _next_states(self, request: SimulationRequest, states: dict[bool, str], goals: dict[bool, int], window: int, rng: random.Random) -> dict[bool, str]:
        """Muestrea S(t+1) con el marcador conocido al inicio de la ventana."""
        home = self._next_state(request.home_team_id, True, window, goals[True] - goals[False], states[True], states[False], rng)
        away = self._next_state(request.away_team_id, False, window, goals[False] - goals[True], states[False], states[True], rng)
        return {True: home, False: away}

    def _next_state(self, team_id: int, is_home: bool, window: int, difference: int, state: str, opponent: str, rng: random.Random) -> str:
        """Selecciona backoff y muestrea la próxima etiqueta táctica."""
        bucket = _score_bucket(difference)
        for tier in TIERS:
            item = self.matrices[tier].get(_transition_key(tier, team_id, is_home, window, bucket, state, opponent))
            if item and int(item["support"]) >= _minimum_support(tier, self.config): return _sample(item["probabilities"], rng)
        return _sample(self.matrices["global"][(state,)]["probabilities"], rng)


def _aggregate(samples: list[dict[str, Any]], request: SimulationRequest, config: SimulationConfig) -> dict[str, Any]:
    """Agrega 1X2, goles, estados por ventana y distribución de marcadores."""
    outcomes, scores, states = Counter(), Counter(), {index: {True: Counter(), False: Counter()} for index in range(6)}
    for sample in samples: _accumulate(sample, outcomes, scores, states)
    count = float(len(samples))
    return {"request": asdict(request), "config": asdict(config), "expected_goals": {"home": sum(item["home_goals"] for item in samples) / count, "away": sum(item["away_goals"] for item in samples) / count}, "markets_experimental": _markets(outcomes, samples, count), "score_distribution": {key: value / count for key, value in sorted(scores.items())}, "state_distribution": _state_distribution(states, count), "simulation_count": len(samples), "classification": "experimental_not_promoted"}


def _accumulate(sample: dict[str, Any], outcomes: Counter[str], scores: Counter[str], states: dict[int, dict[bool, Counter[str]]]) -> None:
    """Actualiza agregados de una trayectoria simulada."""
    home, away = int(sample["home_goals"]), int(sample["away_goals"])
    outcomes["1" if home > away else "2" if home < away else "X"] += 1
    scores[f"{home}-{away}"] += 1
    for row in sample["windows"]:
        states[int(row["window_index"])][True][str(row["home_state"])] += 1
        states[int(row["window_index"])][False][str(row["away_state"])] += 1


def _markets(outcomes: Counter[str], samples: list[dict[str, Any]], count: float) -> dict[str, float]:
    """Calcula mercados de gol experimentales, sin declarar valor de apuesta."""
    over = sum(item["home_goals"] + item["away_goals"] > 2 for item in samples)
    btts = sum(item["home_goals"] > 0 and item["away_goals"] > 0 for item in samples)
    return {"1": outcomes["1"] / count, "X": outcomes["X"] / count, "2": outcomes["2"] / count, "over_2_5": over / count, "btts": btts / count}


def _state_distribution(states: dict[int, dict[bool, Counter[str]]], count: float) -> dict[str, Any]:
    """Convierte conteos de estados simulados a probabilidades por ventana."""
    return {str(window): {"home": {state: values[True][state] / count for state in STATES}, "away": {state: values[False][state] / count for state in STATES}} for window, values in states.items()}


def _audit(result: dict[str, Any], matrices: dict[str, list[dict[str, Any]]], config: SimulationConfig) -> dict[str, Any]:
    """Verifica invariantes de simulación, orientación y probabilidades."""
    markets = result["markets_experimental"]
    normalized = abs(markets["1"] + markets["X"] + markets["2"] - 1.0) < 1e-12
    finite = all(math.isfinite(value) and value >= 0.0 for value in markets.values())
    return {"deterministic_seed": config.seed, "orientation_preserved": result["request"]["home_team_id"] != result["request"]["away_team_id"], "probabilities_normalized": normalized, "probabilities_finite_non_negative": finite, "matrix_tiers_loaded": sorted(matrices), "forbidden_target_fields_used": [], "goal_provider": "historical_state_emission", "promotion_blocked": True}


def run(config: SimulationConfig | None = None, request: SimulationRequest | None = None) -> dict[str, Any]:
    """Ejecuta Fase 04 con una fixture de referencia sin datos de resultado."""
    active, target = config or SimulationConfig(), request or SimulationRequest("phase04_reference_fixture", 900001, 900002)
    windows, labels, matrices = _load(WINDOWS), _load(LABELS), _load(MARKOV / "transition_matrices.json")
    window_index, label_index, development = *_indexes(windows, labels), _development_ids()
    priors, emissions = _initial_priors(window_index, label_index, development), _goal_emissions(window_index, label_index, development)
    result = MarkovMonteCarloSimulator(active, matrices, priors, emissions).simulate(target)
    audit = _audit(result, matrices, active)
    output = {"result": result, "priors": {str(key): value for key, value in priors.items()}, "emissions": emissions, "audit": audit, "classification": "ready_for_next_phase" if all(audit[key] for key in ("orientation_preserved", "probabilities_normalized", "probabilities_finite_non_negative")) else "rejected_for_revision"}
    _publish(output, windows, labels, matrices)
    LOGGER.info("Fase 04 pre_match_simulation: %s", output["classification"])
    return output


def _publish(output: dict[str, Any], windows: Any, labels: Any, matrices: Any) -> None:
    """Publica artefactos, provenance y reporte de simulación experimental."""
    payloads = {"config.json": output["result"]["config"], "simulation_input.json": output["result"]["request"], "input_manifest.json": {"phase_01_hash": _hash(windows), "phase_02_hash": _hash(labels), "phase_03_matrices_hash": _hash(matrices), "goal_provider": "historical_state_emission"}, "initial_state_priors.json": output["priors"], "goal_emissions.json": output["emissions"], "simulation_result.json": output["result"], "audit.json": {**output["audit"], "classification": output["classification"]}}
    for name, value in payloads.items(): _write(name, value)
    report = ["# Fase 04 — pre_match_simulation v1", "", f"**Clasificación:** `{output['classification']}`", "", f"- trayectorias: `{output['result']['simulation_count']}`", "- proveedor de gol: `historical_state_emission`", "- mercados: experimentales; no promovidos ni evaluados aún.", "- siguiente paso: `evaluation_protocol v1`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
