"""Pooling jerárquico suave para transiciones Markov dependientes."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
TIERS = ("team", "competition", "window", "global")


def score_bucket(difference: int) -> str:
    """Agrupa el diferencial de goles al inicio de la ventana."""

    if difference <= -2:
        return "behind_2_plus"
    if difference == -1:
        return "behind_1"
    if difference == 0:
        return "level"
    if difference == 1:
        return "ahead_1"
    return "ahead_2_plus"


def build_transitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construye transiciones sólo con partidos entregados por el llamador."""

    indexed = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in rows}
    output = []
    for key, row in indexed.items():
        match_id, team_id, window = key
        if window >= 5:
            continue
        following = indexed.get((match_id, team_id, window + 1))
        rival = indexed.get((match_id, int(row["opponent_team_id"]), window))
        if not following or not rival:
            continue
        output.append({"team_id": team_id, "league_slug": str(row["league_slug"]), "is_home": bool(row["is_home"]), "window_index": window, "score_bucket": score_bucket(int(row["goal_difference_start"])), "state": str(row["state"]), "opponent_state": str(rival["state"]), "next_state": str(following["state"])})
    return output


class SoftTransitionModel:
    """Estima transiciones con pooling entre cuatro niveles de contexto."""

    def __init__(self, alpha: float = 32.0, specificity: float = 8.0) -> None:
        """Inicializa smoothing Dirichlet y fuerza de especificidad."""

        self.alpha = float(alpha)
        self.specificity = float(specificity)
        self.counts: dict[str, dict[tuple[Any, ...], Counter[str]]] = {}

    def fit(self, rows: list[dict[str, Any]]) -> None:
        """Ajusta conteos exclusivamente con el bloque de desarrollo."""

        self.counts = {tier: defaultdict(Counter) for tier in TIERS}
        for row in rows:
            for tier in TIERS:
                self.counts[tier][self._key(tier, row)][str(row["next_state"])] += 1

    def _key(self, tier: str, row: dict[str, Any]) -> tuple[Any, ...]:
        """Construye la clave jerárquica de un registro."""

        pair = (str(row["state"]), str(row["opponent_state"]))
        if tier == "global":
            return pair
        if tier == "window":
            return (str(row["league_slug"]), int(row["window_index"]), *pair)
        context = (str(row["league_slug"]), bool(row["is_home"]), int(row["window_index"]), str(row["score_bucket"]), *pair)
        return context if tier == "competition" else (int(row["team_id"]), *context)

    def _parent(self, tier: str, key: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]] | None:
        """Obtiene tier y clave padre."""

        if tier == "global":
            return None
        if tier == "window":
            return "global", key[2:]
        if tier == "competition":
            return "window", (key[0], key[2], key[4], key[5])
        return "competition", key[1:]

    def _posterior(self, tier: str, key: tuple[Any, ...]) -> dict[str, float]:
        """Calcula posterior suavizado hacia el padre disponible."""

        counts = self.counts[tier].get(key, Counter())
        support = sum(counts.values())
        parent = self._parent(tier, key)
        prior = {state: 1.0 / len(STATES) for state in STATES} if parent is None else self._posterior(*parent)
        denominator = support + self.alpha
        return {state: (counts[state] + self.alpha * prior[state]) / denominator for state in STATES}

    def predict(self, query: dict[str, Any]) -> tuple[dict[str, float], dict[str, int]]:
        """Mezcla todos los niveles con peso dependiente de soporte."""

        probabilities, supports, weights = {}, {}, {}
        for tier in TIERS:
            key = self._key(tier, query)
            probabilities[tier] = self._posterior(tier, key)
            supports[tier] = sum(self.counts[tier].get(key, Counter()).values())
            weights[tier] = 1.0 if tier == "global" else supports[tier] / (supports[tier] + self.specificity)
        total = sum(weights.values())
        blended = {state: sum(weights[tier] * probabilities[tier][state] for tier in TIERS) / total for state in STATES}
        return blended, supports

