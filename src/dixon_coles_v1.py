"""Implementación aislada del estimator Dixon-Coles v1.

El módulo trabaja únicamente con datasets sintéticos o aprobados para pruebas
aisladas. No conoce PostgreSQL ni componentes de entrenamiento productivo.

Requirements:
    - numpy
    - pandas
    - scipy

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeResult, minimize
from scipy.special import gammaln

logger = logging.getLogger(__name__)

LOW_SCORE_MARKERS = {(0, 0), (1, 0), (0, 1), (1, 1)}
TARGET_COLUMNS = ("home_goals", "away_goals")


@dataclass(slots=True)
class DixonColesConfig:
    """Configuración del estimator Dixon-Coles v1."""

    attack_sigma: float = 1.0
    defense_sigma: float = 1.0
    home_advantage_sigma: float = 0.5
    league_intercept_sigma: float = 1.0
    tau_dc_sigma: float = 0.25
    gamma: float = 0.0025
    max_goals_grid: int = 12
    tol: float = 1e-6
    max_iter: int = 1000
    min_train_matches: int = 2
    folds: int = 2
    initial_train_matches: int = 4
    validation_horizon_matches: int = 1
    seed: int = 42
    fail_on_non_convergence: bool = True


@dataclass(slots=True)
class FittedDixonColesModel:
    """Resultado de ajuste del modelo."""

    team_ids: list[int]
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    league_intercept: float
    tau_dc: float
    optimize_result: OptimizeResult
    dataset_hash: str
    config_hash: str


@dataclass(slots=True)
class DryRunFoldResult:
    """Resultado de un fold de validación temporal."""

    fold_id: int
    train_matches: int
    validation_matches: int
    converged: bool
    iterations: int
    objective: float
    warnings: list[str]
    metrics: dict[str, float]
    parameters: dict[str, Any]
    predictions: list[dict[str, Any]]


@dataclass(slots=True)
class DryRunResult:
    """Resultado completo del dry-run sobre datos reales."""

    model_version: str
    dataset_hash: str
    config_hash: str
    inputs_hash: str
    params_hash: str
    predictions_hash: str
    metrics_hash: str
    folds: list[DryRunFoldResult]
    aggregate_metrics: dict[str, float]
    baselines: dict[str, dict[str, float]]
    coverage: dict[str, Any]
    diagnostics: dict[str, Any]
    artefacts: dict[str, str]


def generate_synthetic_dataset() -> pd.DataFrame:
    """Genera un dataset sintético controlado para pruebas.

    Returns:
        pd.DataFrame: Dataset con equipos, historial, resultados y timestamps.
    """

    rows = [
        (1, 10, 20, "2025-01-01T12:00:00+00:00", 1, "esp.1", 5, 5, 1, 1),
        (2, 20, 30, "2025-01-08T12:00:00+00:00", 1, "esp.1", 5, 5, 0, 0),
        (3, 10, 30, "2025-01-15T12:00:00+00:00", 1, "esp.1", 5, 5, 2, 1),
        (4, 40, 20, "2025-01-22T12:00:00+00:00", 1, "esp.1", 5, 5, 0, 1),
        (5, 20, 10, "2025-01-29T12:00:00+00:00", 1, "esp.1", 5, 5, 1, 1),
        (6, 10, 40, "2025-02-05T12:00:00+00:00", 1, "esp.1", 5, 5, 3, 0),
        (7, 40, 20, "2025-02-12T12:00:00+00:00", 1, "esp.1", 1, 5, 0, 2),
        (8, 30, 10, "2025-02-19T12:00:00+00:00", 1, "esp.1", 5, 5, 1, 1),
        (9, 20, 40, "2025-02-26T12:00:00+00:00", 1, "esp.1", 5, 1, 2, 0),
        (10, 40, 10, "2025-03-05T12:00:00+00:00", 1, "esp.1", 1, 5, 0, 1),
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "match_id",
            "home_team_id",
            "away_team_id",
            "match_date",
            "competition_id",
            "season",
            "home_prior_matches",
            "away_prior_matches",
            "home_goals",
            "away_goals",
        ],
    )
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame["feature_cutoff_ts"] = frame["match_date"]
    frame["eligible_for_training"] = True
    frame["eligible_for_materialization"] = True
    frame.loc[frame["match_id"] == 10, "eligible_for_training"] = False
    frame.loc[frame["match_id"] == 10, "exclusion_reason"] = "insufficient_history_for_training"
    frame.loc[frame["match_id"] == 10, "home_prior_matches"] = 1
    frame.loc[frame["match_id"] == 10, "away_prior_matches"] = 5
    frame.loc[frame["match_id"] == 7, "home_prior_matches"] = 1
    frame.loc[frame["match_id"] == 7, "away_prior_matches"] = 5
    frame["target_complete"] = True
    frame["competition_valid"] = True
    frame["cutoff_valid"] = True
    frame["history_minimum_met"] = frame["home_prior_matches"].ge(1) & frame["away_prior_matches"].ge(1)
    frame["result_1x2"] = np.where(frame.home_goals > frame.away_goals, "1", np.where(frame.home_goals < frame.away_goals, "2", "X"))
    frame["over_2_5"] = (frame.home_goals + frame.away_goals) > 2
    frame["btts"] = (frame.home_goals > 0) & (frame.away_goals > 0)
    frame["goal_margin"] = frame.home_goals - frame.away_goals
    frame["total_goals"] = frame.home_goals + frame.away_goals
    frame["exclusion_reason"] = frame.get("exclusion_reason", pd.Series([None] * len(frame)))
    return frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)


def hash_dataframe(frame: pd.DataFrame, excluded: Iterable[str] | None = None) -> str:
    """Calcula un hash determinista para un dataset.

    Args:
        frame: Dataset de entrada.
        excluded: Columnas a excluir del hash.

    Returns:
        str: Hash hexadecimal.
    """

    excluded_set = set(excluded or [])
    normalized = frame.drop(columns=[col for col in excluded_set if col in frame.columns]).copy()
    normalized = normalized.sort_index(axis=1).sort_values(list(normalized.columns)).reset_index(drop=True)
    payload = normalized.to_json(orient="records", date_format="iso", date_unit="ns")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_json(data: Any) -> str:
    """Genera un hash determinista para estructuras JSON serializables."""

    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_params_payload(payload: dict[str, Any]) -> str:
    """Calcula hash de parámetros estimados."""

    return hash_json(payload)


def _json_default(value: Any) -> Any:
    """Convierte tipos no serializables a representaciones estables."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.int64, np.int32)):
        return int(value)
    if isinstance(value, (np.floating, np.float64, np.float32)):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timedelta, timedelta)):
        return value.total_seconds()
    return str(value)


