"""Implementación sintética de `kalman_v2` basada en EKF Poisson.

El módulo sigue la especificación revisada de Fase 3.11:
- estado en escala log-lambda;
- observación Poisson con aproximación local gaussiana;
- proyección suma-cero;
- mercados derivados de la matriz Poisson conjunta.

Requirements:
    - numpy
    - pandas

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import math
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from src.dixon_coles_v1 import hash_json, low_score_tau
except ModuleNotFoundError:  # pragma: no cover - fallback para ejecución directa.
    from dixon_coles_v1 import hash_json, low_score_tau

logger = None
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KalmanV2Config:
    """Configuración efectiva de `kalman_v2`."""

    model_version: str = "kalman_v2"
    state_version: str = "kalman_v2_state_v1"
    epsilon: float = 1e-6
    process_noise_attack: float = 0.05
    process_noise_defense: float = 0.05
    process_noise_home_advantage: float = 0.01
    process_noise_league_intercept: float = 0.0
    initial_variance_attack: float = 1.5
    initial_variance_defense: float = 1.5
    initial_variance_home_advantage: float = 0.75
    initial_variance_league_intercept: float = 0.0
    max_goals_grid: int = 10
    lambda_warning_low: float = 1e-6
    lambda_warning_high: float = 20.0
    lambda_error_low: float = 0.0
    lambda_error_high: float = 1000.0
    use_dixon_coles_correction: bool = False
    dixon_coles_tau: float = 0.0


@dataclass(slots=True)
class KalmanV2State:
    """Estado latente de Kalman v2."""

    team_ids: list[int]
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    league_intercept: float
    covariance: list[list[float]]
    state_version: str
    cutoff_ts: str


@dataclass(slots=True)
class KalmanV2Prediction:
    """Predicción por partido."""

    match_id: int
    fold_id: int
    home_team_id: int
    away_team_id: int
    lambda_home: float
    lambda_away: float
    expected_home_goals: float
    expected_away_goals: float
    prob_1: float
    prob_x: float
    prob_2: float
    prob_over_2_5: float
    prob_btts: float
    cutoff_ts: str


def generate_synthetic_dataset() -> pd.DataFrame:
    """Crea un dataset sintético con simultáneos, cold-start y extremos."""

    rows = [
        (1, 1, 2, "2025-01-01T12:00:00+00:00", 1, 0),
        (2, 3, 4, "2025-01-01T12:00:00+00:00", 2, 1),
        (3, 1, 3, "2025-01-08T12:00:00+00:00", 0, 0),
        (4, 2, 4, "2025-01-15T12:00:00+00:00", 1, 1),
        (5, 1, 4, "2025-01-22T12:00:00+00:00", 2, 0),
        (6, 2, 3, "2025-01-22T12:00:00+00:00", 0, 2),
        (7, 5, 1, "2025-01-29T12:00:00+00:00", 0, 2),
        (8, 5, 2, "2025-02-05T12:00:00+00:00", 1, 1),
        (9, 3, 5, "2025-02-12T12:00:00+00:00", 2, 1),
        (10, 4, 5, "2025-02-12T12:00:00+00:00", 1, 0),
    ]
    frame = pd.DataFrame(rows, columns=["match_id", "home_team_id", "away_team_id", "match_date", "home_goals", "away_goals"])
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame["feature_cutoff_ts"] = frame["match_date"]
    frame["competition_id"] = "esp.1"
    frame["season"] = "synthetic"
    frame["result_1x2"] = np.where(frame.home_goals > frame.away_goals, "1", np.where(frame.home_goals < frame.away_goals, "2", "X"))
    frame["over_2_5"] = (frame.home_goals + frame.away_goals) > 2
    frame["btts"] = (frame.home_goals > 0) & (frame.away_goals > 0)
    frame["total_goals"] = frame.home_goals + frame.away_goals
    frame["history_minimum_met"] = frame["match_id"] > 2
    frame["kalman_cold_start"] = ~frame["history_minimum_met"]
    frame["eligible_for_kalman"] = True
    return frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)


def _hash_matrix(matrix: np.ndarray) -> str:
    """Calcula hash determinista para matrices."""

    return hashlib.sha256(matrix.astype(float).round(12).tobytes()).hexdigest()


def _factorial(x: int) -> int:
    """Factorial entero estable."""

    return math.factorial(int(x))


def poisson_matrix(lambda_home: float, lambda_away: float, max_goals: int, tau: float = 0.0, enabled: bool = False) -> np.ndarray:
    """Construye una matriz Poisson conjunta normalizada."""

    if not np.isfinite(lambda_home) or not np.isfinite(lambda_away):
        raise ValueError("Las tasas deben ser finitas.")
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("Las tasas deben ser positivas.")
    if isinstance(max_goals, bool) or not isinstance(max_goals, (int, np.integer)) or max_goals < 1:
        raise ValueError("max_goals debe ser un entero positivo.")
    xs = np.arange(max_goals + 1)
    ys = np.arange(max_goals + 1)
    home_p = np.exp(-lambda_home) * np.power(lambda_home, xs) / np.array([_factorial(int(x)) for x in xs], dtype=float)
    away_p = np.exp(-lambda_away) * np.power(lambda_away, ys) / np.array([_factorial(int(y)) for y in ys], dtype=float)
    grid = np.outer(home_p, away_p)
    if enabled and tau != 0.0:
        for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
            grid[x, y] *= low_score_tau(
                x, y, lambda_home, lambda_away, tau)
    total = grid.sum()
    if not np.isfinite(total) or total <= 0:
        raise FloatingPointError("La matriz Poisson es inválida.")
    return grid / total


def _markets_from_matrix(grid: np.ndarray) -> dict[str, float]:
    """Deriva mercados desde una matriz de marcadores."""

    xs = np.arange(grid.shape[0])
    ys = np.arange(grid.shape[1])
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    p1 = float(grid[xx > yy].sum())
    px = float(grid[xx == yy].sum())
    p2 = float(grid[xx < yy].sum())
    over_25 = float(grid[(xx + yy) > 2].sum())
    btts = float(grid[(xx > 0) & (yy > 0)].sum())
    return {"prob_1": p1, "prob_x": px, "prob_2": p2, "prob_over_2_5": over_25, "prob_btts": btts}


def _softmax_disallowed(_: np.ndarray) -> None:
    """Marcador explícito para pruebas anti-softmax."""

    raise RuntimeError("softmax no permitido")


def _goal_probs_from_matrix(grid: np.ndarray) -> tuple[float, float]:
    """Deriva Over 2.5 y BTTS a partir de la matriz Poisson."""

    xs = np.arange(grid.shape[0])
    ys = np.arange(grid.shape[1])
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    over_25 = float(grid[(xx + yy) > 2].sum())
    btts = float(grid[(xx > 0) & (yy > 0)].sum())
    return over_25, btts


class KalmanV2Filter:
    """Filtro EKF Poisson con suma-cero."""

    def __init__(self, config: KalmanV2Config | None = None) -> None:
        """Inicializa el filtro."""

        self.config = config or KalmanV2Config()
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida parámetros de configuración."""

        values = [
            self.config.epsilon,
            self.config.process_noise_attack,
            self.config.process_noise_defense,
            self.config.process_noise_home_advantage,
            self.config.initial_variance_attack,
            self.config.initial_variance_defense,
            self.config.initial_variance_home_advantage,
        ]
        if any(v < 0 or not np.isfinite(v) for v in values):
            raise ValueError("Configuración inválida.")

    def _team_ids(self, frame: pd.DataFrame) -> list[int]:
        """Obtiene universo de equipos."""

        return sorted(set(frame["home_team_id"].astype(int)).union(set(frame["away_team_id"].astype(int))))

    def _state_dim(self, team_ids: list[int]) -> int:
        """Calcula dimensión del estado."""

        return 2 * len(team_ids) + 1

    def _project_sum_zero(self, mean: np.ndarray, cov: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Aplica proyección suma-cero a ataque y defensa."""

        p_team = np.eye(n) - np.ones((n, n)) / n
        p = np.block(
            [
                [p_team, np.zeros((n, n)), np.zeros((n, 1))],
                [np.zeros((n, n)), p_team, np.zeros((n, 1))],
                [np.zeros((1, n)), np.zeros((1, n)), np.eye(1)],
            ]
        )
        projected_mean = p @ mean
        projected_cov = p @ cov @ p.T
        projected_cov = 0.5 * (projected_cov + projected_cov.T)
        eig_min = np.min(np.linalg.eigvalsh(projected_cov))
        if eig_min < -1e-9:
            raise FloatingPointError("La covarianza no es PSD tras proyección.")
        return projected_mean, projected_cov

    def _init_state(self, team_ids: list[int], cutoff_ts: str) -> KalmanV2State:
        """Crea el estado inicial neutral."""

        n = len(team_ids)
        dim = self._state_dim(team_ids)
        cov = np.diag(
            [self.config.initial_variance_attack] * n
            + [self.config.initial_variance_defense] * n
            + [self.config.initial_variance_home_advantage]
        )
        mean = np.zeros(dim, dtype=float)
        mean, cov = self._project_sum_zero(mean, cov, n)
        attack = {team_id: float(mean[i]) for i, team_id in enumerate(team_ids)}
        defense = {team_id: float(mean[n + i]) for i, team_id in enumerate(team_ids)}
        return KalmanV2State(team_ids, attack, defense, float(mean[-1]), 0.0, cov.tolist(), self.config.state_version, cutoff_ts)

    def _vector_from_state(self, state: KalmanV2State) -> np.ndarray:
        """Convierte estado a vector."""

        team_ids = state.team_ids
        attack = [state.attack[team_id] for team_id in team_ids]
        defense = [state.defense[team_id] for team_id in team_ids]
        return np.array(attack + defense + [state.home_advantage], dtype=float)

    def _state_from_vector(self, state: KalmanV2State, vector: np.ndarray, cov: np.ndarray, cutoff_ts: str) -> KalmanV2State:
        """Reconstruye el estado desde vector."""

        n = len(state.team_ids)
        attack = {team_id: float(vector[i]) for i, team_id in enumerate(state.team_ids)}
        defense = {team_id: float(vector[n + i]) for i, team_id in enumerate(state.team_ids)}
        return KalmanV2State(state.team_ids, attack, defense, float(vector[-1]), state.league_intercept, cov.tolist(), state.state_version, cutoff_ts)

    def _lambda_pair(self, vector: np.ndarray, home_idx: int, away_idx: int, n: int, home_advantage: float, league_intercept: float) -> tuple[float, float]:
        """Calcula intensidades esperadas."""

        attack = vector[:n]
        defense = vector[n : 2 * n]
        eta_home = league_intercept + home_advantage + attack[home_idx] - defense[away_idx]
        eta_away = league_intercept + attack[away_idx] - defense[home_idx]
        lambda_home = math.exp(eta_home)
        lambda_away = math.exp(eta_away)
        return lambda_home, lambda_away

    def _initial_state_from_dc(
        self,
        team_ids: list[int],
        dc_parameters: dict[str, Any],
        cutoff_ts: str,
    ) -> KalmanV2State:
        """Construye el estado inicial congelado desde Dixon-Coles."""

        state = self._init_state(team_ids, cutoff_ts)
        attack = dc_parameters.get("attack", {})
        defense = dc_parameters.get("defense", {})
        state.attack = {team_id: float(attack.get(str(team_id), attack.get(team_id, 0.0))) for team_id in team_ids}
        state.defense = {team_id: float(defense.get(str(team_id), defense.get(team_id, 0.0))) for team_id in team_ids}
        state.home_advantage = float(dc_parameters.get("home_advantage", 0.0))
        state.league_intercept = float(dc_parameters.get("league_intercept", 0.0))
        vector = self._vector_from_state(state)
        mean, cov = self._project_sum_zero(vector, np.asarray(state.covariance, dtype=float), len(team_ids))
        return self._state_from_vector(state, mean, cov, cutoff_ts)

    def _jacobian(self, vector: np.ndarray, home_idx: int, away_idx: int, n: int, home_advantage: float, league_intercept: float) -> np.ndarray:
        """Calcula jacobiano de la observación Poisson."""

        lambda_home, lambda_away = self._lambda_pair(vector, home_idx, away_idx, n, home_advantage, league_intercept)
        h = np.zeros((2, 2 * n + 1), dtype=float)
        h[0, home_idx] = lambda_home
        h[0, n + away_idx] = -lambda_home
        h[0, 2 * n] = lambda_home
        h[1, away_idx] = lambda_away
        h[1, n + home_idx] = -lambda_away
        return h

    def _validate_lambdas(self, lambda_home: float, lambda_away: float) -> None:
        """Valida tasas antes de usar la predicción."""

        for value in (lambda_home, lambda_away):
            if not np.isfinite(value) or value <= self.config.lambda_error_low or value > self.config.lambda_error_high:
                raise ValueError("Lambda inválida.")

    def _predict_one(self, state: KalmanV2State, home_team_id: int, away_team_id: int, fold_id: int, match_id: int, cutoff_ts: str) -> tuple[KalmanV2Prediction, dict[str, Any]]:
        """Calcula predicción y matriz de mercados para un partido."""

        n = len(state.team_ids)
        home_idx = state.team_ids.index(home_team_id)
        away_idx = state.team_ids.index(away_team_id)
        vector = self._vector_from_state(state)
        lambda_home, lambda_away = self._lambda_pair(vector, home_idx, away_idx, n, state.home_advantage, state.league_intercept)
        self._validate_lambdas(lambda_home, lambda_away)
        if lambda_home < self.config.lambda_warning_low or lambda_away < self.config.lambda_warning_low or lambda_home > self.config.lambda_warning_high or lambda_away > self.config.lambda_warning_high:
            pass
        matrix = poisson_matrix(lambda_home, lambda_away, self.config.max_goals_grid, self.config.dixon_coles_tau, self.config.use_dixon_coles_correction)
        markets = _markets_from_matrix(matrix)
        pred = KalmanV2Prediction(
            match_id=match_id,
            fold_id=fold_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            expected_home_goals=lambda_home,
            expected_away_goals=lambda_away,
            prob_1=markets["prob_1"],
            prob_x=markets["prob_x"],
            prob_2=markets["prob_2"],
            prob_over_2_5=markets["prob_over_2_5"],
            prob_btts=markets["prob_btts"],
            cutoff_ts=cutoff_ts,
        )
        return pred, {"matrix": matrix, "lambda_home": lambda_home, "lambda_away": lambda_away}

    def _prediction_metrics(self, pred: KalmanV2Prediction, row: pd.Series) -> dict[str, float]:
        """Calcula métricas observacionales para un partido."""

        outcome = str(row.result_1x2)
        probs = {"1": pred.prob_1, "X": pred.prob_x, "2": pred.prob_2}
        log_loss = -math.log(max(1e-15, probs[outcome]))
        brier = sum((probs[k] - float(outcome == k)) ** 2 for k in probs)
        calibration = abs(sum(probs.values()) - 1.0)
        over_25 = -math.log(max(1e-15, pred.prob_over_2_5 if bool(row.over_2_5) else 1.0 - pred.prob_over_2_5))
        btts = -math.log(max(1e-15, pred.prob_btts if bool(row.btts) else 1.0 - pred.prob_btts))
        mae = abs(pred.expected_home_goals + pred.expected_away_goals - float(row.total_goals))
        home_goals, away_goals = int(row.home_goals), int(row.away_goals)
        score_grid = poisson_matrix(
            pred.lambda_home,
            pred.lambda_away,
            max(self.config.max_goals_grid, home_goals, away_goals),
            self.config.dixon_coles_tau,
            self.config.use_dixon_coles_correction,
        )
        goal_log_score = -math.log(max(
            1e-15, float(score_grid[home_goals, away_goals])))
        return {
            "log_loss_1x2": log_loss,
            "brier_1x2": brier,
            "calibration_1x2": calibration,
            "log_score_goals": goal_log_score,
            "mae_goals": mae,
            "over_2_5": over_25,
            "btts": btts,
        }

    def _fold_metrics(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        """Agrega métricas por fold."""

        if not rows:
            return {}
        totals = {k: 0.0 for k in ["log_loss_1x2", "brier_1x2", "calibration_1x2", "log_score_goals", "mae_goals", "over_2_5", "btts"]}
        for row in rows:
            for key in totals:
                totals[key] += float(row[key])
        return {key: value / len(rows) for key, value in totals.items()}

    def _load_common_protocol(self, protocol_path: Path) -> dict[str, Any]:
        """Carga el protocolo temporal común."""

        return json.loads(protocol_path.read_text(encoding="utf-8"))

    def _load_dc_fold_parameters(self, dc_fold_parameters_path: Path) -> list[dict[str, Any]]:
        """Carga parámetros congelados de Dixon-Coles por fold."""

        return json.loads(dc_fold_parameters_path.read_text(encoding="utf-8"))

    def run_real_dry_run(
        self,
        baseline_manifest_path: Path,
        candidate_path: Path,
        protocol_path: Path,
        dc_fold_parameters_path: Path,
        dc_common_evaluation_path: Path,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Ejecuta la evaluación real de `kalman_v2` con folds comunes."""

        from src.kalman_v1 import load_real_baseline

        frame, manifest = load_real_baseline(baseline_manifest_path, candidate_path)
        trainable = frame[frame["eligible_for_training"]].copy().sort_values(["match_date", "match_id"]).reset_index(drop=True)
        if len(trainable) != 331:
            raise RuntimeError(f"Se esperaban 331 filas entrenables y se obtuvieron {len(trainable)}.")
        if int(trainable["match_id"].duplicated().sum()) != 0:
            raise RuntimeError("Hay duplicados en match_id.")
        if "esp.1" not in set(trainable["competition_id"].astype(str)):
            raise RuntimeError("La competencia canónica no es esp.1.")
        if int((trainable["match_id"] == 704766).sum()) != 0:
            raise RuntimeError("704766 no debe estar en el universo entrenable.")
        if trainable["feature_cutoff_ts"].gt(trainable["match_date"]).any():
            raise RuntimeError("Hay violaciones temporales.")
        protocol = self._load_common_protocol(protocol_path)
        folds = protocol["folds"]
        dc_parameters = self._load_dc_fold_parameters(dc_fold_parameters_path)
        if not dc_parameters:
            raise RuntimeError("No se encontraron parámetros congelados de Dixon-Coles.")
        frozen_dc = dc_parameters[0]
        team_ids = sorted(set(trainable["home_team_id"].astype(int)).union(set(trainable["away_team_id"].astype(int))))
        fold_payloads: list[dict[str, Any]] = []
        all_predictions: list[dict[str, Any]] = []
        all_states: list[dict[str, Any]] = []
        all_audits: list[dict[str, Any]] = []
        for fold in folds:
            fold_id = int(fold["fold_id"])
            state = self._initial_state_from_dc(team_ids, frozen_dc, str(fold["train_cutoff_ts"]))
            train_df = trainable[trainable["match_id"].isin(fold["train_ids"])].copy().sort_values(["match_date", "match_id"])
            valid_df = trainable[trainable["match_id"].isin(fold["validation_ids"])].copy().sort_values(["match_date", "match_id"])
            if train_df.empty or valid_df.empty:
                raise RuntimeError("Fold temporal vacío.")
            if set(train_df["match_date"]) & set(valid_df["match_date"]):
                raise RuntimeError("Un kickoff cruza train y validación.")
            if train_df["match_date"].max() >= valid_df["match_date"].min():
                raise RuntimeError("El fold no respeta el orden temporal.")
            # Replay causal: todos los partidos del mismo kickoff se predicen
            # antes de incorporar cualquiera de sus resultados.
            for cutoff, bucket in train_df.groupby("match_date", sort=True):
                batch_updates: list[tuple[int, int, int, int, float, float]] = []
                for _, row in bucket.iterrows():
                    _, extra = self._predict_one(
                        state,
                        int(row.home_team_id),
                        int(row.away_team_id),
                        fold_id,
                        int(row.match_id),
                        str(cutoff.isoformat()),
                    )
                    batch_updates.append((
                        int(row.home_team_id), int(row.away_team_id),
                        int(row.home_goals), int(row.away_goals),
                        float(extra["lambda_home"]),
                        float(extra["lambda_away"]),
                    ))
                state = self._update_batch(state, batch_updates)
                state.cutoff_ts = str(cutoff.isoformat())
            fold_predictions: list[dict[str, Any]] = []
            fold_audits: list[dict[str, Any]] = []
            for cutoff, bucket in valid_df.groupby("match_date", sort=True):
                state_before = json.loads(json.dumps(asdict(state), default=_json_default))
                batch_predictions: list[KalmanV2Prediction] = []
                batch_updates: list[tuple[int, int, int, int, float, float]] = []
                for _, row in bucket.sort_values("match_id").iterrows():
                    pred, extra = self._predict_one(state, int(row.home_team_id), int(row.away_team_id), fold_id, int(row.match_id), cutoff.isoformat())
                    metrics = self._prediction_metrics(pred, row)
                    payload = {**asdict(pred), **metrics}
                    batch_predictions.append(pred)
                    fold_predictions.append(payload)
                    all_predictions.append(payload)
                    all_audits.append({
                        "fold_id": fold_id,
                        "match_id": int(row.match_id),
                        "lambda_home": extra["lambda_home"],
                        "lambda_away": extra["lambda_away"],
                        "r_diag": [extra["lambda_home"] + self.config.epsilon, extra["lambda_away"] + self.config.epsilon],
                        "jacobian": self._jacobian(self._vector_from_state(state), state.team_ids.index(int(row.home_team_id)), state.team_ids.index(int(row.away_team_id)), len(state.team_ids), state.home_advantage, state.league_intercept).tolist(),
                    })
                    batch_updates.append((int(row.home_team_id), int(row.away_team_id), int(row.home_goals), int(row.away_goals), extra["lambda_home"], extra["lambda_away"]))
                state = self._update_batch(state, batch_updates)
                state.cutoff_ts = cutoff.isoformat()
                fold_audits.append({"cutoff_ts": cutoff.isoformat(), "state_before": state_before, "state_after": json.loads(json.dumps(asdict(state), default=_json_default))})
            fold_payloads.append(
                {
                    "fold_id": fold_id,
                    "train_count": len(train_df),
                    "validation_count": len(valid_df),
                    "state_version": state.state_version,
                    "state_before": json.loads(json.dumps(asdict(self._initial_state_from_dc(team_ids, frozen_dc, str(fold["train_cutoff_ts"]))), default=_json_default)),
                    "state_after": json.loads(json.dumps(asdict(state), default=_json_default)),
                    "metrics": self._fold_metrics(fold_predictions),
                    "audit": fold_audits,
                }
            )
            all_states.append({"fold_id": fold_id, "state": json.loads(json.dumps(asdict(state), default=_json_default))})
        aggregate = self._fold_metrics(all_predictions)
        artefacts = {
            "config": output_dir / "kalman_v2_effective_config.json",
            "manifest": output_dir / "kalman_v2_input_manifest.json",
            "states": output_dir / "kalman_v2_states_by_fold.json",
            "predictions": output_dir / "kalman_v2_predictions.json",
            "metrics": output_dir / "kalman_v2_metrics.json",
            "comparisons": output_dir / "kalman_v2_comparisons.json",
            "diagnostics": output_dir / "kalman_v2_diagnostics.json",
            "hashes": output_dir / "kalman_v2_hashes.json",
            "report": output_dir / "kalman_v2_report.md",
            "result": output_dir / "kalman_v2_result.json",
            "audit": output_dir / "kalman_v2_math_audit.json",
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(artefacts["config"], asdict(self.config))
        write_json(artefacts["manifest"], protocol)
        write_json(artefacts["states"], all_states)
        write_json(artefacts["predictions"], all_predictions)
        write_json(artefacts["metrics"], aggregate)
        dc_common_file = dc_common_evaluation_path
        if dc_common_file.is_dir():
            dc_common_file = dc_common_file / "evaluation_metrics_v1.json"
        dc_common = json.loads(dc_common_file.read_text(encoding="utf-8"))
        write_json(artefacts["comparisons"], {
            "dixon_coles_v1": dc_common["dixon_coles_v1"],
            "kalman_v2": aggregate,
        })
        write_json(artefacts["diagnostics"], {"folds": fold_payloads, "coverage": protocol["coverage"]})
        write_json(artefacts["audit"], all_audits)
        result = {
            "model_version": "kalman_v2",
            "dataset_hash": hash_json(trainable.to_dict(orient="records")),
            "config_hash": hash_json(asdict(self.config)),
            "inputs_hash": protocol["dataset_hash"],
            "state_hash": hash_json(all_states),
            "predictions_hash": hash_json(all_predictions),
            "metrics_hash": hash_json(aggregate),
            "coverage": {
                "row_count": int(len(frame)),
                "trainable_count": int(len(trainable)),
                "excluded_match_ids": [704766],
                "competition_ids": sorted(set(trainable["competition_id"].astype(str))),
            },
            "folds": fold_payloads,
            "aggregate_metrics": aggregate,
            "comparisons": {
                "dixon_coles_v1": dc_common["dixon_coles_v1"],
                "poisson_simple": dc_common["poisson_simple"],
                "league_mean": dc_common["league_mean"],
            },
            "math_audit": all_audits,
        }
        write_json(artefacts["hashes"], {k: result[k] for k in ["dataset_hash", "config_hash", "inputs_hash", "state_hash", "predictions_hash", "metrics_hash"]})
        write_json(artefacts["result"], result)
        artefacts["report"].write_text(
            "\n".join(
                [
                    "# Kalman v2 Real Dry-Run",
                    f"- trainable_count: {result['coverage']['trainable_count']}",
                    f"- folds: {len(fold_payloads)}",
                    f"- predictions: {len(all_predictions)}",
                    f"- calibration_warning: low statistical power per fold",
                ]
            ),
            encoding="utf-8",
        )
        result["artefacts"] = {k: str(v) for k, v in artefacts.items()}
        return result

    def _update(self, state: KalmanV2State, home_team_id: int, away_team_id: int, home_goals: int, away_goals: int, lambda_home: float, lambda_away: float) -> KalmanV2State:
        """Actualiza el estado con EKF y proyecta identificabilidad."""

        return self._update_batch(state, [(
            home_team_id, away_team_id, home_goals, away_goals,
            lambda_home, lambda_away,
        )])

    def _update_batch(
        self,
        state: KalmanV2State,
        updates: list[tuple[int, int, int, int, float, float]],
    ) -> KalmanV2State:
        """Aplica simultáneamente las observaciones de un mismo kickoff."""

        if not updates:
            return state

        n = len(state.team_ids)
        vector = self._vector_from_state(state)
        cov = np.asarray(state.covariance, dtype=float)
        jacobians: list[np.ndarray] = []
        observations: list[float] = []
        means: list[float] = []
        variances: list[float] = []
        canonical = sorted(updates, key=lambda item: tuple(item))
        for home_team_id, away_team_id, home_goals, away_goals, lambda_home, lambda_away in canonical:
            self._validate_lambdas(lambda_home, lambda_away)
            if home_goals < 0 or away_goals < 0:
                raise ValueError("Los goles observados no pueden ser negativos.")
            home_idx = state.team_ids.index(home_team_id)
            away_idx = state.team_ids.index(away_team_id)
            jacobians.append(self._jacobian(
                vector, home_idx, away_idx, n,
                state.home_advantage, state.league_intercept))
            observations.extend((float(home_goals), float(away_goals)))
            means.extend((float(lambda_home), float(lambda_away)))
            variances.extend((
                float(lambda_home + self.config.epsilon),
                float(lambda_away + self.config.epsilon),
            ))
        h = np.vstack(jacobians)
        r = np.diag(variances)
        y = np.asarray(observations, dtype=float)
        mu = np.asarray(means, dtype=float)
        s = h @ cov @ h.T + r
        k = cov @ h.T @ np.linalg.pinv(s)
        innovation = y - mu
        updated = vector + k @ innovation
        identity_minus_kh = np.eye(cov.shape[0]) - k @ h
        updated_cov = (
            identity_minus_kh @ cov @ identity_minus_kh.T + k @ r @ k.T)
        updated_cov = 0.5 * (updated_cov + updated_cov.T)
        updated, updated_cov = self._project_sum_zero(updated, updated_cov, n)
        return self._state_from_vector(state, updated, updated_cov, state.cutoff_ts)

    def fit_predict(self, frame: pd.DataFrame) -> dict[str, Any]:
        """Ejecuta el filtro sobre un dataset sintético."""

        required = {"match_id", "home_team_id", "away_team_id", "match_date", "home_goals", "away_goals"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Faltan columnas: {sorted(missing)}")
        prepared = frame.copy()
        prepared["match_date"] = pd.to_datetime(prepared["match_date"], utc=True)
        prepared = prepared.sort_values(["match_date", "match_id"]).reset_index(drop=True)
        team_ids = self._team_ids(prepared)
        state = self._init_state(team_ids, prepared["match_date"].iloc[0].isoformat())
        predictions: list[dict[str, Any]] = []
        states: list[dict[str, Any]] = []
        matrices: list[dict[str, Any]] = []
        for cutoff, bucket in prepared.groupby("match_date", sort=True):
            state_before = json.loads(json.dumps(asdict(state), default=_json_default))
            batch_predictions: list[KalmanV2Prediction] = []
            batch_updates: list[tuple[int, int, int, int, float, float]] = []
            for _, row in bucket.sort_values("match_id").iterrows():
                pred, extra = self._predict_one(state, int(row.home_team_id), int(row.away_team_id), 0, int(row.match_id), cutoff.isoformat())
                batch_predictions.append(pred)
                matrices.append({"match_id": int(row.match_id), "fold_id": 0, "matrix": extra["matrix"].tolist()})
                batch_updates.append((int(row.home_team_id), int(row.away_team_id), int(row.home_goals), int(row.away_goals), extra["lambda_home"], extra["lambda_away"]))
            for idx, pred in enumerate(batch_predictions):
                predictions.append(asdict(pred))
            state = self._update_batch(state, batch_updates)
            state.cutoff_ts = cutoff.isoformat()
            states.append({"cutoff_ts": cutoff.isoformat(), "state_before": state_before, "state_after": json.loads(json.dumps(asdict(state), default=_json_default))})
        result = {
            "state": asdict(state),
            "predictions": predictions,
            "states_by_date": states,
            "matrices": matrices,
            "dataset_hash": hash_json(prepared.to_dict(orient="records")),
            "config_hash": hash_json(asdict(self.config)),
            "state_hash": hash_json(asdict(state)),
            "predictions_hash": hash_json(predictions),
            "matrices_hash": hash_json(matrices),
        }
        return result


def _json_default(value: Any) -> Any:
    """Convierte tipos no serializables a JSON."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return str(value)


def build_manifest(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Construye manifiesto reproducible."""

    return {
        "model_version": "kalman_v2",
        "state_version": payload["state"]["state_version"],
        "dataset_hash": payload["dataset_hash"],
        "config_hash": payload["config_hash"],
        "state_hash": payload["state_hash"],
        "predictions_hash": payload["predictions_hash"],
        "matrices_hash": payload["matrices_hash"],
        "artifact_dir": str(output_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_json(path: Path, payload: Any) -> None:
    """Escribe JSON determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default), encoding="utf-8")


def run_synthetic_kalman_v2(output_dir: Path) -> dict[str, Any]:
    """Ejecuta el flujo sintético de Kalman v2."""

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = generate_synthetic_dataset()
    config = KalmanV2Config()
    filter_ = KalmanV2Filter(config)
    result = filter_.fit_predict(frame)
    metrics = {
        "predictions": len(result["predictions"]),
        "states": len(result["states_by_date"]),
        "matrices": len(result["matrices"]),
        "probabilities_valid": all(
            0.0 <= row["prob_1"] <= 1.0
            and 0.0 <= row["prob_x"] <= 1.0
            and 0.0 <= row["prob_2"] <= 1.0
            and abs((row["prob_1"] + row["prob_x"] + row["prob_2"]) - 1.0) < 1e-6
            for row in result["predictions"]
        ),
    }
    artefacts = {
        "dataset": output_dir / "kalman_v2_dataset.json",
        "config": output_dir / "kalman_v2_config.json",
        "states": output_dir / "kalman_v2_states_by_date.json",
        "predictions": output_dir / "kalman_v2_predictions.json",
        "matrices": output_dir / "kalman_v2_matrices.json",
        "metrics": output_dir / "kalman_v2_metrics.json",
        "manifest": output_dir / "kalman_v2_manifest.json",
        "hashes": output_dir / "kalman_v2_hashes.json",
        "report": output_dir / "kalman_v2_report.md",
        "result": output_dir / "kalman_v2_result.json",
    }
    write_json(artefacts["dataset"], frame.to_dict(orient="records"))
    write_json(artefacts["config"], asdict(config))
    write_json(artefacts["states"], result["states_by_date"])
    write_json(artefacts["predictions"], result["predictions"])
    write_json(artefacts["matrices"], result["matrices"])
    write_json(artefacts["metrics"], metrics)
    write_json(artefacts["manifest"], build_manifest(output_dir, result))
    write_json(artefacts["hashes"], {k: result[k] for k in ["dataset_hash", "config_hash", "state_hash", "predictions_hash", "matrices_hash"]})
    write_json(artefacts["result"], {**result, "metrics": metrics})
    artefacts["report"].write_text(
        "\n".join(
            [
                "# Kalman v2 Synthetic Report",
                f"- predictions: {len(result['predictions'])}",
                f"- states: {len(result['states_by_date'])}",
                f"- matrices: {len(result['matrices'])}",
                f"- probabilities_valid: {metrics['probabilities_valid']}",
            ]
        ),
        encoding="utf-8",
    )
    return {"payload": result, "artefacts": {k: str(v) for k, v in artefacts.items()}}


if __name__ == "__main__":
    out = Path("artifacts/phase_3_12_kalman_v2_synthetic")
    result = run_synthetic_kalman_v2(out)
    assert result["payload"]["predictions_hash"]
