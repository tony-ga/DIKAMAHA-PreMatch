"""Simulador Markov contrafactual pre-match, separado de la salida oficial.

Todas las probabilidades son frecuencias históricas MLE del bloque de
desarrollo. Cuando el soporte contextual es insuficiente se usa un fallback
histórico más amplio y se conserva una advertencia explícita.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

WINDOWS = (("early", 0, 30), ("middle", 30, 60), ("late", 60, 121))
FIRST_OUTCOMES = ("home_early", "home_middle", "home_late", "away_early", "away_middle", "away_late", "no_goal")
SECOND_OUTCOMES = ("same_team_second", "equalizer", "conserve_advantage")
BEHAVIOR_TYPES = ("goal", "shot_off_target", "shot_on_target", "shot_blocked", "corner", "yellow", "red", "substitution")


@dataclass(frozen=True, slots=True)
class CounterfactualConfig:
    """Configuración metodológica sin porcentajes manuales."""

    version: str = "markov_counterfactual_v1"
    context_minutes: int = 15
    minimum_context_support: int = 10
    minimum_reportable_support: int = 30
    bootstrap_replicates: int = 1000
    bootstrap_seed: int = 7112


def window_name(minute: int) -> str:
    """Asigna una ventana temporal contractual a un minuto observado."""

    for name, start, end in WINDOWS:
        if start <= minute < end:
            return name
    return "late"


def strength_bin(lambda_home: float, lambda_away: float, cuts: tuple[float, float]) -> str:
    """Clasifica fuerza relativa usando cortes aprendidos en desarrollo."""

    value = math.log(lambda_home / lambda_away)
    return "away_stronger" if value <= cuts[0] else "home_stronger" if value >= cuts[1] else "balanced"


def learned_strength_cuts(matches: Iterable[dict[str, Any]]) -> tuple[float, float]:
    """Aprende terciles deterministas de log-ratio sólo sobre desarrollo."""

    values = sorted(math.log(row["lambda_base_home"] / row["lambda_base_away"]) for row in matches)
    if len(values) < 3:
        raise ValueError("insufficient_strength_cut_support")
    return values[len(values) // 3], values[(2 * len(values)) // 3]


def actual_outcome(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye targets secuenciales sólo desde eventos cronológicos."""

    goals = [row for row in events if row["event_type"] == "goal" and row.get("valid", True)]
    if not goals:
        return {"first": "no_goal", "first_side": None, "first_minute": None, "second": None, "behavior": {}}
    first, home = goals[0], int(match["home_team_id"])
    side = "home" if first.get("team_id") == home else "away" if first.get("team_id") == int(match["away_team_id"]) else "unknown"
    if side == "unknown":
        return {"first": "unknown", "first_side": side, "first_minute": first["minute"], "second": None, "behavior": {}}
    second = _second_outcome(goals, first, side, match)
    return {"first": f"{side}_{window_name(first['minute'])}", "first_side": side,
            "first_minute": int(first["minute"]), "second": second,
            "behavior": behavior_after_goal(match, events, first, side)}


def _second_outcome(goals: list[dict[str, Any]], first: dict[str, Any], side: str, match: dict[str, Any]) -> str:
    """Etiqueta el siguiente ciclo sin usar el marcador final como feature."""

    if len(goals) == 1:
        return "conserve_advantage"
    scoring_team = int(match[f"{side}_team_id"])
    return "same_team_second" if goals[1].get("team_id") == scoring_team else "equalizer"


def behavior_after_goal(match: dict[str, Any], events: list[dict[str, Any]], first: dict[str, Any], side: str) -> dict[str, float]:
    """Cuenta comportamiento observado en los 15 minutos posteriores al gol."""

    start = float(first["minute"]) + float(first.get("second", 0)) / 60.0
    scorer = int(match[f"{side}_team_id"])
    counts: Counter[str] = Counter()
    for event in events:
        elapsed = float(event["minute"]) + float(event.get("second", 0)) / 60.0
        if start < elapsed <= start + 15 and event["event_type"] in BEHAVIOR_TYPES:
            role = "ahead" if event.get("team_id") == scorer else "behind"
            counts[f"{role}_{event['event_type']}"] += 1
    return {key: float(value) for key, value in counts.items()}