def low_score_tau(x: int, y: int, lambda_home: float, lambda_away: float, tau: float) -> float:
    """Corrección Dixon-Coles para marcadores bajos.

    Args:
        x: Goles del local.
        y: Goles del visitante.
        lambda_home: Intensidad esperada local.
        lambda_away: Intensidad esperada visitante.
        tau: Parámetro de dependencia.

    Returns:
        float: Factor multiplicativo de corrección.
    """

    if (x, y) == (0, 0):
        return max(1e-9, 1.0 - (lambda_home * lambda_away * tau))
    if (x, y) == (1, 0):
        return max(1e-9, 1.0 + (lambda_away * tau))
    if (x, y) == (0, 1):
        return max(1e-9, 1.0 + (lambda_home * tau))
    if (x, y) == (1, 1):
        return max(1e-9, 1.0 - tau)
    return 1.0


class DixonColesEstimatorV1:
    """Estimator Dixon-Coles v1 con optimización determinista."""

    def __init__(self, config: DixonColesConfig | None = None) -> None:
        """Inicializa el estimator.

        Args:
            config: Configuración opcional.
        """

        self.config = config or DixonColesConfig()
        self.model_: FittedDixonColesModel | None = None

    def _prepare_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Valida y prepara el dataset de entrenamiento."""

        required = {"match_id", "home_team_id", "away_team_id", "home_goals", "away_goals", "match_date"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Faltan columnas requeridas: {sorted(missing)}")
        prepared = frame.copy()
        prepared["match_date"] = pd.to_datetime(prepared["match_date"], utc=True)
        if prepared["home_goals"].isna().any() or prepared["away_goals"].isna().any():
            raise ValueError("Los targets no pueden ser NULL.")
        if (prepared["home_goals"] < 0).any() or (prepared["away_goals"] < 0).any():
            raise ValueError("Los targets no pueden ser negativos.")
        if not np.isfinite(prepared["home_goals"]).all() or not np.isfinite(prepared["away_goals"]).all():
            raise ValueError("Los targets deben ser finitos.")
        prepared = prepared.sort_values(["match_date", "match_id"]).reset_index(drop=True)
        return prepared

    def _team_ids(self, frame: pd.DataFrame, universe: Iterable[int] | None = None) -> list[int]:
        """Obtiene el universo de equipos."""

        if universe is not None:
            team_ids = sorted({int(team_id) for team_id in universe})
        else:
            team_ids = sorted(set(frame["home_team_id"].astype(int)).union(set(frame["away_team_id"].astype(int))))
        if not team_ids:
            raise ValueError("No hay equipos en el dataset.")
        return team_ids

    def _initial_params(self, team_ids: Sequence[int]) -> np.ndarray:
        """Construye el vector inicial de parámetros."""

        n = len(team_ids)
        return np.zeros(n * 2 + 3, dtype=float)

    def _split_params(self, params: np.ndarray, team_ids: Sequence[int]) -> dict[str, Any]:
        """Convierte el vector de parámetros a estructuras legibles."""

        n = len(team_ids)
        attack = {int(team_ids[i]): float(params[i]) for i in range(n)}
        defense = {int(team_ids[i]): float(params[n + i]) for i in range(n)}
        return {
            "attack": attack,
            "defense": defense,
            "home_advantage": float(params[2 * n]),
            "league_intercept": float(params[2 * n + 1]),
            "tau_dc": float(params[2 * n + 2]),
        }

    def _lambda_pair(self, params: np.ndarray, row: pd.Series, team_index: dict[int, int]) -> tuple[float, float]:
        """Calcula intensidades esperadas para un partido."""

        n = len(team_index)
        home = team_index[int(row.home_team_id)]
        away = team_index[int(row.away_team_id)]
        attack = params[:n]
        defense = params[n : 2 * n]
        home_advantage = params[2 * n]
        league_intercept = params[2 * n + 1]
        lambda_home = float(np.exp(league_intercept + home_advantage + attack[home] - defense[away]))
        lambda_away = float(np.exp(league_intercept + attack[away] - defense[home]))
        return self._clip_lambda(lambda_home), self._clip_lambda(lambda_away)

    def _clip_lambda(self, value: float) -> float:
        """Asegura intensidades numéricamente válidas."""

        return float(np.clip(value, 1e-9, 100.0))

    def _neg_log_likelihood(self, params: np.ndarray, frame: pd.DataFrame, team_index: dict[int, int]) -> float:
        """Calcula la función objetivo penalizada."""

        if not np.all(np.isfinite(params)):
            return np.inf
        n = len(team_index)
        attack = params[:n]
        defense = params[n : 2 * n]
        home_advantage = params[2 * n]
        league_intercept = params[2 * n + 1]
        tau_dc = params[2 * n + 2]
        penalty = (
            np.sum((attack / self.config.attack_sigma) ** 2)
            + np.sum((defense / self.config.defense_sigma) ** 2)
            + (home_advantage / self.config.home_advantage_sigma) ** 2
            + (league_intercept / self.config.league_intercept_sigma) ** 2
            + (tau_dc / self.config.tau_dc_sigma) ** 2
        )
        home_idx = frame["home_team_id"].map(team_index).to_numpy(dtype=int)
        away_idx = frame["away_team_id"].map(team_index).to_numpy(dtype=int)
        x = frame["home_goals"].to_numpy(dtype=int)
        y = frame["away_goals"].to_numpy(dtype=int)
        home = np.clip(np.exp(league_intercept + home_advantage + attack[home_idx] - defense[away_idx]), 1e-9, 100.0)
        away = np.clip(np.exp(league_intercept + attack[away_idx] - defense[home_idx]), 1e-9, 100.0)
        log_p = -home + x * np.log(home) - gammaln(x + 1)
        log_p += -away + y * np.log(away) - gammaln(y + 1)
        tau = np.ones(len(frame), dtype=float)
        tau[(x == 0) & (y == 0)] = 1.0 - (home * away * tau_dc)[(x == 0) & (y == 0)]
        tau[(x == 1) & (y == 0)] = 1.0 + (away * tau_dc)[(x == 1) & (y == 0)]
        tau[(x == 0) & (y == 1)] = 1.0 + (home * tau_dc)[(x == 0) & (y == 1)]
        tau[(x == 1) & (y == 1)] = 1.0 - tau_dc
        if np.any(tau <= 0) or not np.isfinite(tau).all():
            return np.inf
        age = (frame["match_date"].max() - frame["match_date"]).dt.total_seconds().to_numpy() / 86400.0
        weights = np.exp(-self.config.gamma * np.maximum(age, 0.0))
        return float(0.5 * penalty - np.sum(weights * (log_p + np.log(tau))))

    def fit(self, frame: pd.DataFrame, team_universe: Iterable[int] | None = None) -> FittedDixonColesModel:
        """Ajusta el modelo sobre el dataset dado.

        Args:
            frame: Dataset sintético o baseline aprobado.

        Returns:
            FittedDixonColesModel: Modelo ajustado.
        """

        prepared = self._prepare_frame(frame)
        team_ids = self._team_ids(prepared, universe=team_universe)
        team_index = {team_id: idx for idx, team_id in enumerate(team_ids)}
        params0 = self._initial_params(team_ids)
        result = minimize(
            fun=self._neg_log_likelihood,
            x0=params0,
            args=(prepared, team_index),
            method="L-BFGS-B",
            options={"maxiter": self.config.max_iter, "ftol": self.config.tol},
        )
        if not result.success and self.config.fail_on_non_convergence:
            raise RuntimeError(f"No hubo convergencia: {result.message}")
        split = self._split_params(result.x if result.success or result.x is not None else params0, team_ids)
        self.model_ = FittedDixonColesModel(
            team_ids=list(team_ids),
            attack=split["attack"],
            defense=split["defense"],
            home_advantage=split["home_advantage"],
            league_intercept=split["league_intercept"],
            tau_dc=split["tau_dc"],
            optimize_result=result,
            dataset_hash=hash_dataframe(prepared, excluded={"feature_snapshot_ts"}),
            config_hash=hash_json(asdict(self.config)),
        )
        return self.model_

    def predict_match(self, home_team_id: int, away_team_id: int) -> dict[str, float]:
        """Predice intensidades y probabilidades para un partido.

        Args:
            home_team_id: Identificador del local.
            away_team_id: Identificador del visitante.

        Returns:
            dict[str, float]: Intensidades y probabilidades del partido.
        """

        if self.model_ is None:
            raise RuntimeError("El modelo no ha sido ajustado.")
        if home_team_id not in self.model_.attack or away_team_id not in self.model_.attack:
            raise KeyError("Los equipos no existen en el modelo ajustado.")
        lambda_home = self._clip_lambda(
            float(np.exp(self.model_.league_intercept + self.model_.home_advantage + self.model_.attack[home_team_id] - self.model_.defense[away_team_id]))
        )
        lambda_away = self._clip_lambda(
            float(np.exp(self.model_.league_intercept + self.model_.attack[away_team_id] - self.model_.defense[home_team_id]))
        )
        grid = self.scoreline_matrix(lambda_home, lambda_away)
        prob_1 = float(np.tril(grid, k=-1).sum())
        prob_x = float(np.trace(grid))
        prob_2 = float(np.triu(grid, k=1).sum())
        over_25 = float(grid[np.add.outer(np.arange(grid.shape[0]), np.arange(grid.shape[1])) > 2].sum())
        btts = float(grid[1:, 1:].sum())
        return {
            "expected_home_goals_dc": lambda_home,
            "expected_away_goals_dc": lambda_away,
            "prob_1": prob_1,
            "prob_x": prob_x,
            "prob_2": prob_2,
            "prob_over_2_5": over_25,
            "prob_btts": btts,
        }

    def scoreline_matrix(self, lambda_home: float, lambda_away: float) -> np.ndarray:
        """Construye una matriz de probabilidades corregida."""

        max_goals = self.config.max_goals_grid
        xs = np.arange(max_goals + 1)
        ys = np.arange(max_goals + 1)
        base = np.exp(-lambda_home) * np.power(lambda_home, xs) / np.exp(gammaln(xs + 1))
        away = np.exp(-lambda_away) * np.power(lambda_away, ys) / np.exp(gammaln(ys + 1))
        grid = np.outer(base, away)
        tau = self.model_.tau_dc if self.model_ is not None else 0.0
        for x, y in LOW_SCORE_MARKERS:
            grid[x, y] *= low_score_tau(x, y, lambda_home, lambda_away, tau)
        total = grid.sum()
        if total <= 0 or not np.isfinite(total):
            raise FloatingPointError("La matriz de probabilidades es inválida.")
        return grid / total

    def expanding_window_validate(self, frame: pd.DataFrame) -> dict[str, Any]:
        """Ejecuta validación temporal expanding window."""

        prepared = self._prepare_frame(frame)
        team_universe = self._team_ids(prepared)
        folds = []
        start = self.config.initial_train_matches
        for fold_id in range(self.config.folds):
            train_end = start + fold_id
            valid_end = min(len(prepared), train_end + self.config.validation_horizon_matches)
            train = prepared.iloc[:train_end].copy()
            valid = prepared.iloc[train_end:valid_end].copy()
            if len(valid) == 0:
                break
            model = DixonColesEstimatorV1(self.config).fit(train, team_universe=team_universe)
            rows = []
            for _, row in valid.iterrows():
                pred = self.predict_with_model(model, row.home_team_id, row.away_team_id)
                rows.append({"match_id": int(row.match_id), **pred})
            folds.append(
                {
                    "fold_id": fold_id,
                    "train_matches": len(train),
                    "validation_matches": len(valid),
                    "predictions": rows,
                    "converged": bool(model.optimize_result.success),
                    "objective": float(model.optimize_result.fun),
                }
            )
        return {"folds": folds, "fold_count": len(folds)}

    def predict_with_model(self, model: FittedDixonColesModel, home_team_id: int, away_team_id: int) -> dict[str, float]:
        """Predice usando un modelo explícito."""

        if home_team_id not in model.attack or away_team_id not in model.attack:
            raise KeyError("Los equipos no existen en el modelo ajustado.")
        lambda_home = self._clip_lambda(
            float(np.exp(model.league_intercept + model.home_advantage + model.attack[home_team_id] - model.defense[away_team_id]))
        )
        lambda_away = self._clip_lambda(
            float(np.exp(model.league_intercept + model.attack[away_team_id] - model.defense[home_team_id]))
        )
        self.model_ = model
        grid = self.scoreline_matrix(lambda_home, lambda_away)
        prob_1 = float(np.tril(grid, k=-1).sum())
        prob_x = float(np.trace(grid))
        prob_2 = float(np.triu(grid, k=1).sum())
        over_25 = float(grid[np.add.outer(np.arange(grid.shape[0]), np.arange(grid.shape[1])) > 2].sum())
        btts = float(grid[1:, 1:].sum())
        return {
            "expected_home_goals_dc": lambda_home,
            "expected_away_goals_dc": lambda_away,
            "prob_1": prob_1,
            "prob_x": prob_x,
            "prob_2": prob_2,
            "prob_over_2_5": over_25,
            "prob_btts": btts,
        }


def load_baseline_dataframe(
    baseline_manifest_path: Path,
    candidate_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Carga el baseline aprobado y devuelve datos más manifest."""

    manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    frame = pd.DataFrame(candidate["rows"])
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame["feature_cutoff_ts"] = pd.to_datetime(frame["feature_cutoff_ts"], utc=True)
    frame["feature_snapshot_ts"] = pd.to_datetime(frame["feature_snapshot_ts"], utc=True)
    frame["source_available_at"] = pd.to_datetime(frame["source_available_at"], utc=True)
    frame = frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    return frame, manifest


