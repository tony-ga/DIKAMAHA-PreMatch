"""Markov pre-match para mercados de corners, tiros y tarjetas por equipo.

Version: 1.1.0
Created: 2026-07-28
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

METRICS = ("corners", "shots", "yellow_cards")
MARKET_LINES = {"corners": 2, "shots": 5, "yellow_cards": 0}
STATE_NAMES = {
    "corners": ("sin_corner", "presion", "asedio"),
    "shots": ("bajo", "ataque", "asedio"),
    "yellow_cards": ("limpio", "amonestado", "desorden"),
}


@dataclass(frozen=True, slots=True)
class TeamTrajectory:
    """Predicción de un equipo para 1T y 2T."""

    probabilities: dict[str, float]
    baselines: dict[str, float]
    expected_counts: dict[str, float]
    distributions: dict[str, dict[int, float]]
    baseline_distributions: dict[str, dict[int, float]]


def market_name(metric: str, side: str, half: str) -> str:
    """Construye el identificador público de mercado."""

    return f"{side}_{metric}_{half}_over_{MARKET_LINES[metric]}_5"


def state_for(metric: str, count: int) -> int:
    """Asigna un estado semántico fijo al conteo de ventana."""

    if metric == "shots":
        return 0 if count <= 1 else 1 if count <= 3 else 2
    return 0 if count == 0 else 1 if count == 1 else 2


def _normalize(values: list[float]) -> list[float]:
    """Normaliza un vector positivo."""

    total = sum(values)
    return [value / total for value in values]


class TeamMarketMarkov:
    """Cadena por métrica con pooling causal equipo→liga."""

    def __init__(self) -> None:
        """Inicializa acumuladores vacíos."""

        self._initial: dict[Any, list[float]] = {}
        self._league_initial: dict[Any, list[float]] = {}
        self._transition: dict[Any, list[float]] = {}
        self._league_transition: dict[Any, list[float]] = {}
        self._emissions: dict[Any, Counter[int]] = defaultdict(Counter)
        self._baseline: dict[Any, list[float]] = {}
        self._global_baseline: dict[Any, list[float]] = {}

    def predict_match(self, match: dict[str, Any]) -> dict[str, TeamTrajectory]:
        """Predice ambas orientaciones antes de actualizar historia."""

        return {
            side: self._predict_team(match, side)
            for side in ("home", "away")
        }

    def update(self, match: dict[str, Any]) -> None:
        """Actualiza la cadena sólo después de la predicción."""

        for side in ("home", "away"):
            self._update_team(match, side)

    def _predict_team(
        self, match: dict[str, Any], side: str,
    ) -> TeamTrajectory:
        """Deriva distribuciones de mitad para un equipo."""

        home = side == "home"
        team_id = int(match[f"{side}_team_id"])
        league = str(match["league_slug"])
        probabilities, baselines, expected = {}, {}, {}
        distributions, baseline_distributions = {}, {}
        for metric in METRICS:
            values = self._metric_prediction(
                league, team_id, home, metric, side)
            probabilities.update(values[0])
            baselines.update(values[1])
            expected.update(values[2])
            distributions.update(values[3])
            baseline_distributions.update(values[4])
        return TeamTrajectory(
            probabilities, baselines, expected,
            distributions, baseline_distributions)

    def _metric_prediction(
        self, league: str, team_id: int, home: bool,
        metric: str, side: str,
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float],
               dict[str, dict[int, float]], dict[str, dict[int, float]]]:
        """Deriva probabilidades y PMF para una sola métrica."""

        periods = ("first_half", "second_half", "full_match")
        distributions = {
            f"{metric}_{period}": self._period_distribution(
                league, team_id, home, metric, period)
            for period in periods}
        baselines_dist = {
            f"{metric}_{period}": self._period_distribution(
                league, -1, home, metric, period)
            for period in periods}
        probabilities, baselines = {}, {}
        for index, half in enumerate(periods[:2]):
            name = market_name(metric, side, half)
            probabilities[name] = _over_distribution(
                distributions[f"{metric}_{half}"], MARKET_LINES[metric])
            baselines[name] = self._baseline_probability(
                league, home, metric, index)
        expected = {key: _expectation(value)
                    for key, value in distributions.items()}
        return probabilities, baselines, expected, distributions, baselines_dist

    def _period_distribution(
        self, league: str, team_id: int, home: bool, metric: str, period: str,
    ) -> dict[int, float]:
        """Propaga una marginal exacta sin construir el producto 1T×2T."""

        states = self._initial_probabilities(league, team_id, home, metric)
        joint = {(state, 0): value for state, value in enumerate(states)}
        for window in range(6):
            joint = self._emit_period(joint, metric, window, period)
            if window < 5:
                joint = self._advance_period(
                    joint, league, team_id, home, metric, window + 1)
        return _collapse_counts(joint)

    def _emit_period(
        self, joint: dict[tuple[int, int], float], metric: str,
        window: int, period: str,
    ) -> dict[tuple[int, int], float]:
        """Aplica emisiones a una sola marginal temporal."""

        output: dict[tuple[int, int], float] = defaultdict(float)
        included = _window_in_period(window, period)
        for (state, total), probability in joint.items():
            for count, emission in self._emission(metric, state).items():
                key = (state, total + count if included else total)
                output[key] += probability * emission
        return dict(output)

    def _advance_period(
        self, joint: dict[tuple[int, int], float], league: str,
        team_id: int, home: bool, metric: str, window: int,
    ) -> dict[tuple[int, int], float]:
        """Propaga estados conservando un solo conteo acumulado."""

        output: dict[tuple[int, int], float] = defaultdict(float)
        for (previous, total), probability in joint.items():
            values = self._transition_probabilities(
                league, team_id, home, metric, window, previous)
            for state, value in enumerate(values):
                output[(state, total)] += probability * value
        return dict(output)

    def _distribution(
        self, league: str, team_id: int, home: bool, metric: str,
    ) -> dict[tuple[int, int], float]:
        """Propaga estados, emisiones y conteos durante seis ventanas."""

        states = self._initial_probabilities(league, team_id, home, metric)
        joint = {(state, 0, 0): probability
                 for state, probability in enumerate(states)}
        for window in range(6):
            emitted = self._emit(joint, metric, window)
            if window == 5:
                joint = emitted
                break
            joint = self._advance(
                emitted, league, team_id, home, metric, window + 1)
        output: dict[tuple[int, int], float] = defaultdict(float)
        for (_, first, second), probability in joint.items():
            output[(first, second)] += probability
        return dict(output)

    def _emit(
        self, joint: dict[tuple[int, int, int], float],
        metric: str, window: int,
    ) -> dict[tuple[int, int, int], float]:
        """Aplica emisiones empíricas por estado."""

        output: dict[tuple[int, int, int], float] = defaultdict(float)
        for (state, first, second), probability in joint.items():
            for count, emission in self._emission(metric, state).items():
                key = (state, first + count, second) if window < 3 else (
                    state, first, second + count)
                output[key] += probability * emission
        return dict(output)

    def _advance(
        self, joint: dict[tuple[int, int, int], float], league: str,
        team_id: int, home: bool, metric: str, window: int,
    ) -> dict[tuple[int, int, int], float]:
        """Propaga una transición condicionada por estado previo."""

        output: dict[tuple[int, int, int], float] = defaultdict(float)
        for (previous, first, second), probability in joint.items():
            transition = self._transition_probabilities(
                league, team_id, home, metric, window, previous)
            for state, value in enumerate(transition):
                output[(state, first, second)] += probability * value
        return dict(output)

    def _initial_probabilities(
        self, league: str, team_id: int, home: bool, metric: str,
    ) -> list[float]:
        """Mezcla soporte de equipo, liga y prior uniforme."""

        team = self._initial.get((league, team_id, home, metric), [0.0] * 3)
        league_values = self._league_initial.get(
            (league, home, metric), [0.0] * 3)
        return _normalize([
            team[index] + 5.0 * league_values[index] / max(
                sum(league_values), 1.0) + 1.0
            for index in range(3)
        ])

    def _transition_probabilities(
        self, league: str, team_id: int, home: bool, metric: str,
        window: int, previous: int,
    ) -> list[float]:
        """Mezcla transición específica y liga/localía."""

        team = self._transition.get(
            (league, team_id, home, metric, window, previous), [0.0] * 3)
        league_values = self._league_transition.get(
            (league, home, metric, window, previous), [0.0] * 3)
        return _normalize([
            team[index] + 8.0 * league_values[index] / max(
                sum(league_values), 1.0) + 1.0
            for index in range(3)
        ])

    def _emission(self, metric: str, state: int) -> dict[int, float]:
        """Devuelve emisión global suavizada del estado."""

        counts = self._emissions[(metric, state)]
        allowed = _allowed_counts(metric, state, counts)
        total = sum(counts.values()) + len(allowed)
        return {count: (counts[count] + 1.0) / total for count in allowed}

    def _baseline_probability(
        self, league: str, home: bool, metric: str, half: int,
    ) -> float:
        """Calcula prior Bernoulli liga/localía con smoothing global."""

        local = self._baseline.get(
            (league, home, metric, half), [0.0, 0.0])
        global_values = self._global_baseline.get(
            (metric, half), [0.0, 0.0])
        prior = (global_values[0] + 1.0) / (global_values[1] + 2.0)
        return (local[0] + 20.0 * prior) / (local[1] + 20.0)

    def _update_team(self, match: dict[str, Any], side: str) -> None:
        """Actualiza estados, emisiones y baseline de un equipo."""

        home, league = side == "home", str(match["league_slug"])
        team_id, windows = int(match[f"{side}_team_id"]), match[side]
        for metric in METRICS:
            counts = [int(row[metric]) for row in windows]
            states = [state_for(metric, count) for count in counts]
            self._add(self._initial, (league, team_id, home, metric), states[0])
            self._add(self._league_initial, (league, home, metric), states[0])
            for window, (previous, state) in enumerate(
                    zip(states, states[1:]), start=1):
                self._add(self._transition, (
                    league, team_id, home, metric, window, previous), state)
                self._add(self._league_transition, (
                    league, home, metric, window, previous), state)
            for state, count in zip(states, counts):
                self._emissions[(metric, state)][count] += 1
            self._update_baseline(league, home, metric, counts)

    def _update_baseline(
        self, league: str, home: bool, metric: str, counts: list[int],
    ) -> None:
        """Actualiza outcomes binarios de ambas mitades."""

        for half, values in enumerate((counts[:3], counts[3:])):
            outcome = float(sum(values) > MARKET_LINES[metric])
            self._add_pair(
                self._baseline, (league, home, metric, half), outcome)
            self._add_pair(
                self._global_baseline, (metric, half), outcome)

    @staticmethod
    def _add(store: dict[Any, list[float]], key: Any, state: int) -> None:
        """Incrementa un vector de tres estados."""

        values = store.setdefault(key, [0.0] * 3)
        values[state] += 1.0

    @staticmethod
    def _add_pair(
        store: dict[Any, list[float]], key: Any, outcome: float,
    ) -> None:
        """Acumula positivos y observaciones."""

        values = store.setdefault(key, [0.0, 0.0])
        values[0] += outcome
        values[1] += 1.0


def _allowed_counts(
    metric: str, state: int, observed: Counter[int],
) -> list[int]:
    """Define soporte de emisión sin borrar colas observadas."""

    if state == 0:
        base = [0, 1] if metric == "shots" else [0]
    elif state == 1:
        base = [2, 3] if metric == "shots" else [1]
    else:
        base = [4] if metric == "shots" else [2]
    return sorted(set(base) | set(observed))


def _marginal(
    distribution: dict[tuple[int, int], float], period: int | str,
) -> dict[int, float]:
    """Marginaliza la distribución conjunta por mitad o partido."""

    output: dict[int, float] = defaultdict(float)
    for (first, second), probability in distribution.items():
        count = first + second if period == "full_match" else (
            first if period == "first_half" else second)
        output[count] += probability
    total = sum(output.values())
    return {
        count: probability / total
        for count, probability in sorted(output.items())
    }


def _expectation(distribution: dict[int, float]) -> float:
    """Calcula la media de una PMF discreta."""

    return sum(count * probability for count, probability in distribution.items())


def _over_joint(
    distribution: dict[tuple[int, int], float], half: int, line: int,
) -> float:
    """Calcula la cola de una mitad en la distribución conjunta."""

    return sum(value for totals, value in distribution.items()
               if totals[half] > line)


def _window_in_period(window: int, period: str) -> bool:
    """Indica si una ventana contribuye al periodo solicitado."""

    return period == "full_match" or (
        period == "first_half" and window < 3) or (
        period == "second_half" and window >= 3)


def _collapse_counts(
    distribution: dict[tuple[int, int], float],
) -> dict[int, float]:
    """Marginaliza estado y normaliza conteos."""

    output: dict[int, float] = defaultdict(float)
    for (_, count), probability in distribution.items():
        output[count] += probability
    total = sum(output.values())
    return {count: value / total for count, value in sorted(output.items())}


def _over_distribution(distribution: dict[int, float], line: int) -> float:
    """Calcula la cola superior de una PMF marginal."""

    return sum(value for count, value in distribution.items() if count > line)


# Version: 1.2.0
# Created: 2026-07-28