def poisson_first_goal(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """Deriva primer gol y ventana directamente de intensidades pre-match."""

    total = lambda_home + lambda_away
    rate = total / 90.0
    output = {key: 0.0 for key in FIRST_OUTCOMES}
    output["no_goal"] = math.exp(-total)
    for name, start, end in WINDOWS:
        upper = min(end, 90)
        mass = math.exp(-rate * start) - math.exp(-rate * upper)
        output[f"home_{name}"] = mass * lambda_home / total
        output[f"away_{name}"] = mass * lambda_away / total
    return _normalize(output)


def _normalize(values: dict[str, float]) -> dict[str, float]:
    """Normaliza frecuencias no negativas y rechaza masa inválida."""

    total = sum(values.values())
    if total <= 0 or any(value < 0 or not math.isfinite(value) for value in values.values()):
        raise ValueError("invalid_probability_mass")
    return {key: value / total for key, value in values.items()}


class CounterfactualEstimator:
    """Estimador empírico contextual congelado en desarrollo."""

    def __init__(self, config: CounterfactualConfig | None = None) -> None:
        """Inicializa acumuladores sin observar validación o confirmación."""

        self.config = config or CounterfactualConfig()
        self.cuts = (0.0, 0.0)
        self.first_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.second_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.behavior_sums: dict[str, Counter[str]] = defaultdict(Counter)

    def fit(self, matches: list[dict[str, Any]]) -> "CounterfactualEstimator":
        """Ajusta frecuencias MLE exclusivamente con filas development."""

        if any(row["block"] != "development" for row in matches):
            raise ValueError("fit_requires_development_only")
        self.cuts = learned_strength_cuts(matches)
        for row in matches:
            outcome = row["actual"]
            if outcome["first"] not in FIRST_OUTCOMES:
                continue
            strength = strength_bin(row["lambda_base_home"], row["lambda_base_away"], self.cuts)
            self.first_counts[strength][outcome["first"]] += 1
            self.first_counts["global"][outcome["first"]] += 1
            if outcome["second"]:
                key = f"{outcome['first_side']}|{window_name(outcome['first_minute'])}|{strength}"
                self._add_context(key, outcome)
                self._add_context(f"{outcome['first_side']}|{window_name(outcome['first_minute'])}|global", outcome)
        return self

    def _add_context(self, key: str, outcome: dict[str, Any]) -> None:
        """Acumula transición y comportamiento para un contexto histórico."""

        self.second_counts[key][outcome["second"]] += 1
        self.behavior_sums[key].update(outcome["behavior"])

    def predict(self, match: dict[str, Any]) -> dict[str, Any]:
        """Genera el árbol completo sin consumir eventos del partido objetivo."""

        strength = strength_bin(match["lambda_base_home"], match["lambda_base_away"], self.cuts)
        first = self._distribution(self.first_counts[strength], self.first_counts["global"], FIRST_OUTCOMES)
        branches = []
        for outcome, probability in first.items():
            if probability == 0.0:
                continue
            if outcome == "no_goal":
                branches.append(self._no_goal_branch(match, probability, strength))
            else:
                branches.extend(self._goal_branches(match, outcome, probability, strength))
        return {"match_id": match["match_id"], "block": match["block"], "strength_bin": strength,
                "lambda_base_home": match["lambda_base_home"], "lambda_base_away": match["lambda_base_away"],
                "first_goal_distribution": first, "lambda_baseline_first_goal": poisson_first_goal(match["lambda_base_home"], match["lambda_base_away"]),
                "branches": branches, "branch_probability_sum": sum(row["probability"] for row in branches),
                "official_output_modified": False, "hawkes_used": False, "version": self.config.version}

    def _distribution(self, context: Counter[str], fallback: Counter[str], outcomes: tuple[str, ...]) -> dict[str, float]:
        """Usa MLE contextual o fallback global, ambos aprendidos del histórico."""

        source = context if sum(context.values()) >= self.config.minimum_context_support else fallback
        return _normalize({outcome: float(source[outcome]) for outcome in outcomes})

    def _goal_branches(self, match: dict[str, Any], first: str, first_probability: float, strength: str) -> list[dict[str, Any]]:
        """Expande segundo gol, empate o conservación tras el primer gol."""

        side, window = first.split("_", 1)
        exact = f"{side}|{window}|{strength}"
        fallback = f"{side}|{window}|global"
        counts = self.second_counts[exact]
        selected = exact if sum(counts.values()) >= self.config.minimum_context_support else fallback
        second = self._distribution(counts, self.second_counts[fallback], SECOND_OUTCOMES)
        support = sum(self.second_counts[selected].values())
        behavior = {key: value / support for key, value in self.behavior_sums[selected].items()} if support else {}
        return [self._branch(match, side, window, next_outcome, first_probability * probability,
                             probability, second, support, behavior, selected) for next_outcome, probability in second.items()]

    def _branch(self, match: dict[str, Any], side: str, window: str, outcome: str, probability: float,
                conditional: float, second: dict[str, float], support: int, behavior: dict[str, float], context: str) -> dict[str, Any]:
        """Materializa una rama contextual con intensidades y provenance."""

        start, end = next((a, b) for name, a, b in WINDOWS if name == window)
        hypothetical_minute = (start + min(end, 90)) / 2.0
        ahead_state, behind_state = self._expected_states(behavior)
        lambdas = self._contextual_lambdas(match, side, behavior)
        behavior_summary = self._behavior_summary(behavior)
        return {"first_goal_team": side, "first_goal_window": window, "interval": [start, min(end, 90)],
                "score_after_first": "1-0" if side == "home" else "0-1", "next_outcome": outcome,
                "hypothetical_first_goal_minute": hypothetical_minute, "time_remaining_minutes": 90.0 - hypothetical_minute,
                "probability": probability, "conditional_next_probability": conditional,
                "probability_second_goal_same_team": second["same_team_second"],
                "probability_equalizer": second["equalizer"],
                "probability_conserve_advantage": second["conserve_advantage"],
                "next_cycle": "reset_after_equalizer" if outcome == "equalizer" else "two_goal_lead_cycle" if outcome == "same_team_second" else "advantage_preservation_cycle",
                "expected_transition": {side: ahead_state, "away" if side == "home" else "home": behind_state},
                **lambdas, **behavior_summary, "expected_behavior_15m": behavior, "historical_support": support,
                "support_level": "adequate" if support >= self.config.minimum_reportable_support else "low",
                "warnings": [] if support >= self.config.minimum_reportable_support else ["low_contextual_support"],
                "provenance": {"context": context, "source_block": "development", "hawkes": "disabled"}}

    @staticmethod
    def _contextual_lambdas(match: dict[str, Any], side: str, behavior: dict[str, float]) -> dict[str, float]:
        """Convierte tasa Poisson MLE de 15 minutos a escala de 90 minutos."""

        ahead = behavior.get("ahead_goal", 0.0) * 6.0
        behind = behavior.get("behind_goal", 0.0) * 6.0
        home = ahead if side == "home" else behind
        away = behind if side == "home" else ahead
        return {"lambda_home": home if home > 0 else match["lambda_base_home"],
                "lambda_away": away if away > 0 else match["lambda_base_away"]}

    @staticmethod
    def _behavior_summary(behavior: dict[str, float]) -> dict[str, Any]:
        """Resume tiros, corners, presión, tarjetas y cambios por rol."""

        def total(role: str, suffixes: tuple[str, ...]) -> float:
            return sum(value for key, value in behavior.items() if key.startswith(f"{role}_") and key.endswith(suffixes))

        return {"expected_shots_ahead_15m": total("ahead", ("shot_off_target", "shot_on_target", "shot_blocked")),
                "expected_shots_behind_15m": total("behind", ("shot_off_target", "shot_on_target", "shot_blocked")),
                "expected_shots_conceded_ahead_15m": total("behind", ("shot_off_target", "shot_on_target", "shot_blocked")),
                "expected_shots_conceded_behind_15m": total("ahead", ("shot_off_target", "shot_on_target", "shot_blocked")),
                "expected_shots_on_target_ahead_15m": behavior.get("ahead_shot_on_target", 0.0),
                "expected_shots_on_target_behind_15m": behavior.get("behind_shot_on_target", 0.0),
                "expected_corners_ahead_15m": behavior.get("ahead_corner", 0.0),
                "expected_corners_behind_15m": behavior.get("behind_corner", 0.0),
                "expected_pressure_ahead_15m": total("ahead", ("shot_off_target", "shot_on_target", "shot_blocked", "corner")),
                "expected_pressure_behind_15m": total("behind", ("shot_off_target", "shot_on_target", "shot_blocked", "corner")),
                "expected_cards_ahead_15m": total("ahead", ("yellow", "red")),
                "expected_cards_behind_15m": total("behind", ("yellow", "red")),
                "expected_substitutions_ahead_15m": behavior.get("ahead_substitution", 0.0),
                "expected_substitutions_behind_15m": behavior.get("behind_substitution", 0.0)}

    @staticmethod
    def _expected_states(behavior: dict[str, float]) -> tuple[str, str]:
        """Relaciona presión histórica esperada con estados tácticos interpretables."""

        ahead = sum(value for key, value in behavior.items() if key.startswith("ahead_shot") or key == "ahead_corner")
        behind = sum(value for key, value in behavior.items() if key.startswith("behind_shot") or key == "behind_corner")
        return ("repliegue" if behind > ahead else "equilibrio", "asedio" if behind > ahead else "equilibrio")

    def _no_goal_branch(self, match: dict[str, Any], probability: float, strength: str) -> dict[str, Any]:
        """Representa explícitamente el partido sin primer gol."""

        return {"first_goal_team": None, "first_goal_window": None, "interval": [0, 90], "score_after_first": "0-0",
                "next_outcome": "no_goal", "probability": probability, "conditional_next_probability": 1.0,
                "expected_transition": {"home": "equilibrio", "away": "equilibrio"},
                "lambda_home": match["lambda_base_home"], "lambda_away": match["lambda_base_away"],
                "expected_behavior_15m": {}, "historical_support": sum(self.first_counts[strength].values()),
                "support_level": "aggregate", "warnings": [],
                "provenance": {"context": strength, "source_block": "development", "hawkes": "disabled"}}

    def support(self) -> dict[str, Any]:
        """Expone conteos exactos y celdas por debajo del umbral contractual."""

        contexts = {key: dict(value) for key, value in sorted(self.second_counts.items())}
        sparse = [{"context": key, "support": sum(value.values())} for key, value in sorted(self.second_counts.items())
                  if sum(value.values()) < self.config.minimum_reportable_support]
        return {"first_goal_counts": {key: dict(value) for key, value in sorted(self.first_counts.items())},
                "second_transition_counts": contexts, "sparse_contexts": sparse, "strength_cuts": list(self.cuts)}


def categorical_metrics(actual: str, probabilities: dict[str, float]) -> dict[str, float]:
    """Calcula log score y Brier para una observación categórica."""

    probability = max(probabilities.get(actual, 0.0), 1e-15)
    brier = sum((value - (1.0 if key == actual else 0.0)) ** 2 for key, value in probabilities.items())
    return {"log_score": -math.log(probability), "brier": brier}


# Version: 1.0.0
# Created: 2026-07-16