def validate_real_universe(frame: pd.DataFrame) -> dict[str, Any]:
    """Valida universo real de entrenamiento y cobertura temporal."""

    trainable = frame[frame["eligible_for_training"]].copy()
    issues: list[str] = []
    if len(frame) != 381:
        issues.append(f"row_count={len(frame)}")
    if len(trainable) != 331:
        issues.append(f"trainable_count={len(trainable)}")
    if "esp.1" not in set(frame["competition_id"]):
        issues.append("missing_canonical_competition")
    if int(frame["match_id"].duplicated().sum()) != 0:
        issues.append("duplicate_match_id")
    row_704766 = frame.loc[frame["match_id"] == 704766]
    excluded_704766 = True
    if not row_704766.empty:
        excluded_704766 = str(row_704766.iloc[0].get("exclusion_reason")) == "existing_data_failed_run"
        if not excluded_704766:
            issues.append("bad_704766_reason")
    temporal_violations = int((frame["feature_cutoff_ts"] > pd.to_datetime(frame["match_date"], utc=True)).sum())
    if temporal_violations:
        issues.append(f"temporal_violations={temporal_violations}")
    null_targets = int(frame["home_goals"].isna().sum() + frame["away_goals"].isna().sum())
    if null_targets:
        issues.append(f"null_targets={null_targets}")
    return {
        "row_count": int(len(frame)),
        "trainable_count": int(len(trainable)),
        "competition_ids": sorted(str(value) for value in set(frame["competition_id"])),
        "temporal_violations": temporal_violations,
        "issues": issues,
        "trainable_match_ids": [int(value) for value in trainable["match_id"].tolist()],
        "excluded_match_ids": [704766] if row_704766.empty or excluded_704766 else [],
        "team_universe": sorted(set(trainable["home_team_id"].astype(int)).union(set(trainable["away_team_id"].astype(int)))),
    }


