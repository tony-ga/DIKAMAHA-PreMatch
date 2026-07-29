"""Simulador pre-match dual que conserva intensidad estructural.

Requirements:
    numpy>=2.0

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

REGIMES = 3
STATES = 6
WINDOWS = 18


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Entradas congeladas antes del kickoff."""

    match_id: int
    league_slug: str
    home_team_id: int
    away_team_id: int
    cutoff_utc: str
    lambda_home: float
    lambda_away: float
    initial_home: tuple[float, ...]
    initial_away: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Configuración reproducible de Monte Carlo."""

    simulations: int = 5_000
    seed: int = 79
    windows: int = WINDOWS


class TransitionKernel(ABC):
    """Puerto de transición independiente de la persistencia."""

    @abstractmethod
    def probability(
        self, league: str, is_home: bool, window: int,
        regime: int, opponent_regime: int,
    ) -> np.ndarray:
        """Obtiene una distribución normalizada de régimen siguiente."""


class HierarchicalTransitionKernel(TransitionKernel):
    """Kernel rival→liga→global reconstruido desde conteos serializados."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Carga parámetros auditados de Fase 78."""

        self.alpha = float(payload["alpha"])
        self.global_counts = self._index(payload["global"])
        self.baseline_counts = self._index(payload["baseline"])
        self.context_counts = self._index(payload["context"])

    @classmethod
    def from_path(cls, path: Path) -> "HierarchicalTransitionKernel":
        """Construye el kernel desde un artefacto JSON."""

        return cls(json.loads(path.read_text(encoding="utf-8")))

    def probability(
        self, league: str, is_home: bool, window: int,
        regime: int, opponent_regime: int,
    ) -> np.ndarray:
        """Aplica backoff completo aun para ligas desconocidas."""

        uniform = np.full(REGIMES, 1.0 / REGIMES)
        global_p = self._pool(
            self.global_counts.get((window, regime)), uniform, 20.0)
        baseline = self._pool(
            self.baseline_counts.get((league, window, regime)),
            global_p, 20.0)
        key = (league, is_home, window, regime, opponent_regime)
        return self._pool(self.context_counts.get(key), baseline, self.alpha)

    @staticmethod
    def _index(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], np.ndarray]:
        """Indexa contextos preservando booleanos y cadenas."""

        return {tuple(row["context"]): np.asarray(row["counts"], dtype=float)
                for row in rows}

    @staticmethod
    def _pool(
        counts: np.ndarray | None, parent: np.ndarray, alpha: float,
    ) -> np.ndarray:
        """Suaviza conteos hacia una distribución padre."""

        observed = np.zeros(REGIMES) if counts is None else counts
        return (observed + alpha * parent) / (observed.sum() + alpha)


