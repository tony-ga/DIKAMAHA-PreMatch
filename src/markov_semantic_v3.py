"""Estados Markov conjuntos, causales y alineados con el mercado temporal.

# Requirements:
#     pip install numpy scikit-learn

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

TEMPO_STATES = ("calma", "construccion", "amenaza_sostenida", "intercambio_abierto")
CONTROL_STATES = ("bajo_presion", "neutral", "control")


@dataclass(frozen=True, slots=True)
class SemanticConfig:
    """Configura los estados sin utilizar goles en sus reglas."""

    version: str = "markov_state_semantics_v3"
    tempo_quantiles: tuple[float, float, float] = (0.25, 0.60, 0.85)
    control_quantile: float = 0.70
    initial_prior_strength: float = 8.0
    transition_prior_strength: float = 12.0
    recent_matches: int = 8


@dataclass(frozen=True, slots=True)
class SemanticThresholds:
    """Conserva umbrales aprendidos exclusivamente en desarrollo."""

    tempo: tuple[float, float, float]
    control_margin: float
    config: dict[str, Any]


def danger_score(row: dict[str, Any]) -> float:
    """Calcula amenaza sin usar goles ni marcador final."""

    shots = max(float(row["shots"]) - float(row["shots_on_target"]), 0.0)
    return (
        2.0 * float(row["shots_on_target"])
        + 0.6 * shots
        + 0.25 * float(row["shots_blocked"])
        + 0.25 * float(row["corners"])
    )


def _quantiles(values: Sequence[float], cuts: Sequence[float]) -> tuple[float, ...]:
    """Calcula cuantiles lineales de forma determinista."""

    if not values:
        raise ValueError("semantic_thresholds_require_development_rows")
    return tuple(float(value) for value in np.quantile(values, cuts))


def pair_windows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte dos filas de equipo en una observación conjunta por ventana."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["match_id"]), int(row["window_index"]))].append(row)
    output = [_pair(key, values) for key, values in grouped.items() if len(values) == 2]
    return sorted(output, key=lambda row: (row["match_date"], row["match_id"], row["window_index"]))


def _pair(key: tuple[int, int], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Orienta una ventana conjunta por local y visitante."""

    home = next(row for row in rows if bool(row["is_home"]))
    away = next(row for row in rows if not bool(row["is_home"]))
    home_danger, away_danger = danger_score(home), danger_score(away)
    return {
        "match_id": key[0], "window_index": key[1], "match_date": str(home["match_date"]),
        "league_slug": str(home["league_slug"]), "home_team_id": int(home["team_id"]),
        "away_team_id": int(away["team_id"]), "home_danger": home_danger,
        "away_danger": away_danger, "tempo": home_danger + away_danger,
        "control_margin": home_danger - away_danger,
        "home_goals": float(home["goals"]), "away_goals": float(away["goals"]),
        "goals": float(home["goals"]) + float(away["goals"]),
    }


