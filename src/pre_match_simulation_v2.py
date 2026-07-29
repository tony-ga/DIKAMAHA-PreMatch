"""Simulación Markov v2 con intensidad estructural conservada por partido.

Version: 1.0.0
Created: 2026-07-26
"""
# Temporal residual outputs added for first-half, second-half and comeback markets.
from __future__ import annotations

import hashlib
import json
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.pre_match_simulation_v1 import (
    MARKOV, STATES, _emission_key, _goal_emissions, _indexes, _initial_priors,
    _initial_priors_by_team,
    _load, _poisson, _sample, MarkovMonteCarloSimulator, SimulationConfig,
    SimulationRequest,
)

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
PRIORS = ROOT / "artifacts/phase_06_markov_v2_goal_prior/goal_priors.json"
OUTPUT = ROOT / "artifacts/phase_06_markov_v2_simulation"


@dataclass(frozen=True, slots=True)
class StructuralSimulationConfig:
    """Configuración reproducible del simulador estructural Markov v2."""

    version: str = "pre_match_simulation_v2"
    simulations: int = 5000
    seed: int = 20260726


class StructuralTrajectorySimulator(ABC):
    """Contrato para simuladores que conservan intensidad por partido."""

    @abstractmethod
    def simulate(self, prior: dict[str, Any]) -> dict[str, Any]:
        """Simula estados y goles con masa estructural conservada."""


def _write(name: str, value: Any) -> None:
    """Escribe JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _development_ids() -> set[int]:
    """Recupera el bloque de desarrollo de Fase 03 para emisiones causales."""
    manifest = _load(MARKOV / "input_manifest.json")
    return {int(value) for value in manifest["match_split"]["development"]}


class ConservingMarkovSimulator(StructuralTrajectorySimulator):
    """Asigna toda la intensidad base entre ventanas según estados simulados."""

    def __init__(self, config: StructuralSimulationConfig, matrices: dict[str, Any], priors: dict[bool, Any], emissions: dict[str, Any], team_priors: dict[tuple[int, bool], dict[str, float]] | None = None) -> None:
        """Inicializa matrices, distribución inicial y pesos históricos congelados."""
        self.config, self.priors, self.emissions = config, priors, emissions
        self.team_priors = team_priors or {}
        self.transitions = MarkovMonteCarloSimulator(SimulationConfig(simulations=1, seed=config.seed), matrices, priors, emissions)
        self.mean_weight = sum(value["rate"] for value in emissions.values()) / len(emissions)

    def simulate(self, prior: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta trayectorias y conserva lambda base en cada una de ellas."""
        rng, samples = random.Random(self.config.seed + int(prior["match_id"])), []
        for _ in range(self.config.simulations): samples.append(self._trajectory(prior, rng))
        return _aggregate(samples, prior, self.config)

    def _trajectory(self, prior: dict[str, Any], rng: random.Random) -> dict[str, Any]:
        """Simula seis ventanas actualizando score y masa restante por equipo."""
        request = SimulationRequest(str(prior["match_id"]), int(prior["home_team_id"]), int(prior["away_team_id"]), cutoff_ts=str(prior["cutoff_ts"]))
        states = {True: _sample(self.team_priors.get((int(prior["home_team_id"]), True), self.priors[True]), rng), False: _sample(self.team_priors.get((int(prior["away_team_id"]), False), self.priors[False]), rng)}
        goals, remaining, windows = {True: 0, False: 0}, {True: float(prior["lambda_base_home"]), False: float(prior["lambda_base_away"])}, []
        for index in range(6):
            opening, rates = dict(goals), self._rates(states, remaining, index)
            scored = {side: _poisson(rates[side], rng) for side in (True, False)}
            for side in goals: goals[side] += scored[side]; remaining[side] -= rates[side]
            windows.append({"window_index": index, "home_state": states[True], "away_state": states[False], "home_lambda": rates[True], "away_lambda": rates[False], "home_goals": scored[True], "away_goals": scored[False]})
            if index < 5: states = self.transitions._next_states(request, states, opening, index, rng)
        return {"home_goals": goals[True], "away_goals": goals[False], "windows": windows, "remaining": remaining}

    def _rates(self, states: dict[bool, str], remaining: dict[bool, float], index: int) -> dict[bool, float]:
        """Asigna masa restante con peso presente y expectativa futura congelada."""
        future_count = 5 - index
        rates = {side: self._rate(side, states[side], remaining[side], index, future_count) for side in (True, False)}
        return rates

    def _rate(self, is_home: bool, state: str, remaining: float, index: int, future_count: int) -> float:
        """Reserva intensidad futura y garantiza asignación total en ventana final."""
        if index == 5: return remaining
        weight = self.emissions[_emission_key(is_home, index, state)]["rate"]
        return remaining * weight / (weight + future_count * self.mean_weight) if weight > 0.0 else 0.0


