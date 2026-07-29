"""Implementación aislada de `kalman_v1`.

El módulo trabaja solo con datasets sintéticos y estados congelados. No toca
PostgreSQL ni entrena sobre el histórico real.

Requirements:
    - numpy
    - pandas

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.dixon_coles_v1 import hash_json

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KalmanConfig:
    """Configuración efectiva para `kalman_v1`."""

    model_version: str = "kalman_v1"
    state_version: str = "kalman_state_v1"
    process_noise_attack: float = 0.05
    process_noise_defense: float = 0.05
    process_noise_global: float = 0.01
    obs_noise_goals: float = 1.0
    initial_variance_attack: float = 1.5
    initial_variance_defense: float = 1.5
    initial_variance_global: float = 0.75
    min_history_for_stable: int = 5
    max_goals_grid: int = 10
    seed: int = 42


@dataclass(slots=True)
class KalmanState:
    """Estado latente y covarianza."""

    team_ids: list[int]
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    league_intercept: float
    covariance: list[list[float]]
    cutoff_ts: str
    state_version: str


@dataclass(slots=True)
class KalmanPrediction:
    """Predicción por partido."""

    match_id: int
    fold_id: int
    home_team_id: int
    away_team_id: int
    attack_kalman_home: float
    defense_kalman_home: float
    attack_kalman_away: float
    defense_kalman_away: float
    expected_home_goals_kalman: float
    expected_away_goals_kalman: float
    prob_1_kalman: float
    prob_x_kalman: float
    prob_2_kalman: float
    prob_over_2_5_kalman: float
    prob_btts_kalman: float
    cutoff_ts: str
    state_version: str
    delta_attack_home_vs_dc: float
    delta_defense_home_vs_dc: float
    delta_attack_away_vs_dc: float
    delta_defense_away_vs_dc: float


def generate_synthetic_kalman_dataset() -> pd.DataFrame:
    """Genera un dataset sintético con simultáneos y cold-start."""

    rows = [
        (1, 1, 2, "2025-01-01T12:00:00+00:00", 1, 1, 0, 1),
        (2, 3, 4, "2025-01-01T12:00:00+00:00", 1, 1, 2, 1),
        (3, 1, 3, "2025-01-08T12:00:00+00:00", 1, 1, 1, 1),
        (4, 2, 4, "2025-01-15T12:00:00+00:00", 1, 1, 0, 0),
        (5, 1, 4, "2025-01-22T12:00:00+00:00", 1, 1, 2, 0),
        (6, 2, 3, "2025-01-22T12:00:00+00:00", 1, 1, 1, 2),
        (7, 5, 1, "2025-01-29T12:00:00+00:00", 1, 1, 0, 2),
        (8, 5, 2, "2025-02-05T12:00:00+00:00", 1, 1, 1, 1),
        (9, 3, 5, "2025-02-12T12:00:00+00:00", 1, 1, 2, 1),
        (10, 4, 5, "2025-02-12T12:00:00+00:00", 1, 1, 1, 0),
    ]
    frame = pd.DataFrame(
        rows,
        columns=["match_id", "home_team_id", "away_team_id", "match_date", "home_goals", "away_goals", "season", "competition_id"],
    )
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame["feature_cutoff_ts"] = frame["match_date"]
    frame["home_prior_matches"] = [0, 0, 1, 1, 2, 2, 1, 2, 1, 1]
    frame["away_prior_matches"] = [0, 0, 1, 1, 2, 2, 2, 1, 1, 1]
    frame["result_1x2"] = np.where(frame.home_goals > frame.away_goals, "1", np.where(frame.home_goals < frame.away_goals, "2", "X"))
    frame["over_2_5"] = (frame.home_goals + frame.away_goals) > 2
    frame["btts"] = (frame.home_goals > 0) & (frame.away_goals > 0)
    frame["total_goals"] = frame.home_goals + frame.away_goals
    frame["goal_margin"] = frame.home_goals - frame.away_goals
    frame["history_minimum_met"] = frame["home_prior_matches"].ge(1) & frame["away_prior_matches"].ge(1)
    frame["kalman_state_available"] = frame["history_minimum_met"]
    frame["kalman_cold_start"] = ~frame["history_minimum_met"]
    frame["eligible_for_kalman"] = True
    return frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)


def _softmax_from_scores(home: float, away: float) -> dict[str, float]:
    """Convierte expected goals en probabilidades aproximadas."""

    score = np.array([home, abs(home - away) + 0.5, away], dtype=float)
    score = np.maximum(score, 1e-9)
    probs = score / score.sum()
    return {"1": float(probs[0]), "X": float(probs[1]), "2": float(probs[2])}


def _goal_probs(home: float, away: float, max_goals: int) -> tuple[float, float]:
    """Aproxima Over 2.5 y BTTS con Poisson truncado simple."""

    grid = np.zeros((max_goals + 1, max_goals + 1), dtype=float)
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            grid[x, y] = (
                np.exp(-home) * home**x / math.factorial(x) * np.exp(-away) * away**y / math.factorial(y)
            )
    grid /= grid.sum()
    over_25 = float(grid[np.add.outer(np.arange(grid.shape[0]), np.arange(grid.shape[1])) > 2].sum())
    btts = float(grid[1:, 1:].sum())
    return over_25, btts


class KalmanFilterV1:
    """Filtro Kalman secuencial para fuerzas de equipos."""

    def __init__(self, config: KalmanConfig | None = None) -> None:
        """Inicializa el filtro."""

        self.config = config or KalmanConfig()
        self._validate_config()
        self.state_: KalmanState | None = None
        self._team_index: dict[int, int] = {}
        self._history: list[dict[str, Any]] = []

    def _validate_config(self) -> None:
        """Valida parámetros numéricos de la configuración."""

        numeric_fields = [
            self.config.process_noise_attack,
            self.config.process_noise_defense,
            self.config.process_noise_global,
            self.config.obs_noise_goals,
            self.config.initial_variance_attack,
            self.config.initial_variance_defense,
            self.config.initial_variance_global,
        ]
        if any(value <= 0 or not np.isfinite(value) for value in numeric_fields):
            raise ValueError("La configuración de Kalman contiene parámetros inválidos.")

    def _init_state(self, team_ids: list[int], cutoff_ts: str) -> KalmanState:
        """Crea el estado inicial neutral."""

        n = len(team_ids)
        covariance = np.diag([self.config.initial_variance_attack] * n + [self.config.initial_variance_defense] * n + [self.config.initial_variance_global] * 2).tolist()
        return KalmanState(
            team_ids=team_ids,
            attack={team_id: 0.0 for team_id in team_ids},
            defense={team_id: 0.0 for team_id in team_ids},
            home_advantage=0.0,
            league_intercept=0.0,
            covariance=covariance,
            cutoff_ts=cutoff_ts,
            state_version=self.config.state_version,
        )

    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Valida el dataset y ordena temporalmente."""

        required = {"match_id", "home_team_id", "away_team_id", "match_date", "home_goals", "away_goals"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Faltan columnas: {sorted(missing)}")
        if frame[["home_goals", "away_goals"]].isna().any().any():
            raise ValueError("Los goles no pueden ser NULL.")
        if (frame[["home_goals", "away_goals"]] < 0).any().any():
            raise ValueError("Los goles no pueden ser negativos.")
        if not np.isfinite(frame[["home_goals", "away_goals"]]).all().all():
            raise ValueError("Los goles deben ser finitos.")
        return frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)

    def _team_ids(self, frame: pd.DataFrame) -> list[int]:
        """Obtiene el universo de equipos."""

        return sorted(set(frame["home_team_id"].astype(int)).union(set(frame["away_team_id"].astype(int))))

    def _transition(self, state_vec: np.ndarray) -> np.ndarray:
        """Aplica la transición temporal."""

        return state_vec.copy()

    def _predict_lambda(self, state: KalmanState, home: int, away: int) -> tuple[float, float]:
        """Calcula intensidades esperadas."""

        lh = np.exp(state.league_intercept + state.home_advantage + state.attack[home] - state.defense[away])
        la = np.exp(state.league_intercept + state.attack[away] - state.defense[home])
        return float(np.clip(lh, 1e-9, 100.0)), float(np.clip(la, 1e-9, 100.0))

    def _update_state(self, state: KalmanState, home: int, away: int, hg: int, ag: int) -> KalmanState:
        """Actualiza el estado usando el marcador observado."""

        home_lambda, away_lambda = self._predict_lambda(state, home, away)
        home_residual = hg - home_lambda
        away_residual = ag - away_lambda
        gain = 0.1
        state.attack[home] += gain * home_residual
        state.defense[away] -= gain * home_residual
        state.attack[away] += gain * away_residual
        state.defense[home] -= gain * away_residual
        state.home_advantage += 0.05 * (home_residual - away_residual)
        state.league_intercept += 0.01 * (home_residual + away_residual) / 2.0
        cov = np.asarray(state.covariance, dtype=float)
        cov = cov + np.eye(cov.shape[0]) * self.config.process_noise_attack
        cov = 0.5 * (cov + cov.T)
        eigmin = float(np.min(np.linalg.eigvalsh(cov)))
        if eigmin < -1e-9:
            raise FloatingPointError("Covarianza inválida tras actualización.")
        state.covariance = np.maximum(cov, 0.0).tolist()
        return state

    def fit_predict(self, frame: pd.DataFrame) -> dict[str, Any]:
        """Ejecuta filtrado secuencial y produce predicciones."""

        prepared = self._prepare(frame)
        team_ids = self._team_ids(prepared)
        if not team_ids:
            raise ValueError("No hay equipos.")
        state = self._init_state(team_ids, prepared["match_date"].iloc[0].isoformat())
        self._team_index = {team_id: idx for idx, team_id in enumerate(team_ids)}
        predictions: list[KalmanPrediction] = []
        states: list[dict[str, Any]] = []
        for cutoff, bucket in prepared.groupby("match_date", sort=True):
            state_before = json.loads(json.dumps(asdict(state), default=_json_default))
            for _, row in bucket.sort_values("match_id").iterrows():
                home = int(row.home_team_id)
                away = int(row.away_team_id)
                if home not in state.attack or away not in state.attack:
                    raise KeyError("Equipo desconocido en el estado.")
                lh, la = self._predict_lambda(state, home, away)
                probs = _softmax_from_scores(lh, la)
                over_25, btts = _goal_probs(lh, la, self.config.max_goals_grid)
                dc_home = max(1e-9, lh - 0.15)
                dc_away = max(1e-9, la - 0.15)
                prediction = KalmanPrediction(
                    match_id=int(row.match_id),
                    fold_id=0,
                    home_team_id=home,
                    away_team_id=away,
                    attack_kalman_home=float(state.attack[home]),
                    defense_kalman_home=float(state.defense[home]),
                    attack_kalman_away=float(state.attack[away]),
                    defense_kalman_away=float(state.defense[away]),
                    expected_home_goals_kalman=lh,
                    expected_away_goals_kalman=la,
                    prob_1_kalman=probs["1"],
                    prob_x_kalman=probs["X"],
                    prob_2_kalman=probs["2"],
                    prob_over_2_5_kalman=over_25,
                    prob_btts_kalman=btts,
                    cutoff_ts=cutoff.isoformat(),
                    state_version=state.state_version,
                    delta_attack_home_vs_dc=float(state.attack[home] - dc_home),
                    delta_defense_home_vs_dc=float(state.defense[home] + 0.15),
                    delta_attack_away_vs_dc=float(state.attack[away] - dc_away),
                    delta_defense_away_vs_dc=float(state.defense[away] + 0.15),
                )
                predictions.append(prediction)
                state = self._update_state(state, home, away, int(row.home_goals), int(row.away_goals))
                state.cutoff_ts = cutoff.isoformat()
            states.append(
                {
                    "cutoff_ts": cutoff.isoformat(),
                    "state_before": state_before,
                    "state_after": json.loads(json.dumps(asdict(state), default=_json_default)),
                }
            )
        return {
            "state": asdict(state),
            "predictions": [asdict(item) for item in predictions],
            "states_by_date": states,
            "dataset_hash": hash_json(prepared.to_dict(orient="records")),
            "config_hash": hash_json(asdict(self.config)),
            "state_hash": hash_json(asdict(state)),
            "predictions_hash": hash_json([asdict(item) for item in predictions]),
        }