class DualMarkovSimulator:
    """Simula estados conjuntos y reasigna lambdas sin modificarlas."""

    def __init__(
        self, kernel: TransitionKernel, risk_weights: Sequence[float],
        config: SimulationConfig | None = None,
    ) -> None:
        """Valida dependencias y fija la configuración."""

        self.kernel = kernel
        self.risk = np.asarray(risk_weights, dtype=float)
        self.config = config or SimulationConfig()
        if self.risk.shape != (STATES,) or np.any(self.risk <= 0):
            raise ValueError("risk_weights_must_be_six_positive_values")
        if self.config.windows != WINDOWS:
            raise ValueError("phase_79_requires_eighteen_windows")

    def simulate(self, request: SimulationRequest) -> dict[str, Any]:
        """Genera trayectorias, mercados y auditoría causal."""

        self._validate(request)
        rng = np.random.default_rng(self.config.seed)
        home_states, away_states = self._states(request, rng)
        home_rates = self._allocate(request.lambda_home, home_states)
        away_rates = self._allocate(request.lambda_away, away_states)
        home_goals = rng.poisson(home_rates)
        away_goals = rng.poisson(away_rates)
        result = self._result(request, home_states, away_states,
                              home_rates, away_rates, home_goals, away_goals)
        result["prediction_hash"] = self._hash(result)
        return result

    def _states(
        self, request: SimulationRequest, rng: np.random.Generator,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Muestrea estados conjuntos usando siempre el estado previo."""

        shape = (self.config.simulations, WINDOWS)
        home, away = np.zeros(shape, dtype=np.int8), np.zeros(shape, dtype=np.int8)
        home[:, 0] = rng.choice(STATES, shape[0], p=request.initial_home)
        away[:, 0] = rng.choice(STATES, shape[0], p=request.initial_away)
        for window in range(WINDOWS - 1):
            home[:, window + 1] = self._next(
                request.league_slug, True, window, home[:, window],
                away[:, window], rng)
            away[:, window + 1] = self._next(
                request.league_slug, False, window, away[:, window],
                home[:, window], rng)
        return home, away

    def _next(
        self, league: str, is_home: bool, window: int,
        states: np.ndarray, opponents: np.ndarray, rng: np.random.Generator,
    ) -> np.ndarray:
        """Muestrea por contexto conservando el estilo inicial."""

        output = np.empty_like(states)
        for state in range(STATES):
            for opponent in range(STATES):
                mask = (states == state) & (opponents == opponent)
                count = int(mask.sum())
                if not count:
                    continue
                probability = self.kernel.probability(
                    league, is_home, window, state % 3, opponent % 3)
                regimes = rng.choice(REGIMES, count, p=probability)
                output[mask] = (state // 3) * REGIMES + regimes
        return output

    def _allocate(self, intensity: float, states: np.ndarray) -> np.ndarray:
        """Normaliza el riesgo temporal a la lambda de cada trayectoria."""

        raw = self.risk[states]
        return intensity * raw / raw.sum(axis=1, keepdims=True)

    def _result(
        self, request: SimulationRequest, home_states: np.ndarray,
        away_states: np.ndarray, home_rates: np.ndarray,
        away_rates: np.ndarray, home_goals: np.ndarray,
        away_goals: np.ndarray,
    ) -> dict[str, Any]:
        """Compone salida compacta y trazable."""

        total_home = home_goals.sum(axis=1)
        total_away = away_goals.sum(axis=1)
        markets = self._markets(home_goals, away_goals)
        return {
            "model": "dual_markov_prematch_v4",
            "request": self._request_dict(request),
            "simulation_count": self.config.simulations,
            "seed": self.config.seed,
            "markets": markets,
            "expected_goals_mc": {
                "home": float(total_home.mean()),
                "away": float(total_away.mean())},
            "window_15m": self._window_markets(home_goals, away_goals),
            "trajectory_markets": self._trajectory_markets(
                home_goals, away_goals),
            "classification": "experimental_shadow_not_promoted",
            "audit": self._audit(
                request, home_states, away_states, home_rates, away_rates),
        }

    @staticmethod
    def _markets(home: np.ndarray, away: np.ndarray) -> dict[str, float]:
        """Calcula mercados principales desde trayectorias completas."""

        home_total, away_total = home.sum(axis=1), away.sum(axis=1)
        first_half = home[:, :9].sum(axis=1) + away[:, :9].sum(axis=1)
        return {
            "home_win": float(np.mean(home_total > away_total)),
            "draw": float(np.mean(home_total == away_total)),
            "away_win": float(np.mean(home_total < away_total)),
            "over_2_5": float(np.mean(home_total + away_total >= 3)),
            "btts": float(np.mean((home_total > 0) & (away_total > 0))),
            "first_half_goal": float(np.mean(first_half > 0)),
        }

    @staticmethod
    def _window_markets(home: np.ndarray, away: np.ndarray) -> list[dict[str, float]]:
        """Agrega microventanas 5→15 minutos."""

        output = []
        for index in range(6):
            start = index * 3
            goals = home[:, start:start + 3].sum(axis=1)
            goals += away[:, start:start + 3].sum(axis=1)
            output.append({"window": index, "any_goal": float(np.mean(goals > 0))})
        return output

    @staticmethod
    def _trajectory_markets(
        home: np.ndarray, away: np.ndarray,
    ) -> dict[str, Any]:
        """Deriva mercados que dependen de la trayectoria completa."""

        combined = (home + away).reshape(len(home), 6, 3).sum(axis=2)
        active = combined > 0
        first = np.where(active.any(axis=1), active.argmax(axis=1), 6)
        scoring = active.sum(axis=1)
        labels = ("0_15", "16_30", "31_45", "46_60", "61_75", "76_90", "none")
        return {
            "first_goal_window": {
                label: float(np.mean(first == index))
                for index, label in enumerate(labels)},
            "scoring_windows": {
                str(count): float(np.mean(scoring == count))
                for count in range(7)},
            "consecutive_scoring_windows": float(np.mean(
                (active[:, :-1] & active[:, 1:]).any(axis=1))),
            "clustered_goals_same_window": float(np.mean(
                (combined >= 2).any(axis=1))),
            "second_half_more_active": float(np.mean(
                active[:, 3:].sum(axis=1) > active[:, :3].sum(axis=1))),
        }

    @staticmethod
    def _audit(
        request: SimulationRequest, home_states: np.ndarray,
        away_states: np.ndarray, home_rates: np.ndarray,
        away_rates: np.ndarray,
    ) -> dict[str, Any]:
        """Mide invariantes sin observar resultados del objetivo."""

        return {
            "home_mass_error": float(np.max(np.abs(
                home_rates.sum(axis=1) - request.lambda_home))),
            "away_mass_error": float(np.max(np.abs(
                away_rates.sum(axis=1) - request.lambda_away))),
            "home_style_changes": int(np.sum(
                home_states // 3 != home_states[:, :1] // 3)),
            "away_style_changes": int(np.sum(
                away_states // 3 != away_states[:, :1] // 3)),
            "target_post_cutoff_reads": 0,
            "joint_previous_state_transition": True,
            "home_temporal_allocation": home_rates.mean(axis=0).tolist(),
            "away_temporal_allocation": away_rates.mean(axis=0).tolist(),
        }

    @staticmethod
    def _request_dict(request: SimulationRequest) -> dict[str, Any]:
        """Serializa sólo entradas pre-match permitidas."""

        return {
            "match_id": request.match_id, "league_slug": request.league_slug,
            "home_team_id": request.home_team_id,
            "away_team_id": request.away_team_id,
            "cutoff_utc": request.cutoff_utc,
            "lambda_home": request.lambda_home,
            "lambda_away": request.lambda_away,
            "initial_home": list(request.initial_home),
            "initial_away": list(request.initial_away),
        }

    def _validate(self, request: SimulationRequest) -> None:
        """Rechaza intensidades o distribuciones inválidas."""

        if request.lambda_home < 0 or request.lambda_away < 0:
            raise ValueError("lambdas_must_be_nonnegative")
        for values in (request.initial_home, request.initial_away):
            if len(values) != STATES or min(values) < 0:
                raise ValueError("invalid_initial_state_distribution")
            if not np.isclose(sum(values), 1.0, atol=1e-12):
                raise ValueError("initial_state_distribution_not_normalized")

    @staticmethod
    def _hash(result: dict[str, Any]) -> str:
        """Calcula hash canónico de la predicción."""

        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