def _aggregate(samples: list[dict[str, Any]], prior: dict[str, Any], config: StructuralSimulationConfig) -> dict[str, Any]:
    """Resume mercados y conservación de intensidad sobre todas las trayectorias."""
    total = float(len(samples)); home = sum(row["home_goals"] for row in samples); away = sum(row["away_goals"] for row in samples)
    outcomes = {"1": sum(row["home_goals"] > row["away_goals"] for row in samples) / total, "X": sum(row["home_goals"] == row["away_goals"] for row in samples) / total, "2": sum(row["home_goals"] < row["away_goals"] for row in samples) / total}
    markets = {"over_2_5": sum(row["home_goals"] + row["away_goals"] > 2 for row in samples) / total, "btts": sum(row["home_goals"] > 0 and row["away_goals"] > 0 for row in samples) / total}
    temporal = {"first_half_goal": sum(any(item["home_goals"] + item["away_goals"] > 0 for item in row["windows"][:3]) for row in samples) / total, "second_half_goal": sum(any(item["home_goals"] + item["away_goals"] > 0 for item in row["windows"][3:]) for row in samples) / total, "home_comeback": sum(_comeback(row, True) for row in samples) / total, "away_comeback": sum(_comeback(row, False) for row in samples) / total, "home_recovery_draw_or_win": sum(_recovery_draw_or_win(row, True) for row in samples) / total, "away_recovery_draw_or_win": sum(_recovery_draw_or_win(row, False) for row in samples) / total, "home_reaches_level_after_half": sum(_reaches_level_after_half(row, True) for row in samples) / total, "away_reaches_level_after_half": sum(_reaches_level_after_half(row, False) for row in samples) / total}
    conservation = {"home_max_abs_remaining": max(abs(row["remaining"][True]) for row in samples), "away_max_abs_remaining": max(abs(row["remaining"][False]) for row in samples)}
    return {"prior": prior, "config": asdict(config), "expected_goals": {"home": home / total, "away": away / total}, "prob_1x2": outcomes, "markets_experimental": {**markets, **temporal}, "simulation_count": len(samples), "conservation": conservation, "classification": "experimental_not_promoted"}


def _comeback(sample: dict[str, Any], home: bool) -> bool:
    """Identifica una remontada completa desde desventaja al descanso."""
    first_half = sample["windows"][:3]
    home_half = sum(row["home_goals"] for row in first_half)
    away_half = sum(row["away_goals"] for row in first_half)
    final_home, final_away = sample["home_goals"], sample["away_goals"]
    return (home_half < away_half and final_home > final_away) if home else (away_half < home_half and final_away > final_home)


def _recovery_draw_or_win(sample: dict[str, Any], home: bool) -> bool:
    """Identifica recuperación desde desventaja hasta empate o victoria."""
    first_half = sample["windows"][:3]
    home_half = sum(row["home_goals"] for row in first_half)
    away_half = sum(row["away_goals"] for row in first_half)
    trailing = home_half < away_half if home else away_half < home_half
    final_home, final_away = sample["home_goals"], sample["away_goals"]
    recovered = final_home >= final_away if home else final_away >= final_home
    return trailing and recovered


def _reaches_level_after_half(sample: dict[str, Any], home: bool) -> bool:
    """Comprueba si el equipo iguala al rival en una ventana posterior."""
    first_half = sample["windows"][:3]
    own = sum(row["home_goals"] for row in first_half) if home else sum(row["away_goals"] for row in first_half)
    rival = sum(row["away_goals"] for row in first_half) if home else sum(row["home_goals"] for row in first_half)
    if own >= rival:
        return False
    for window in sample["windows"][3:]:
        own += window["home_goals"] if home else window["away_goals"]
        rival += window["away_goals"] if home else window["home_goals"]
        if own >= rival:
            return True
    return False


def run(config: StructuralSimulationConfig | None = None) -> dict[str, Any]:
    """Publica una simulación de referencia v2 sin evaluar ni promover mercados."""
    active, windows, labels = config or StructuralSimulationConfig(), _load(WINDOWS), _load(LABELS)
    development = _development_ids(); window_index, label_index = _indexes(windows, labels)
    matrices = _load(MARKOV / "transition_matrices.json")
    simulator = ConservingMarkovSimulator(active, matrices, _initial_priors(window_index, label_index, development), _goal_emissions(window_index, label_index, development), _initial_priors_by_team(window_index, label_index, development))
    result = simulator.simulate(_load(PRIORS)[0])
    audit = {"all_intensity_conserved": max(result["conservation"].values()) < 1e-12, "probabilities_normalized": abs(sum(result["prob_1x2"].values()) - 1.0) < 1e-12, "target_outcomes_used_as_features": False, "promotion_blocked": True}
    _publish(result, audit)
    LOGGER.info("Simulación Markov v2: %s", result["classification"])
    return {"result": result, "audit": audit}


def _publish(result: dict[str, Any], audit: dict[str, Any]) -> None:
    """Publica resultado, auditoría, provenance y hashes del experimento v2."""
    _write("simulation_result.json", result); _write("audit.json", audit)
    _write("config.json", result["config"]); _write("input_manifest.json", {"goal_priors_hash": hashlib.sha256(PRIORS.read_bytes()).hexdigest(), "matrices_hash": hashlib.sha256((MARKOV / "transition_matrices.json").read_bytes()).hexdigest()})
    (OUTPUT / "final_report.md").write_text("# Fase 06 — simulación Markov v2\n\n**Clasificación:** `experimental_not_promoted`\n\n- intensidad estructural conservada por trayectoria.\n- pendiente: generar predicciones OOS v2 y evaluación independiente.\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