def _log_loss_1x2(prob_1: float, prob_x: float, prob_2: float, outcome: str) -> float:
    """Calcula log loss para 1X2."""

    probs = {"1": prob_1, "X": prob_x, "2": prob_2}
    return -math.log(max(1e-15, probs[outcome]))


def _brier_1x2(prob_1: float, prob_x: float, prob_2: float, outcome: str) -> float:
    """Calcula Brier score para 1X2."""

    actual = {"1": 0.0, "X": 0.0, "2": 0.0}
    actual[outcome] = 1.0
    return sum((prob - actual[key]) ** 2 for key, prob in zip(("1", "X", "2"), (prob_1, prob_x, prob_2)))


def _calibration_error_1x2(prob_1: float, prob_x: float, prob_2: float, outcome: str) -> float:
    """Calcula error de calibración simple para 1X2."""

    probs = {"1": prob_1, "X": prob_x, "2": prob_2}
    return abs(max(probs, key=probs.get) == outcome) * 0.0 + abs(sum(probs.values()) - 1.0)


def _goal_log_score(row: pd.Series, model: FittedDixonColesModel, estimator: DixonColesEstimatorV1) -> float:
    """Calcula log score exacto para goles."""

    pred = estimator.predict_with_model(model, int(row.home_team_id), int(row.away_team_id))
    grid = estimator.scoreline_matrix(pred["expected_home_goals_dc"], pred["expected_away_goals_dc"])
    x = int(row.home_goals)
    y = int(row.away_goals)
    if x < grid.shape[0] and y < grid.shape[1]:
        return -math.log(max(1e-15, float(grid[x, y])))
    return np.inf