class SemanticStateLabeler:
    """Ajusta y aplica la taxonomía conjunta de ritmo y control."""

    def __init__(self, config: SemanticConfig | None = None) -> None:
        """Inicializa el etiquetador sin umbrales aprendidos."""

        self.config = config or SemanticConfig()
        self.thresholds: SemanticThresholds | None = None

    def fit(self, rows: Sequence[dict[str, Any]]) -> "SemanticStateLabeler":
        """Aprende umbrales únicamente desde observaciones de desarrollo."""

        tempo = _quantiles([float(row["tempo"]) for row in rows], self.config.tempo_quantiles)
        margins = [abs(float(row["control_margin"])) for row in rows]
        control = _quantiles(margins, (self.config.control_quantile,))[0]
        self.thresholds = SemanticThresholds(tempo, control, asdict(self.config))
        return self

    def transform(self, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Etiqueta observaciones sin consultar goles ni ventanas futuras."""

        if self.thresholds is None:
            raise RuntimeError("semantic_labeler_not_fitted")
        return [{**row, **self._labels(row)} for row in rows]

    def _labels(self, row: dict[str, Any]) -> dict[str, str]:
        """Deriva los dos ejes semánticos de una observación."""

        assert self.thresholds is not None
        index = int(np.searchsorted(self.thresholds.tempo, float(row["tempo"]), side="left"))
        margin, limit = float(row["control_margin"]), self.thresholds.control_margin
        home = "control" if margin >= limit else "bajo_presion" if margin <= -limit else "neutral"
        away = "bajo_presion" if home == "control" else "control" if home == "bajo_presion" else "neutral"
        return {"tempo_state": TEMPO_STATES[index], "home_control_state": home, "away_control_state": away}


class TempoTransitionModel:
    """Cadena conjunta con pooling suave por equipo, liga y ventana."""

    def __init__(self, config: SemanticConfig | None = None) -> None:
        """Inicializa contadores jerárquicos."""

        self.config = config or SemanticConfig()
        self.global_counts: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
        self.league_counts: dict[tuple[str, int, str], Counter[str]] = defaultdict(Counter)
        self.team_counts: dict[tuple[int, int, str], Counter[str]] = defaultdict(Counter)

    def fit(self, rows: Sequence[dict[str, Any]], development_ids: set[int]) -> "TempoTransitionModel":
        """Ajusta transiciones dentro de partidos de desarrollo."""

        grouped = _by_match(rows)
        for match_id in development_ids:
            sequence = sorted(grouped[match_id], key=lambda row: int(row["window_index"]))
            for current, following in zip(sequence, sequence[1:]):
                self._add(current, str(following["tempo_state"]))
        return self

    def _add(self, row: dict[str, Any], following: str) -> None:
        """Acumula una transición en todos sus niveles causales."""

        window, current = int(row["window_index"]), str(row["tempo_state"])
        league = str(row["league_slug"])
        self.global_counts[(window, current)][following] += 1
        self.league_counts[(league, window, current)][following] += 1
        for team in (int(row["home_team_id"]), int(row["away_team_id"])):
            self.team_counts[(team, window, current)][following] += 1

    def matrix(self, league: str, teams: tuple[int, int], window: int) -> np.ndarray:
        """Construye una matriz normalizada para un fixture pre-match."""

        return np.asarray(
            [self._distribution(league, teams, window, state) for state in TEMPO_STATES],
            dtype=float,
        )

    def parent_matrix(self, league: str, window: int) -> np.ndarray:
        """Expone el prior liga+ventana para auditoría comparativa."""

        rows = []
        uniform = np.full(len(TEMPO_STATES), 1.0 / len(TEMPO_STATES))
        for state in TEMPO_STATES:
            global_p = _pooled(
                self.global_counts[(window, state)], uniform, self.config.transition_prior_strength
            )
            rows.append(
                _pooled(
                    self.league_counts[(league, window, state)],
                    global_p,
                    self.config.transition_prior_strength,
                )
            )
        return np.asarray(rows, dtype=float)

    def _distribution(
        self, league: str, teams: tuple[int, int], window: int, state: str
    ) -> list[float]:
        """Aplica pooling global→liga→equipos según soporte."""

        uniform = np.full(len(TEMPO_STATES), 1.0 / len(TEMPO_STATES))
        global_p = _pooled(self.global_counts[(window, state)], uniform, self.config.transition_prior_strength)
        league_p = _pooled(self.league_counts[(league, window, state)], global_p, self.config.transition_prior_strength)
        counts = self.team_counts[(teams[0], window, state)] + self.team_counts[(teams[1], window, state)]
        return _pooled(counts, league_p, self.config.transition_prior_strength).tolist()


def _pooled(counts: Counter[str], parent: np.ndarray, strength: float) -> np.ndarray:
    """Suaviza un contador categórico con un prior explícito."""

    observed = np.asarray([counts[state] for state in TEMPO_STATES], dtype=float)
    total = float(observed.sum()) + strength
    return (observed + strength * parent) / total


def _by_match(rows: Sequence[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa observaciones por partido completo."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return grouped


def initial_distribution(
    histories: dict[int, list[str]], league_prior: np.ndarray, teams: tuple[int, int],
    config: SemanticConfig,
) -> tuple[np.ndarray, int]:
    """Estima state_0 con aperturas históricas previas al kickoff."""

    counts = Counter()
    for team in teams:
        counts.update(histories.get(team, [])[-config.recent_matches:])
    observed = np.asarray([counts[state] for state in TEMPO_STATES], dtype=float)
    support = int(observed.sum())
    values = observed + config.initial_prior_strength * league_prior
    return values / values.sum(), support


def chain_features(
    initial: np.ndarray, model: TempoTransitionModel, league: str,
    teams: tuple[int, int],
) -> list[float]:
    """Propaga tres ocupaciones esperadas para el primer tiempo."""

    first = initial @ model.matrix(league, teams, 0)
    second = first @ model.matrix(league, teams, 1)
    features = [*initial.tolist(), *first.tolist(), *second.tolist()]
    return [*features, _entropy(initial), _entropy(first), _entropy(second)]


def _entropy(values: np.ndarray) -> float:
    """Calcula entropía natural de una distribución discreta."""

    positive = values[values > 0.0]
    return float(-np.sum(positive * np.log(positive)))


class ResidualSolver(ABC):
    """Contrato de un calibrador residual anclado al baseline."""

    @abstractmethod
    def fit(self, rows: Sequence[Sequence[float]], targets: Sequence[int]) -> None:
        """Ajusta el solver con datos anteriores al holdout."""

    @abstractmethod
    def predict(self, rows: Sequence[Sequence[float]]) -> np.ndarray:
        """Devuelve probabilidades binarias calibradas."""


class LogisticResidualSolver(ResidualSolver):
    """Implementa el residual mínimo con regularización L2."""

    def __init__(self, regularization: float) -> None:
        """Configura un pipeline determinista y regularizado."""

        self.model: Pipeline = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=regularization, max_iter=2000, random_state=20260727),
        )

    def fit(self, rows: Sequence[Sequence[float]], targets: Sequence[int]) -> None:
        """Ajusta el pipeline sobre un bloque temporal anterior."""

        self.model.fit(rows, targets)

    def predict(self, rows: Sequence[Sequence[float]]) -> np.ndarray:
        """Predice la clase positiva."""

        return np.asarray(self.model.predict_proba(rows)[:, 1], dtype=float)


def logit(probability: float) -> float:
    """Convierte una probabilidad acotada a log-odds."""

    value = min(max(float(probability), 1e-9), 1.0 - 1e-9)
    return math.log(value / (1.0 - value))


# Version: 1.0.0
# Created: 2026-07-27