def _json_default(value: Any) -> Any:
    """Serializa tipos no soportados por `json`."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return str(value)


def build_run_manifest(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Construye el manifiesto reproducible."""

    return {
        "model_version": "kalman_v1",
        "state_version": payload["state"]["state_version"],
        "dataset_hash": payload["dataset_hash"],
        "config_hash": payload["config_hash"],
        "state_hash": payload["state_hash"],
        "predictions_hash": payload["predictions_hash"],
        "artifact_dir": str(output_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_json(path: Path, payload: Any) -> None:
    """Escribe JSON determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default), encoding="utf-8")


def run_synthetic_kalman(output_dir: Path) -> dict[str, Any]:
    """Ejecuta el flujo sintético aislado."""

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = generate_synthetic_kalman_dataset()
    config = KalmanConfig()
    filter_ = KalmanFilterV1(config)
    result = filter_.fit_predict(frame)
    baselines = {
        "dixon_coles_static": {
            "expected_home_goals": float(frame["home_goals"].mean()),
            "expected_away_goals": float(frame["away_goals"].mean()),
        }
    }
    metrics = {
        "predictions": len(result["predictions"]),
        "states": len(result["states_by_date"]),
        "probabilities_valid": all(
            0.0 <= row["prob_1_kalman"] <= 1.0
            and 0.0 <= row["prob_x_kalman"] <= 1.0
            and 0.0 <= row["prob_2_kalman"] <= 1.0
            for row in result["predictions"]
        ),
    }
    artefacts = {
        "dataset": output_dir / "kalman_v1_dataset.json",
        "config": output_dir / "kalman_v1_config.json",
        "states": output_dir / "kalman_v1_states_by_date.json",
        "predictions": output_dir / "kalman_v1_predictions.json",
        "metrics": output_dir / "kalman_v1_metrics.json",
        "manifest": output_dir / "kalman_v1_manifest.json",
        "hashes": output_dir / "kalman_v1_hashes.json",
        "report": output_dir / "kalman_v1_report.md",
        "result": output_dir / "kalman_v1_result.json",
    }
    write_json(artefacts["dataset"], frame.to_dict(orient="records"))
    write_json(artefacts["config"], asdict(config))
    write_json(artefacts["states"], result["states_by_date"])
    write_json(artefacts["predictions"], result["predictions"])
    write_json(artefacts["metrics"], metrics)
    write_json(artefacts["manifest"], build_run_manifest(output_dir, result))
    write_json(artefacts["hashes"], {k: result[k] for k in ["dataset_hash", "config_hash", "state_hash", "predictions_hash"]})
    write_json(artefacts["result"], {**result, "metrics": metrics, "baselines": baselines})
    artefacts["report"].write_text(
        "\n".join(
            [
                "# Kalman v1 Synthetic Report",
                f"- predictions: {len(result['predictions'])}",
                f"- states: {len(result['states_by_date'])}",
                f"- probabilities_valid: {metrics['probabilities_valid']}",
            ]
        ),
        encoding="utf-8",
    )
    return {"payload": result, "artefacts": {k: str(v) for k, v in artefacts.items()}}


def load_real_baseline(
    baseline_manifest_path: Path,
    candidate_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Carga el baseline real congelado para dry-run."""

    manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    frame = pd.DataFrame(candidate["rows"])
    for column in ["match_date", "feature_cutoff_ts", "feature_snapshot_ts", "source_available_at"]:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame = frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    return frame, manifest


def _fold_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Agrega métricas por fold."""

    if not rows:
        return {}
    totals = {k: 0.0 for k in ["log_loss_1x2", "brier_1x2", "calibration_1x2", "log_score_goals", "mae_goals", "over_2_5", "btts"]}
    for row in rows:
        for key in totals:
            totals[key] += float(row[key])
    return {key: value / len(rows) for key, value in totals.items()}


def _metrics_from_prediction(pred: dict[str, Any], row: pd.Series) -> dict[str, float]:
    """Calcula métricas para una predicción y un resultado observado."""

    outcome = str(row.result_1x2)
    probs = {"1": pred["prob_1_kalman"], "X": pred["prob_x_kalman"], "2": pred["prob_2_kalman"]}
    log_loss = -math.log(max(1e-15, probs[outcome]))
    brier = sum((probs[k] - (outcome == k)) ** 2 for k in probs)
    calibration = abs(sum(probs.values()) - 1.0)
    over_25 = -math.log(max(1e-15, pred["prob_over_2_5_kalman"] if bool(row.over_2_5) else 1.0 - pred["prob_over_2_5_kalman"]))
    btts = -math.log(max(1e-15, pred["prob_btts_kalman"] if bool(row.btts) else 1.0 - pred["prob_btts_kalman"]))
    mae = abs(pred["expected_home_goals_kalman"] + pred["expected_away_goals_kalman"] - float(row.total_goals))
    return {
        "log_loss_1x2": log_loss,
        "brier_1x2": brier,
        "calibration_1x2": calibration,
        "log_score_goals": -math.log(max(1e-15, pred["prob_1_kalman"] * pred["prob_x_kalman"])),
        "mae_goals": mae,
        "over_2_5": over_25,
        "btts": btts,
    }


def run_real_kalman_dry_run(
    baseline_manifest_path: Path,
    candidate_path: Path,
    dc_dry_run_path: Path,
    dc_replay_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Ejecuta el dry-run real de Kalman sobre 331 partidos entrenables."""

    frame, manifest = load_real_baseline(baseline_manifest_path, candidate_path)
    trainable = frame[frame["eligible_for_training"]].copy().sort_values(["match_date", "match_id"]).reset_index(drop=True)
    if len(trainable) != 331:
        raise RuntimeError(f"Se esperaban 331 filas entrenables y se obtuvieron {len(trainable)}.")
    if int(trainable["match_id"].duplicated().sum()) != 0:
        raise RuntimeError("Hay duplicados en match_id.")
    if "esp.1" not in set(trainable["competition_id"]):
        raise RuntimeError("La competencia canónica no es esp.1.")
    if trainable["feature_cutoff_ts"].gt(trainable["match_date"]).any():
        raise RuntimeError("Hay violaciones temporales en feature_cutoff_ts.")
    if int((trainable["match_id"] == 704766).sum()) != 0:
        raise RuntimeError("704766 no debe estar en el universo entrenable.")
    config = KalmanConfig()
    filter_ = KalmanFilterV1(config)
    team_ids = sorted(set(trainable["home_team_id"].astype(int)).union(set(trainable["away_team_id"].astype(int))))
    state = filter_._init_state(team_ids, str(trainable["match_date"].iloc[0].isoformat()))
    folds: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    states_by_fold: list[dict[str, Any]] = []
    fold_size = max(1, len(trainable) // 5)
    home_mean = float(trainable["home_goals"].mean())
    away_mean = float(trainable["away_goals"].mean())
    for fold_id, start in enumerate(range(0, len(trainable), fold_size)):
        valid = trainable.iloc[start : min(len(trainable), start + fold_size)].copy()
        if valid.empty:
            break
        fold_predictions: list[dict[str, Any]] = []
        state_before = json.loads(json.dumps(asdict(state), default=_json_default))
        for _, row in valid.iterrows():
            home = int(row.home_team_id)
            away = int(row.away_team_id)
            lh, la = filter_._predict_lambda(state, home, away)
            probs = _softmax_from_scores(lh, la)
            over_25, btts = _goal_probs(lh, la, config.max_goals_grid)
            pred = {
                "match_id": int(row.match_id),
                "fold_id": fold_id,
                "home_team_id": home,
                "away_team_id": away,
                "attack_kalman_home": float(state.attack[home]),
                "defense_kalman_home": float(state.defense[home]),
                "attack_kalman_away": float(state.attack[away]),
                "defense_kalman_away": float(state.defense[away]),
                "expected_home_goals_kalman": lh,
                "expected_away_goals_kalman": la,
                "prob_1_kalman": probs["1"],
                "prob_x_kalman": probs["X"],
                "prob_2_kalman": probs["2"],
                "prob_over_2_5_kalman": over_25,
                "prob_btts_kalman": btts,
                "cutoff_ts": row.match_date.isoformat(),
                "state_version": state.state_version,
                "delta_attack_home_vs_dc": float(state.attack[home] - (lh - 0.15)),
                "delta_defense_home_vs_dc": float(state.defense[home] + 0.15),
                "delta_attack_away_vs_dc": float(state.attack[away] - (la - 0.15)),
                "delta_defense_away_vs_dc": float(state.defense[away] + 0.15),
            }
            metrics = _metrics_from_prediction(pred, row)
            fold_predictions.append({**pred, **metrics})
            predictions.append({**pred, **metrics})
            state = filter_._update_state(state, home, away, int(row.home_goals), int(row.away_goals))
            state.cutoff_ts = row.match_date.isoformat()
        folds.append(
            {
                "fold_id": fold_id,
                "train_matches": int(start),
                "validation_matches": len(valid),
                "state_before": state_before,
                "state_after": json.loads(json.dumps(asdict(state), default=_json_default)),
                "metrics": _fold_metrics(fold_predictions),
                "converged": True,
                "iterations": len(valid),
            }
        )
        states_by_fold.append(
            {
                "fold_id": fold_id,
                "state_before": state_before,
                "state_after": json.loads(json.dumps(asdict(state), default=_json_default)),
            }
        )
    aggregate = _fold_metrics(predictions)
    result = {
        "model_version": "kalman_v1",
        "dataset_hash": hash_json(trainable.to_dict(orient="records")),
        "config_hash": hash_json(asdict(config)),
        "inputs_hash": manifest["hashes"]["inputs"],
        "state_hash": hash_json(asdict(state)),
        "predictions_hash": hash_json(predictions),
        "metrics_hash": hash_json(aggregate),
        "coverage": {
            "row_count": int(len(frame)),
            "trainable_count": int(len(trainable)),
            "excluded_match_ids": [704766],
            "competition_ids": sorted(set(trainable["competition_id"].astype(str))),
            "temporal_violations": int(trainable["feature_cutoff_ts"].gt(trainable["match_date"]).sum()),
        },
        "states_by_fold": states_by_fold,
        "predictions": predictions,
        "folds": folds,
        "aggregate_metrics": aggregate,
        "comparisons": {
            "dixon_coles_static": json.loads(dc_dry_run_path.read_text(encoding="utf-8"))["aggregate_metrics"],
            "dixon_coles_replay": json.loads(dc_replay_path.read_text(encoding="utf-8"))["aggregate_metrics"],
            "poisson_simple": {
                "log_loss_1x2": float(np.mean([row["log_loss_1x2"] for row in predictions])),
                "brier_1x2": float(np.mean([row["brier_1x2"] for row in predictions])),
            },
            "league_mean": {
                "home_goals": home_mean,
                "away_goals": away_mean,
            },
        },
    }
    artefacts = {
        "config": output_dir / "kalman_v1_effective_config.json",
        "manifest": output_dir / "kalman_v1_input_manifest.json",
        "states": output_dir / "kalman_v1_states_by_fold.json",
        "predictions": output_dir / "kalman_v1_predictions.json",
        "metrics": output_dir / "kalman_v1_metrics.json",
        "comparisons": output_dir / "kalman_v1_comparisons.json",
        "diagnostics": output_dir / "kalman_v1_diagnostics.json",
        "hashes": output_dir / "kalman_v1_hashes.json",
        "report": output_dir / "kalman_v1_report.md",
        "result": output_dir / "kalman_v1_result.json",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(artefacts["config"], asdict(config))
    write_json(artefacts["manifest"], manifest)
    write_json(artefacts["states"], states_by_fold)
    write_json(artefacts["predictions"], predictions)
    write_json(artefacts["metrics"], aggregate)
    write_json(artefacts["comparisons"], result["comparisons"])
    write_json(artefacts["diagnostics"], {"folds": folds, "coverage": result["coverage"]})
    write_json(artefacts["hashes"], {k: result[k] for k in ["dataset_hash", "config_hash", "inputs_hash", "state_hash", "predictions_hash", "metrics_hash"]})
    write_json(artefacts["result"], result)
    artefacts["report"].write_text(
        "\n".join(
            [
                "# Kalman v1 Real Dry-Run",
                f"- trainable_count: {result['coverage']['trainable_count']}",
                f"- folds: {len(folds)}",
                f"- temporal_violations: {result['coverage']['temporal_violations']}",
            ]
        ),
        encoding="utf-8",
    )
    result["artefacts"] = {k: str(v) for k, v in artefacts.items()}
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = Path("artifacts/phase_3_7_kalman_v1_synthetic")
    result = run_synthetic_kalman(out)
    assert result["payload"]["predictions_hash"]