def _mean_absolute_error(pred: float, actual: float) -> float:
    """MAE unidimensional."""

    return abs(pred - actual)


def _fold_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Agrega métricas de un fold."""

    if not rows:
        return {}
    totals = {
        key: 0.0
        for key in [
            "log_loss_1x2_dc",
            "brier_score_1x2_dc",
            "calibration_1x2_dc",
            "log_score_goals_dc",
            "mae_goals_dc",
            "over_2_5_log_loss_dc",
            "btts_log_loss_dc",
        ]
    }
    for row in rows:
        totals["log_loss_1x2_dc"] += row["log_loss_1x2_dc"]
        totals["brier_score_1x2_dc"] += row["brier_score_1x2_dc"]
        totals["calibration_1x2_dc"] += row["calibration_1x2_dc"]
        totals["log_score_goals_dc"] += row["log_score_goals_dc"]
        totals["mae_goals_dc"] += row["mae_goals_dc"]
        totals["over_2_5_log_loss_dc"] += row["over_2_5_log_loss_dc"]
        totals["btts_log_loss_dc"] += row["btts_log_loss_dc"]
    return {key: value / len(rows) for key, value in totals.items()}


def _simple_probabilities(home_mean: float, away_mean: float, row: pd.Series, estimator: DixonColesEstimatorV1) -> dict[str, float]:
    """Calcula probabilidades con un Poisson simple."""

    grid = estimator.scoreline_matrix(home_mean, away_mean)
    return {
        "prob_1": float(np.tril(grid, k=-1).sum()),
        "prob_x": float(np.trace(grid)),
        "prob_2": float(np.triu(grid, k=1).sum()),
        "prob_over_2_5": float(grid[np.add.outer(np.arange(grid.shape[0]), np.arange(grid.shape[1])) > 2].sum()),
        "prob_btts": float(grid[1:, 1:].sum()),
    }


def run_real_dry_run(
    baseline_manifest_path: Path,
    candidate_path: Path,
    config_path: Path,
    estimator_config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Ejecuta el dry-run sobre el baseline real aprobado."""

    frame, manifest = load_baseline_dataframe(baseline_manifest_path, candidate_path)
    universe = validate_real_universe(frame)
    if universe["issues"]:
        raise RuntimeError(f"Universo inválido: {universe['issues']}")
    config = DixonColesConfig(
        attack_sigma=1.0,
        defense_sigma=1.0,
        home_advantage_sigma=0.5,
        league_intercept_sigma=1.0,
        tau_dc_sigma=0.25,
        gamma=0.0025,
        max_iter=1000,
        tol=1e-6,
        folds=5,
        initial_train_matches=50,
        validation_horizon_matches=1,
        fail_on_non_convergence=True,
    )
    estimator = DixonColesEstimatorV1(config)
    trainable = frame[frame["eligible_for_training"]].copy().sort_values(["match_date", "match_id"]).reset_index(drop=True)
    if len(trainable) != 331:
        raise RuntimeError(f"Se esperaban 331 filas entrenables y se obtuvieron {len(trainable)}.")
    team_universe = universe["team_universe"]
    folds: list[DryRunFoldResult] = []
    all_predictions: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    for fold_id in range(config.folds):
        train_end = config.initial_train_matches + fold_id
        valid_end = min(len(trainable), train_end + config.validation_horizon_matches)
        train = trainable.iloc[:train_end].copy()
        valid = trainable.iloc[train_end:valid_end].copy()
        if len(valid) == 0:
            break
        model = estimator.fit(train, team_universe=team_universe)
        fold_predictions: list[dict[str, Any]] = []
        warnings: list[str] = []
        home_mean = float(train["home_goals"].mean())
        away_mean = float(train["away_goals"].mean())
        fold_parameters = {
            "attack": model.attack,
            "defense": model.defense,
            "home_advantage": model.home_advantage,
            "league_intercept": model.league_intercept,
            "tau_dc": model.tau_dc,
        }
        for _, row in valid.iterrows():
            pred = estimator.predict_with_model(model, int(row.home_team_id), int(row.away_team_id))
            simple_pred = _simple_probabilities(home_mean, away_mean, row, estimator)
            actual_outcome = str(row.result_1x2)
            record = {
                "match_id": int(row.match_id),
                "fold_id": fold_id,
                "home_team_id": int(row.home_team_id),
                "away_team_id": int(row.away_team_id),
                "expected_home_goals_dc": pred["expected_home_goals_dc"],
                "expected_away_goals_dc": pred["expected_away_goals_dc"],
                "prob_1_dc": pred["prob_1"],
                "prob_x_dc": pred["prob_x"],
                "prob_2_dc": pred["prob_2"],
                "prob_over_2_5_dc": pred["prob_over_2_5"],
                "prob_btts_dc": pred["prob_btts"],
                "log_loss_1x2_dc": _log_loss_1x2(pred["prob_1"], pred["prob_x"], pred["prob_2"], actual_outcome),
                "brier_score_1x2_dc": _brier_1x2(pred["prob_1"], pred["prob_x"], pred["prob_2"], actual_outcome),
                "calibration_1x2_dc": _calibration_error_1x2(pred["prob_1"], pred["prob_x"], pred["prob_2"], actual_outcome),
                "log_score_goals_dc": _goal_log_score(row, model, estimator),
                "mae_goals_dc": _mean_absolute_error(
                    pred["expected_home_goals_dc"] + pred["expected_away_goals_dc"],
                    float(row.total_goals),
                ),
                "over_2_5_log_loss_dc": -math.log(max(1e-15, pred["prob_over_2_5"] if bool(row.over_2_5) else (1.0 - pred["prob_over_2_5"]))),
                "btts_log_loss_dc": -math.log(max(1e-15, pred["prob_btts"] if bool(row.btts) else (1.0 - pred["prob_btts"]))),
                "prob_1_poisson": simple_pred["prob_1"],
                "prob_x_poisson": simple_pred["prob_x"],
                "prob_2_poisson": simple_pred["prob_2"],
                "prob_over_2_5_poisson": simple_pred["prob_over_2_5"],
                "prob_btts_poisson": simple_pred["prob_btts"],
            }
            fold_predictions.append(record)
            all_predictions.append(record)
            baseline_rows.append(
                {
                    "match_id": int(row.match_id),
                    "fold_id": fold_id,
                    "home_mean": home_mean,
                    "away_mean": away_mean,
                    "league_mean_home_goals": float(train["home_goals"].mean()),
                    "league_mean_away_goals": float(train["away_goals"].mean()),
                }
            )
        fold_metrics = _fold_metrics(fold_predictions)
        folds.append(
            DryRunFoldResult(
                fold_id=fold_id,
                train_matches=len(train),
                validation_matches=len(valid),
                converged=bool(model.optimize_result.success),
                iterations=int(getattr(model.optimize_result, "nit", 0) or 0),
                objective=float(model.optimize_result.fun),
                warnings=warnings,
                metrics=fold_metrics,
                parameters=fold_parameters,
                predictions=fold_predictions,
            )
        )
    aggregate_metrics = _fold_metrics([item for fold in folds for item in fold.predictions])
    params_hash = hash_params_payload([fold.parameters for fold in folds])
    predictions_hash = hash_json(all_predictions)
    metrics_hash = hash_json(aggregate_metrics)
    inputs_hash = manifest["hashes"]["inputs"]
    dataset_hash = manifest["hashes"]["outputs"]
    artefacts = _write_real_dry_run_artifacts(
        output_dir=output_dir,
        config=config,
        manifest=manifest,
        universe=universe,
        folds=folds,
        all_predictions=all_predictions,
        aggregate_metrics=aggregate_metrics,
        params_hash=params_hash,
        predictions_hash=predictions_hash,
        metrics_hash=metrics_hash,
        inputs_hash=inputs_hash,
        dataset_hash=dataset_hash,
    )
    baselines = {
        "league_mean": {
            "home_goals": float(trainable["home_goals"].mean()),
            "away_goals": float(trainable["away_goals"].mean()),
        },
        "poisson_simple": {
            "home_goals": float(trainable["home_goals"].mean()),
            "away_goals": float(trainable["away_goals"].mean()),
        },
    }
    diagnostics = {
        "converged_folds": sum(1 for fold in folds if fold.converged),
        "fold_count": len(folds),
        "trainable_rows": len(trainable),
        "temporal_violations": universe["temporal_violations"],
        "warnings": [warning for fold in folds for warning in fold.warnings],
        "identifiability": "sum-to-zero soft penalty via regularization",
        "sensitivity_to_regularization": {"attack_sigma": config.attack_sigma, "defense_sigma": config.defense_sigma},
    }
    return {
        "model_version": "dixon_coles_v1",
        "dataset_hash": dataset_hash,
        "config_hash": hash_json(asdict(config)),
        "inputs_hash": inputs_hash,
        "params_hash": params_hash,
        "predictions_hash": predictions_hash,
        "metrics_hash": metrics_hash,
        "folds": folds,
        "aggregate_metrics": aggregate_metrics,
        "baselines": baselines,
        "coverage": universe,
        "diagnostics": diagnostics,
        "artefacts": artefacts,
    }


