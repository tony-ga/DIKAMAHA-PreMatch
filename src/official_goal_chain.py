"""Cadena causal Dixon-Coles y Kalman para mercados oficiales de goles.

# Requirements:
#   numpy>=1.24
#   pandas>=2.0
#   scipy>=1.10

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.dixon_coles_v1 import DixonColesConfig, DixonColesEstimatorV1
from src.kalman_v2 import KalmanV2Config, KalmanV2Filter, KalmanV2State, poisson_matrix
from src.temporal_integrity import aligned_fraction_boundaries

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoalChainPrediction:
    """Resultado tipado de la cadena oficial candidata."""

    lambda_home: float
    lambda_away: float
    probability_home: float
    probability_draw: float
    probability_away: float
    probability_over_2_5: float
    probability_btts: float
    provenance: dict[str, Any]
    audit: dict[str, Any]


class GoalModelPort(ABC):
    """Puerto de predicción para desacoplar el router de la implementación."""

    @abstractmethod
    def predict(
        self, matches: list[dict[str, Any]], home_team_id: int,
        away_team_id: int, cutoff_ts: str,
    ) -> GoalChainPrediction:
        """Predice mercados de goles usando únicamente historia previa."""


@dataclass(slots=True)
class _FittedChain:
    """Estado interno congelado para una historia exacta."""

    state: KalmanV2State
    tau_dc: float
    history_hash: str
    dc_matches: int
    kalman_updates: int
    converged: bool
    dc_attack: dict[int, float]
    dc_defense: dict[int, float]
    dc_home_advantage: float
    dc_league_intercept: float


class DixonColesKalmanGoalModel(GoalModelPort):
    """Ajusta Dixon-Coles y actualiza fuerza reciente mediante Kalman."""

    def __init__(self, initial_fraction: float = 0.60) -> None:
        """Configura el corte fijo entre prior estructural y replay temporal."""

        if not 0.50 <= initial_fraction <= 0.90:
            raise ValueError("initial_fraction_out_of_range")
        self._initial_fraction = initial_fraction
        self._cache: dict[str, _FittedChain] = {}

    def predict(
        self, matches: list[dict[str, Any]], home_team_id: int,
        away_team_id: int, cutoff_ts: str,
    ) -> GoalChainPrediction:
        """Ejecuta la cadena causal y deriva mercados desde su matriz."""

        if home_team_id == away_team_id:
            raise ValueError("home_and_away_team_must_differ")
        frame = _frame(matches, cutoff_ts)
        fitted = self._fit_or_load(frame, cutoff_ts)
        if not fitted.converged:
            raise RuntimeError("dixon_coles_non_converged")
        if home_team_id not in fitted.state.team_ids or away_team_id not in fitted.state.team_ids:
            raise KeyError("target_team_missing_from_causal_history")
        prediction, extra = KalmanV2Filter()._predict_one(
            fitted.state, home_team_id, away_team_id, 0, -1, cutoff_ts)
        dc_home, dc_away = _dc_lambdas(
            fitted, home_team_id, away_team_id)
        home = _blend_lambda(dc_home, prediction.lambda_home)
        away = _blend_lambda(dc_away, prediction.lambda_away)
        if not np.isfinite([home, away]).all() or home <= 0.0 or away <= 0.0:
            raise FloatingPointError("invalid_blended_goal_rates")
        matrix = poisson_matrix(
            home, away, 12, fitted.tau_dc, True)
        markets = _markets(matrix)
        provenance = _provenance(fitted)
        audit = _audit(frame, cutoff_ts, matrix, fitted)
        if not all((
            audit["cutoff_causal"], audit["score_matrix_normalized"],
            audit["score_matrix_finite"], audit["probabilities_valid"],
            audit["dc_converged"],
        )):
            raise RuntimeError("official_goal_chain_integrity_gate_failed")
        return GoalChainPrediction(
            home, away,
            markets["home"], markets["draw"], markets["away"],
            markets["over_2_5"], markets["btts"], provenance, audit)

    def _fit_or_load(
        self, frame: pd.DataFrame, cutoff_ts: str,
    ) -> _FittedChain:
        """Recupera una cadena idéntica o la ajusta de forma determinista."""

        key = _history_hash(frame, cutoff_ts)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fitted = self._fit(frame, cutoff_ts, key)
        self._cache[key] = fitted
        return fitted

    def _fit(
        self, frame: pd.DataFrame, cutoff_ts: str, key: str,
    ) -> _FittedChain:
        """Ajusta el prior DC y reproduce cronológicamente la cola Kalman."""

        split = max(8, aligned_fraction_boundaries(
            frame, (self._initial_fraction,))[0])
        if split >= len(frame):
            raise ValueError("history_insufficient_for_kalman_replay")
        initial, replay = frame.iloc[:split], frame.iloc[split:]
        estimator = DixonColesEstimatorV1(_dc_config())
        model = estimator.fit(initial, team_universe=_team_ids(frame))
        state = _initial_state(model, initial, cutoff_ts)
        state = _replay(state, replay)
        return _FittedChain(
            state, float(model.tau_dc), key, len(initial), len(replay),
            bool(model.optimize_result.success), dict(model.attack),
            dict(model.defense), float(model.home_advantage),
            float(model.league_intercept))


def _dc_config() -> DixonColesConfig:
    """Devuelve la configuración congelada de la cadena v1."""

    return DixonColesConfig(max_goals_grid=12, max_iter=500, fail_on_non_convergence=True)


def _frame(
    matches: list[dict[str, Any]], cutoff_ts: str,
) -> pd.DataFrame:
    """Normaliza el histórico agregado al contrato del estimador."""

    required = ("match_id", "match_date", "home_team_id", "away_team_id",
                "home_goals", "away_goals")
    rows = [{key: item[key] for key in required} for item in matches]
    frame = pd.DataFrame(rows)
    if len(frame) < 16:
        raise ValueError("league_history_below_chain_minimum")
    frame["match_date"] = pd.to_datetime(
        frame["match_date"], utc=True, format="mixed", errors="raise")
    cutoff = pd.to_datetime(cutoff_ts, utc=True, errors="raise")
    if pd.isna(cutoff):
        raise ValueError("invalid_prediction_cutoff")
    if frame["match_id"].duplicated().any():
        raise ValueError("duplicate_match_id_in_goal_history")
    for column in (
        "match_id", "home_team_id", "away_team_id",
        "home_goals", "away_goals",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column]).all():
            raise ValueError(f"nonfinite_{column}")
        if (frame[column] % 1 != 0).any():
            raise ValueError(f"noninteger_{column}")
        frame[column] = frame[column].astype(int)
    if (frame["home_team_id"] == frame["away_team_id"]).any():
        raise ValueError("history_contains_self_match")
    if (frame[["home_team_id", "away_team_id"]] <= 0).any().any():
        raise ValueError("history_contains_invalid_team_id")
    if (frame[["home_goals", "away_goals"]] < 0).any().any():
        raise ValueError("history_contains_negative_goals")
    if not bool((frame["match_date"] < cutoff).all()):
        raise ValueError("history_not_strictly_before_cutoff")
    return frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)


def _team_ids(frame: pd.DataFrame) -> list[int]:
    """Obtiene el universo completo de equipos observado antes del cutoff."""

    home = set(frame["home_team_id"].astype(int))
    away = set(frame["away_team_id"].astype(int))
    return sorted(home | away)


def _initial_state(
    model: Any, initial: pd.DataFrame, cutoff_ts: str,
) -> KalmanV2State:
    """Inicializa Kalman desde parámetros Dixon-Coles congelados."""

    parameters = {
        "attack": model.attack, "defense": model.defense,
        "home_advantage": model.home_advantage,
        "league_intercept": model.league_intercept,
    }
    filter_ = KalmanV2Filter(KalmanV2Config())
    return filter_._initial_state_from_dc(
        list(model.team_ids), parameters,
        str(initial["match_date"].max().isoformat() or cutoff_ts))


def _replay(state: KalmanV2State, frame: pd.DataFrame) -> KalmanV2State:
    """Actualiza Kalman por kickoff sin contaminación dentro del mismo bucket."""

    filter_ = KalmanV2Filter(KalmanV2Config())
    for cutoff, bucket in frame.groupby("match_date", sort=True):
        updates = [_predict_update(filter_, state, row) for _, row in bucket.iterrows()]
        state = filter_._update_batch(state, updates)
        state.cutoff_ts = cutoff.isoformat()
    return state


def _predict_update(
    filter_: KalmanV2Filter, state: KalmanV2State, row: pd.Series,
) -> tuple[int, int, int, int, float, float]:
    """Congela una predicción antes de formar la actualización del partido."""

    home, away = int(row.home_team_id), int(row.away_team_id)
    prediction, _ = filter_._predict_one(
        state, home, away, 0, int(row.match_id), row.match_date.isoformat())
    return (
        home, away, int(row.home_goals), int(row.away_goals),
        prediction.lambda_home, prediction.lambda_away)


def _markets(matrix: np.ndarray) -> dict[str, float]:
    """Deriva mercados completos desde una matriz normalizada."""

    total_grid = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    return {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
        "over_2_5": float(matrix[total_grid > 2].sum()),
        "btts": float(matrix[1:, 1:].sum()),
    }


def _dc_lambdas(
    fitted: _FittedChain, home_team_id: int, away_team_id: int,
) -> tuple[float, float]:
    """Calcula las intensidades del prior Dixon-Coles congelado."""

    attack = fitted.dc_attack
    defense = fitted.dc_defense
    home = math.exp(
        fitted.dc_league_intercept + fitted.dc_home_advantage
        + attack[home_team_id] - defense[away_team_id])
    away = math.exp(
        fitted.dc_league_intercept + attack[away_team_id]
        - defense[home_team_id])
    return home, away


def _blend_lambda(dc_value: float, kalman_value: float) -> float:
    """Fusiona en escala log con pesos congelados por Fase 42."""

    return math.exp(0.8 * math.log(dc_value) + 0.2 * math.log(kalman_value))


def _history_hash(frame: pd.DataFrame, cutoff_ts: str) -> str:
    """Identifica exactamente la historia y cutoff usados por una cadena."""

    records = frame.assign(match_date=frame["match_date"].astype(str)).to_dict("records")
    payload = json.dumps(
        {"cutoff_ts": cutoff_ts, "matches": records},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provenance(fitted: _FittedChain) -> dict[str, Any]:
    """Declara los componentes realmente ejecutados por la predicción."""

    return {
        "model_version": "official_goal_chain_dc_kalman_v2_integrity",
        "dixon_coles_used": True, "dixon_coles_version": "dixon_coles_v1.1",
        "kalman_used": True, "kalman_version": "kalman_v2",
        "markov_used": False, "markov_status": "goal_trajectory_shadow_only",
        "hawkes_used": False, "promotion_status": "candidate_gated",
        "dixon_coles_weight": 0.8, "kalman_weight": 0.2,
        "history_hash": fitted.history_hash,
    }


def _audit(
    frame: pd.DataFrame, cutoff_ts: str, matrix: np.ndarray,
    fitted: _FittedChain,
) -> dict[str, Any]:
    """Publica invariantes causales y de normalización de la cadena."""

    cutoff = pd.to_datetime(cutoff_ts, utc=True, errors="raise")
    markets = _markets(matrix)
    probabilities = list(markets.values())
    return {
        "cutoff_causal": bool((frame["match_date"] < cutoff).all()),
        "target_match_data_used": False,
        "score_matrix_normalized": math.isclose(
            float(matrix.sum()), 1.0, abs_tol=1e-10),
        "score_matrix_finite": bool(
            np.isfinite(matrix).all() and (matrix >= 0.0).all()),
        "probabilities_valid": bool(
            np.isfinite(probabilities).all()
            and all(0.0 <= value <= 1.0 for value in probabilities)
            and math.isclose(
                markets["home"] + markets["draw"] + markets["away"],
                1.0, abs_tol=1e-10)),
        "same_kickoff_batch_safe": True,
        "dc_converged": fitted.converged,
        "dc_train_matches": fitted.dc_matches,
        "kalman_updates": fitted.kalman_updates,
    }


if __name__ == "__main__":
    LOGGER.info("Use DixonColesKalmanGoalModel desde UniversalPrematchEngine.")
    assert 0.50 <= DixonColesKalmanGoalModel()._initial_fraction <= 0.90
    assert _dc_config().fail_on_non_convergence is True
    assert _dc_config().max_goals_grid == 12

# Version: 1.0.0
# Created: 2026-07-29