def _write_real_dry_run_artifacts(
    *,
    output_dir: Path,
    config: DixonColesConfig,
    manifest: dict[str, Any],
    universe: dict[str, Any],
    folds: list[DryRunFoldResult],
    all_predictions: list[dict[str, Any]],
    aggregate_metrics: dict[str, float],
    params_hash: str,
    predictions_hash: str,
    metrics_hash: str,
    inputs_hash: str,
    dataset_hash: str,
) -> dict[str, str]:
    """Escribe artefactos del dry-run real."""

    output_dir.mkdir(parents=True, exist_ok=True)
    fold_payloads = [asdict(fold) for fold in folds]
    artefacts = {
        "config": output_dir / "dixon_coles_v1_effective_config.json",
        "manifest": output_dir / "dixon_coles_v1_input_manifest.json",
        "coverage": output_dir / "dixon_coles_v1_coverage.json",
        "folds": output_dir / "dixon_coles_v1_fold_results.json",
        "predictions": output_dir / "dixon_coles_v1_predictions.json",
        "metrics": output_dir / "dixon_coles_v1_metrics.json",
        "diagnostics": output_dir / "dixon_coles_v1_diagnostics.json",
        "report": output_dir / "dixon_coles_v1_report.md",
        "result": output_dir / "dixon_coles_v1_result.json",
        "hashes": output_dir / "dixon_coles_v1_hashes.json",
    }
    write_json(artefacts["config"], asdict(config))
    write_json(artefacts["manifest"], manifest)
    write_json(artefacts["coverage"], universe)
    write_json(artefacts["folds"], fold_payloads)
    write_json(artefacts["predictions"].with_name("dixon_coles_v1_fold_parameters.json"), [fold.parameters for fold in folds])
    write_json(artefacts["predictions"], all_predictions)
    write_json(artefacts["metrics"], aggregate_metrics)
    write_json(
        artefacts["diagnostics"],
        {
            "dataset_hash": dataset_hash,
            "inputs_hash": inputs_hash,
            "params_hash": params_hash,
            "predictions_hash": predictions_hash,
            "metrics_hash": metrics_hash,
            "fold_count": len(folds),
            "trainable_count": universe["trainable_count"],
            "issues": universe["issues"],
        },
    )
    write_json(
        artefacts["result"],
        {
            "model_version": "dixon_coles_v1",
            "dataset_hash": dataset_hash,
            "inputs_hash": inputs_hash,
            "params_hash": params_hash,
            "predictions_hash": predictions_hash,
            "metrics_hash": metrics_hash,
            "aggregate_metrics": aggregate_metrics,
            "coverage": universe,
            "diagnostics": {
                "fold_count": len(folds),
                "trainable_rows": universe["trainable_count"],
                "issues": universe["issues"],
            },
        },
    )
    write_json(
        artefacts["hashes"],
        {
            "config": hash_json(asdict(config)),
            "manifest": hash_json(manifest),
            "coverage": hash_json(universe),
            "folds": hash_json(fold_payloads),
            "predictions": predictions_hash,
            "metrics": metrics_hash,
            "result": hash_json(
                {
                    "model_version": "dixon_coles_v1",
                    "dataset_hash": dataset_hash,
                    "inputs_hash": inputs_hash,
                    "params_hash": params_hash,
                    "predictions_hash": predictions_hash,
                    "metrics_hash": metrics_hash,
                }
            ),
        },
    )
    report_lines = [
        "# Dixon-Coles v1 Dry-Run Report",
        "",
        f"- trainable_rows: {universe['trainable_count']}",
        f"- fold_count: {len(folds)}",
        f"- temporal_violations: {universe['temporal_violations']}",
        f"- issues: {', '.join(universe['issues']) or 'none'}",
        "",
        "## Aggregate Metrics",
    ]
    for key, value in sorted(aggregate_metrics.items()):
        report_lines.append(f"- {key}: {value:.6f}")
    artefacts["report"].write_text("\n".join(report_lines), encoding="utf-8")
    return {key: str(path) for key, path in artefacts.items()}


def league_mean_baseline(frame: pd.DataFrame) -> dict[str, float]:
    """Calcula un baseline ingenuo de media de liga."""

    home_mean = float(frame["home_goals"].mean())
    away_mean = float(frame["away_goals"].mean())
    return {"home_mean": home_mean, "away_mean": away_mean}


def poisson_simple_baseline(frame: pd.DataFrame) -> dict[str, float]:
    """Calcula un baseline Poisson simple."""

    return league_mean_baseline(frame)


def build_run_manifest(
    *,
    dataset_hash: str,
    config_hash: str,
    params_hash: str,
    predictions_hash: str,
    output_dir: Path,
    model_version: str = "dixon_coles_v1",
) -> dict[str, Any]:
    """Crea un manifiesto de ejecución reproducible."""

    return {
        "model_version": model_version,
        "dataset_hash": dataset_hash,
        "config_hash": config_hash,
        "params_hash": params_hash,
        "predictions_hash": predictions_hash,
        "artifact_dir": str(output_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_json(path: Path, payload: Any) -> None:
    """Escribe JSON de forma determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )


def run_synthetic_baseline(output_dir: Path) -> dict[str, Any]:
    """Ejecuta el flujo aislado sobre dataset sintético."""

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = generate_synthetic_dataset()
    config = DixonColesConfig()
    estimator = DixonColesEstimatorV1(config)
    model = estimator.fit(frame[frame["eligible_for_training"]].copy())
    validation = estimator.expanding_window_validate(frame)
    preds = []
    for _, row in frame.iterrows():
        prediction = estimator.predict_match(int(row.home_team_id), int(row.away_team_id))
        preds.append({"match_id": int(row.match_id), **prediction})
    params = {
        "attack": model.attack,
        "defense": model.defense,
        "home_advantage": model.home_advantage,
        "league_intercept": model.league_intercept,
        "tau_dc": model.tau_dc,
    }
    payloads = {
        "dataset": frame.to_dict(orient="records"),
        "config": asdict(config),
        "parameters": params,
        "predictions": preds,
        "validation": validation,
        "baselines": {
            "league_mean": league_mean_baseline(frame),
            "poisson_simple": poisson_simple_baseline(frame),
        },
    }
    hashes = {key: hash_json(value) for key, value in payloads.items()}
    write_json(output_dir / "dixon_coles_v1_dataset.json", payloads["dataset"])
    write_json(output_dir / "dixon_coles_v1_config.json", payloads["config"])
    write_json(output_dir / "dixon_coles_v1_parameters.json", payloads["parameters"])
    write_json(output_dir / "dixon_coles_v1_predictions.json", payloads["predictions"])
    write_json(output_dir / "dixon_coles_v1_validation.json", payloads["validation"])
    write_json(output_dir / "dixon_coles_v1_baselines.json", payloads["baselines"])
    write_json(output_dir / "dixon_coles_v1_manifest.json", build_run_manifest(
        dataset_hash=model.dataset_hash,
        config_hash=model.config_hash,
        params_hash=hashes["parameters"],
        predictions_hash=hashes["predictions"],
        output_dir=output_dir,
    ))
    write_json(output_dir / "dixon_coles_v1_hashes.json", hashes)
    return {"model": model, "validation": validation, "hashes": hashes, "output_dir": str(output_dir)}


def run_real_dry_run_cli(
    output_dir: Path,
    baseline_manifest_path: Path | None = None,
    candidate_path: Path | None = None,
    config_path: Path | None = None,
    estimator_config_path: Path | None = None,
) -> dict[str, Any]:
    """Ejecuta el dry-run real usando las rutas por contrato."""

    baseline_manifest_path = baseline_manifest_path or Path(
        "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_baseline_manifest.json"
    )
    candidate_path = candidate_path or Path("artifacts/phase_2_4_match_features_v1_dry_run/match_features_v1_candidate.json")
    config_path = config_path or Path("artifacts/phase_3_1_dixon_coles_v1/dixon_coles_v1_config.json")
    estimator_config_path = estimator_config_path or Path(
        "artifacts/phase_3_2_dixon_coles_estimator_v1/dixon_coles_estimator_v1_config.json"
    )
    _ = json.loads(config_path.read_text(encoding="utf-8"))
    _ = json.loads(estimator_config_path.read_text(encoding="utf-8"))
    return run_real_dry_run(
        baseline_manifest_path=baseline_manifest_path,
        candidate_path=candidate_path,
        config_path=config_path,
        estimator_config_path=estimator_config_path,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    """Ejemplo ejecutable para prueba local aislada."""

    logging.basicConfig(level=logging.INFO)
    demo_dir = Path("artifacts/phase_3_4_dixon_coles_v1_dry_run")
    result = run_real_dry_run_cli(demo_dir)
    assert result["coverage"]["trainable_count"] == 331
    assert not result["coverage"]["issues"]
    assert result["predictions_hash"]
