"""Implementación sintética de `markov_v1`.

La capa transforma intensidades base en intensidades contextualizadas por
estados tácticos reproducibles. No calcula probabilidades ni excitación Hawkes.

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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


EVENT_TYPES_ALLOWED = {
    "goal",
    "shot_off_target",
    "shot_on_target",
    "shot_blocked",
    "corner",
    "yellow",
    "red",
    "substitution",
    "penalty_awarded",
    "penalty_scored",
}


@dataclass(slots=True)
class MarkovV1Config:
    """Configuración efectiva de `markov_v1`."""

    model_version: str = "markov_v1"
    transition_version: str = "markov_transition_v1"
    state_labels: dict[int, str] | None = None
    state_multipliers: dict[int, float] | None = None
    context_factor_enabled: bool = False
    context_factor_value: float = 1.0
    base_matrix: list[list[float]] | None = None
    goal_difference_limit: float = 0.12
    minute_limit: float = 0.15
    opponent_state_limit: float = 0.10
    recent_events_limit: float = 0.10
    min_probability: float = 0.0
    max_probability: float = 1.0
    window_short_minutes: int = 5
    window_medium_minutes: int = 10
    window_long_minutes: int = 15
    max_intensity: float = 50.0
    min_intensity: float = 1e-6

    def __post_init__(self) -> None:
        """Normaliza diccionarios por defecto."""

        if self.state_labels is None:
            self.state_labels = {0: "equilibrio", 1: "repliegue", 2: "asedio"}
        if self.state_multipliers is None:
            self.state_multipliers = {0: 1.0, 1: 0.75, 2: 1.25}
        if self.base_matrix is None:
            self.base_matrix = [[0.82, 0.10, 0.08], [0.12, 0.74, 0.14], [0.10, 0.18, 0.72]]


@dataclass(slots=True)
class MarkovSnapshot:
    """Salida contextual por snapshot."""

    match_id: int
    snapshot_ts: str
    window_5m: dict[str, Any]
    window_10m: dict[str, Any]
    state_before: dict[str, int]
    state_after: dict[str, int]
    multiplier_home: float
    multiplier_away: float
    lambda_base_home: float
    lambda_base_away: float
    lambda_markov_home: float
    lambda_markov_away: float
    home_state: int
    away_state: int
    recent_events: list[dict[str, Any]]
    home_team_id: int
    away_team_id: int
    cutoff_ts: str


def generate_synthetic_markov_dataset() -> pd.DataFrame:
    """Genera un dataset sintético de eventos para validación."""

    rows = [
        (1, "2025-01-01T12:00:00+00:00", 1, 2, 0, 0, "kickoff", None, None, False),
        (1, "2025-01-01T12:05:00+00:00", 1, 2, 5, 0, "shot_on_target", 1, 1001, False),
        (1, "2025-01-01T12:12:00+00:00", 1, 2, 12, 0, "goal", 2, 1002, False),
        (1, "2025-01-01T12:12:00+00:00", 1, 2, 12, 0, "goal", 2, 1002, False),
        (1, "2025-01-01T12:20:00+00:00", 1, 2, 20, 0, "yellow", 1, 1003, False),
        (1, "2025-01-01T12:32:00+00:00", 1, 2, 32, 0, "substitution", 1, 1004, False),
        (2, "2025-01-01T12:00:00+00:00", 3, 4, 0, 0, "kickoff", None, None, False),
        (2, "2025-01-01T12:03:00+00:00", 3, 4, 3, 0, "unknown_event", 3, 2001, False),
        (2, "2025-01-01T12:08:00+00:00", 3, 4, 8, 0, "shot_blocked", 4, 2002, False),
        (2, "2025-01-01T12:14:00+00:00", 3, 4, 14, 0, "red", 3, 2003, False),
        (2, "2025-01-01T12:14:00+00:00", 3, 4, 14, 0, "red", 3, 2003, False),
        (2, "2025-01-01T12:25:00+00:00", 3, 4, 25, 0, "penalty_awarded", 4, 2004, True),
        (3, "2025-01-01T12:00:00+00:00", 5, 6, 0, 0, "kickoff", None, None, False),
        (3, "2025-01-01T12:04:00+00:00", 5, 6, 4, 0, "corner", 5, 3001, False),
        (3, "2025-01-01T12:09:00+00:00", 5, 6, 9, 0, "shot_off_target", 6, 3002, False),
        (3, "2025-01-01T12:18:00+00:00", 5, 6, 18, 0, "penalty_scored", 5, 3003, False),
        (4, "2025-01-01T12:00:00+00:00", 7, 8, 0, 0, "kickoff", None, None, False),
        (4, "2025-01-01T12:06:00+00:00", 7, 8, 6, 0, "shot_on_target", None, 4001, False),
        (4, "2025-01-01T12:11:00+00:00", 7, 8, 11, 0, "shot_on_target", 8, 4002, False),
        (4, "2025-01-01T12:16:00+00:00", 7, 8, 16, 0, "goal", 7, 4003, False),
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "match_id",
            "event_ts",
            "home_team_id",
            "away_team_id",
            "minute",
            "second",
            "event_type",
            "team_id",
            "event_id",
            "annulled",
        ],
    )
    frame["event_ts"] = pd.to_datetime(frame["event_ts"], utc=True)
    frame["kickoff_ts"] = frame.groupby("match_id")["event_ts"].transform("min")
    frame["snapshot_ts"] = frame["event_ts"]
    frame["score_home"] = frame.groupby("match_id").cumcount()
    frame["score_away"] = 0
    frame["is_control"] = frame["event_type"].eq("kickoff")
    return frame.sort_values(["match_id", "event_ts", "event_id"], kind="stable").reset_index(drop=True)


def _json_default(value: Any) -> Any:
    """Serializa tipos no JSON."""

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return str(value)


def _hash_json(value: Any) -> str:
    """Calcula hash determinista de un objeto JSON-serializable."""

    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MarkovV1:
    """Modulador táctico de intensidades in-play."""

    def __init__(self, config: MarkovV1Config | None = None) -> None:
        self.config = config or MarkovV1Config()
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida configuración numérica."""

        if self.config.context_factor_value != 1.0:
            raise ValueError("C_e(t) debe permanecer fijado en 1.0 en markov_v1.")
        if self.config.min_intensity <= 0 or self.config.max_intensity <= self.config.min_intensity:
            raise ValueError("Rango de intensidad inválido.")
        if len(self.config.base_matrix or []) != 3:
            raise ValueError("La matriz base debe ser 3x3.")

    def _normalize_rows(self, matrix: np.ndarray) -> np.ndarray:
        """Normaliza filas y valida la matriz."""

        if matrix.shape != (3, 3):
            raise ValueError("La matriz de transición debe ser 3x3.")
        if np.any(matrix < 0.0):
            raise ValueError("La matriz de transición no puede tener valores negativos.")
        row_sums = matrix.sum(axis=1, keepdims=True)
        if np.any(row_sums <= 0):
            raise ValueError("La matriz de transición tiene filas inválidas.")
        normalized = matrix / row_sums
        if not np.allclose(normalized.sum(axis=1), 1.0, atol=1e-9):
            raise FloatingPointError("La matriz de transición no normaliza correctamente.")
        return normalized

    def _validate_state(self, state: int) -> int:
        """Valida que el estado táctico pertenezca al dominio permitido."""

        if state not in {0, 1, 2}:
            raise ValueError("Estado táctico inválido.")
        return state

    def _event_ts(self, kickoff_ts: pd.Timestamp, minute: int, second: int) -> pd.Timestamp:
        """Deriva el timestamp del evento a partir del kickoff."""

        return kickoff_ts + pd.Timedelta(minutes=int(minute), seconds=int(second))

    def _state_from_score(self, goal_difference: int, minute: int, event_count: int, previous: int) -> int:
        """Calcula el estado táctico de forma determinista."""

        if goal_difference <= -1:
            return 2 if minute >= 60 or event_count > 0 else previous
        if goal_difference >= 1:
            return 1 if minute >= 60 or event_count > 0 else previous
        if minute >= 75 and event_count > 1:
            return 2
        return 0

    def _allowed_events(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Filtra eventos válidos para transición."""

        allowed = frame[frame["event_type"].isin(EVENT_TYPES_ALLOWED)].copy()
        return allowed.sort_values(["event_ts", "event_id"], kind="stable").reset_index(drop=True)

    def _deduplicate(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Deduplica eventos por clave semántica estable."""

        keys = ["match_id", "event_ts", "event_type", "team_id", "minute", "second"]
        if "event_id" in frame.columns:
            keys.append("event_id")
        return frame.drop_duplicates(subset=keys, keep="first").sort_values(["match_id", "event_ts", "event_id"], kind="stable").reset_index(drop=True)

    def _transition_adjustment(self, minute: int, goal_difference: int, rival_state: int, event_volume: int) -> np.ndarray:
        """Construye ajustes acotados para la matriz base."""

        minute_scale = min(1.0, max(0.0, minute / 90.0))
        gd_scale = float(np.clip(goal_difference / 3.0, -1.0, 1.0))
        event_scale = min(1.0, event_volume / 4.0)
        rival_scale = 1.0 if rival_state == 0 else (0.5 if rival_state == 1 else -0.5)
        adjustment = np.array(
            [
                [-0.02 * minute_scale - 0.03 * gd_scale, 0.01 * event_scale, 0.01 * rival_scale],
                [0.02 * gd_scale, -0.01 * minute_scale, 0.01 * event_scale],
                [0.01 * minute_scale, 0.02 * event_scale, -0.03 * gd_scale - 0.01 * rival_scale],
            ],
            dtype=float,
        )
        return np.clip(adjustment, -self.config.goal_difference_limit, self.config.goal_difference_limit)

    def _return_to_baseline_multiplier(self, event_volume: int, window_minutes: int) -> float:
        """Acerca el multiplicador al baseline cuando no hay señales recientes."""

        if event_volume > 0:
            return 1.0
        decay = 1.0 - min(0.25, window_minutes / 60.0)
        return float(np.clip(decay, 0.75, 1.0))

    def _transition_matrix(
        self,
        team_id: int,
        minute: int,
        goal_difference: int,
        rival_state: int,
        event_volume: int,
    ) -> np.ndarray:
        """Devuelve la matriz de transición 3x3 reproducible."""

        base = np.asarray(self.config.base_matrix, dtype=float)
        adjustment = self._transition_adjustment(minute, goal_difference, rival_state, event_volume)
        matrix = base + adjustment
        if np.any(matrix < 0.0):
            raise ValueError("La matriz de transición contiene probabilidades negativas.")
        if np.any(matrix.sum(axis=1) <= 0):
            raise ValueError("La matriz de transición contiene filas inválidas.")
        matrix = np.clip(matrix, self.config.min_probability, self.config.max_probability)
        return self._normalize_rows(matrix)

    def _state_and_multiplier(self, previous_state: int, minute: int, goal_difference: int, event_volume: int) -> tuple[int, float]:
        """Calcula estado y multiplicador táctico."""

        previous_state = self._validate_state(previous_state)
        state = self._validate_state(self._state_from_score(goal_difference, minute, event_volume, previous_state))
        multiplier = float(self.config.state_multipliers[state])
        multiplier *= self._return_to_baseline_multiplier(event_volume, minute)
        return state, float(np.clip(multiplier, self.config.min_intensity, self.config.max_intensity))

    def _window_events(self, rows: pd.DataFrame, snapshot_ts: pd.Timestamp, window_minutes: int) -> pd.DataFrame:
        """Obtiene eventos dentro de una ventana temporal explícita."""

        lower_bound = snapshot_ts - pd.Timedelta(minutes=window_minutes)
        window = rows[(rows["event_ts"] > lower_bound) & (rows["event_ts"] <= snapshot_ts)].copy()
        return window.sort_values(["event_ts", "event_id"], kind="stable").reset_index(drop=True)

    def _window_summary(self, rows: pd.DataFrame, snapshot_ts: pd.Timestamp, window_minutes: int) -> dict[str, Any]:
        """Resume eventos por equipo y rival en una ventana dada."""

        window = self._window_events(rows, snapshot_ts, window_minutes)
        counts_home = int(window["team_id"].eq(int(rows["home_team_id"].iloc[0])).sum())
        counts_away = int(window["team_id"].eq(int(rows["away_team_id"].iloc[0])).sum())
        return {
            "window_minutes": window_minutes,
            "event_count": int(len(window)),
            "home_event_count": counts_home,
            "away_event_count": counts_away,
            "events": [
                {
                    "event_type": str(row["event_type"]),
                    "team_id": None if pd.isna(row["team_id"]) else int(row["team_id"]),
                    "event_ts": row["event_ts"].isoformat(),
                }
                for _, row in window.iterrows()
            ],
        }

    def _reject_invalid_transitions(self, matrix: np.ndarray) -> None:
        """Rechaza matrices de transición inválidas."""

        if matrix.shape != (3, 3):
            raise ValueError("La matriz de transición debe ser 3x3.")
        if np.any(matrix < 0.0):
            raise ValueError("Las probabilidades de transición no pueden ser negativas.")
        row_sums = matrix.sum(axis=1)
        if not np.allclose(row_sums, 1.0, atol=1e-9):
            raise ValueError("Las filas de la matriz de transición deben sumar 1.")

    def _state_by_team(self, rows: pd.DataFrame, kickoff_ts: pd.Timestamp, snapshot_ts: pd.Timestamp) -> list[dict[str, Any]]:
        """Procesa snapshots de un partido para ambos equipos."""

        team_ids = sorted(set(rows["home_team_id"].astype(int)).union(set(rows["away_team_id"].astype(int))))
        home_team_id, away_team_id = int(rows["home_team_id"].iloc[0]), int(rows["away_team_id"].iloc[0])
        states = {home_team_id: 0, away_team_id: 0}
        snapshots: list[dict[str, Any]] = []
        rows = rows.copy()
        rows["event_ts"] = pd.to_datetime(rows["event_ts"], utc=True)
        rows = self._deduplicate(rows)
        rows = rows[rows["event_ts"] <= snapshot_ts].copy()
        filtered = self._allowed_events(rows)
        for _, row in filtered.iterrows():
            if bool(row.get("annulled", False)):
                continue
            team_id = row.get("team_id")
            if pd.isna(team_id):
                continue
            team_id = int(team_id)
            if team_id not in states:
                continue
            if row["event_type"] == "goal":
                states[team_id] = 2 if team_id == away_team_id else 1 if team_id == home_team_id else states[team_id]
            elif row["event_type"] in {"red", "yellow"}:
                states[team_id] = 1 if row["event_type"] == "yellow" else 2
            elif row["event_type"] in {"shot_on_target", "shot_blocked", "shot_off_target", "corner"}:
                states[team_id] = max(states[team_id], 0 if team_id == home_team_id else 0)
            elif row["event_type"] in {"substitution", "penalty_awarded", "penalty_scored"}:
                states[team_id] = states[team_id]
            snapshots.append(
                {
                    "match_id": int(row["match_id"]),
                    "snapshot_ts": row["event_ts"].isoformat(),
                    "event_type": row["event_type"],
                    "team_id": team_id,
                }
            )
        return snapshots

    def predict_snapshot(self, frame: pd.DataFrame, base_intensity_home: float, base_intensity_away: float, snapshot_ts: str) -> dict[str, Any]:
        """Calcula intensidades contextualizadas en un snapshot."""

        if frame.empty:
            raise ValueError("El frame no puede estar vacío.")
        kickoff_ts = pd.to_datetime(frame["kickoff_ts"].iloc[0], utc=True)
        snapshot = pd.to_datetime(snapshot_ts, utc=True)
        if snapshot < kickoff_ts:
            raise ValueError("Los snapshots anteriores al kickoff no son válidos.")
        rows = frame.copy()
        rows["event_ts"] = pd.to_datetime(rows["event_ts"], utc=True)
        if (rows["event_ts"] > snapshot).any():
            rows = rows[rows["event_ts"] <= snapshot].copy()
        rows = self._deduplicate(rows)
        rows = rows[rows["event_ts"] <= snapshot].copy()
        home_id = int(rows["home_team_id"].iloc[0])
        away_id = int(rows["away_team_id"].iloc[0])
        home_state = 0
        away_state = 0
        recent_events: list[dict[str, Any]] = []
        window_5 = self._window_summary(rows, snapshot, self.config.window_short_minutes)
        window_10 = self._window_summary(rows, snapshot, self.config.window_medium_minutes)
        window_events = pd.concat(
            [
                self._window_events(rows, snapshot, self.config.window_short_minutes),
                self._window_events(rows, snapshot, self.config.window_medium_minutes),
            ],
            ignore_index=True,
        ).drop_duplicates(subset=["event_ts", "event_id", "event_type", "team_id"], keep="first")
        window_events = window_events.sort_values(["event_ts", "event_id"], kind="stable").reset_index(drop=True)
        for _, row in window_events.iterrows():
            if bool(row.get("annulled", False)):
                continue
            event_type = str(row["event_type"])
            if event_type not in EVENT_TYPES_ALLOWED:
                continue
            team_id = row.get("team_id")
            if pd.isna(team_id) or int(team_id) not in {home_id, away_id}:
                recent_events.append(
                    {
                        "event_type": event_type,
                        "team_id": None if pd.isna(team_id) else int(team_id),
                        "event_ts": row["event_ts"].isoformat(),
                    }
                )
                continue
            team_id = int(team_id)
            if event_type == "goal":
                if team_id == home_id:
                    home_state = 2
                else:
                    away_state = 2
            elif event_type == "red":
                if team_id == home_id:
                    home_state = 1
                else:
                    away_state = 1
            elif event_type == "yellow":
                if team_id == home_id:
                    home_state = max(home_state, 1)
                else:
                    away_state = max(away_state, 1)
            recent_events.append(
                {
                    "event_type": event_type,
                    "team_id": team_id,
                    "event_ts": row["event_ts"].isoformat(),
                }
            )
        minute = int(((snapshot - kickoff_ts).total_seconds() // 60))
        gd = int((0 if home_state == away_state else (1 if home_state > away_state else -1)))
        home_matrix = self._transition_matrix(home_id, minute, gd, away_state, len(window_5["events"]) + len(window_10["events"]))
        away_matrix = self._transition_matrix(away_id, minute, -gd, home_state, len(window_5["events"]) + len(window_10["events"]))
        self._reject_invalid_transitions(home_matrix)
        self._reject_invalid_transitions(away_matrix)
        state_before = {"home_state": 0, "away_state": 0}
        home_state = int(np.argmax(home_matrix[home_state]))
        away_state = int(np.argmax(away_matrix[away_state]))
        home_state = self._validate_state(home_state)
        away_state = self._validate_state(away_state)
        home_multiplier = float(self.config.state_multipliers[home_state])
        away_multiplier = float(self.config.state_multipliers[away_state])
        lambda_home = float(base_intensity_home) * home_multiplier
        lambda_away = float(base_intensity_away) * away_multiplier
        if not np.isfinite(lambda_home) or not np.isfinite(lambda_away):
            raise FloatingPointError("Las intensidades deben ser finitas.")
        if lambda_home <= 0 or lambda_away <= 0:
            raise ValueError("Las intensidades deben ser positivas.")
        lambda_home = float(np.clip(lambda_home, self.config.min_intensity, self.config.max_intensity))
        lambda_away = float(np.clip(lambda_away, self.config.min_intensity, self.config.max_intensity))
        return {
            "match_id": int(rows["match_id"].iloc[0]),
            "snapshot_ts": snapshot.isoformat(),
            "window_5m": window_5,
            "window_10m": window_10,
            "state_before": state_before,
            "state_after": {"home_state": home_state, "away_state": away_state},
            "multiplier_home": home_multiplier,
            "multiplier_away": away_multiplier,
            "lambda_base_home": float(base_intensity_home),
            "lambda_base_away": float(base_intensity_away),
            "lambda_markov_home": lambda_home,
            "lambda_markov_away": lambda_away,
            "home_state": home_state,
            "away_state": away_state,
            "recent_events": recent_events,
            "recent_events_5m": window_5["events"],
            "recent_events_10m": window_10["events"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "cutoff_ts": snapshot.isoformat(),
            "home_transition_matrix": home_matrix.tolist(),
            "away_transition_matrix": away_matrix.tolist(),
        }

    def fit_predict(self, frame: pd.DataFrame, base_intensity_home: float = 1.5, base_intensity_away: float = 1.2) -> dict[str, Any]:
        """Ejecuta el flujo sintético sobre snapshots."""

        required = {"match_id", "event_ts", "home_team_id", "away_team_id", "minute", "second", "event_type"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Faltan columnas: {sorted(missing)}")
        prepared = frame.copy()
        prepared["event_ts"] = pd.to_datetime(prepared["event_ts"], utc=True)
        prepared = prepared.sort_values(["match_id", "event_ts", "event_id"], kind="stable").reset_index(drop=True)
        snapshots: list[dict[str, Any]] = []
        transition_matrices: list[dict[str, Any]] = []
        events_audit: list[dict[str, Any]] = []
        for match_id, bucket in prepared.groupby("match_id", sort=True):
            kickoff_ts = pd.to_datetime(bucket["kickoff_ts"].iloc[0], utc=True)
            for snapshot_ts, snapshot_bucket in bucket.groupby("snapshot_ts", sort=True):
                snapshot = self.predict_snapshot(snapshot_bucket, base_intensity_home, base_intensity_away, str(snapshot_ts))
                snapshots.append(snapshot)
                transition_matrices.append(
                    {
                        "match_id": int(match_id),
                        "snapshot_ts": str(snapshot_ts),
                        "home_transition_matrix": snapshot["home_transition_matrix"],
                        "away_transition_matrix": snapshot["away_transition_matrix"],
                    }
                )
                events_audit.extend(snapshot["recent_events"])
        result = {
            "model_version": self.config.model_version,
            "config_hash": _hash_json(asdict(self.config)),
            "dataset_hash": _hash_json(prepared.to_dict(orient="records")),
            "predictions_hash": _hash_json(snapshots),
            "matrices_hash": _hash_json(transition_matrices),
            "events_hash": _hash_json(events_audit),
            "snapshots": snapshots,
            "transition_matrices": transition_matrices,
            "events_audit": events_audit,
            "coverage": {
                "match_count": int(prepared["match_id"].nunique()),
                "snapshot_count": len(snapshots),
                "event_count": len(events_audit),
            },
        }
        return result


def build_manifest(payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Construye manifiesto reproducible."""

    return {
        "model_version": "markov_v1",
        "config_hash": payload["config_hash"],
        "dataset_hash": payload["dataset_hash"],
        "predictions_hash": payload["predictions_hash"],
        "matrices_hash": payload["matrices_hash"],
        "events_hash": payload["events_hash"],
        "artifact_dir": str(output_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_json(path: Path, payload: Any) -> None:
    """Escribe JSON determinista."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default), encoding="utf-8")


def run_synthetic_markov_v1(output_dir: Path) -> dict[str, Any]:
    """Ejecuta la validación sintética de `markov_v1`."""

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = generate_synthetic_markov_dataset()
    config = MarkovV1Config()
    model = MarkovV1(config)
    payload = model.fit_predict(frame)
    artefacts = {
        "dataset": output_dir / "markov_v1_dataset.json",
        "config": output_dir / "markov_v1_config.json",
        "snapshots": output_dir / "markov_v1_snapshots.json",
        "matrices": output_dir / "markov_v1_transition_matrices.json",
        "events": output_dir / "markov_v1_events_audit.json",
        "manifest": output_dir / "markov_v1_manifest.json",
        "hashes": output_dir / "markov_v1_hashes.json",
        "report": output_dir / "markov_v1_report.md",
        "result": output_dir / "markov_v1_result.json",
    }
    write_json(artefacts["dataset"], frame.to_dict(orient="records"))
    write_json(artefacts["config"], asdict(config))
    write_json(artefacts["snapshots"], payload["snapshots"])
    write_json(artefacts["matrices"], payload["transition_matrices"])
    write_json(artefacts["events"], payload["events_audit"])
    write_json(artefacts["manifest"], build_manifest(payload, output_dir))
    write_json(artefacts["hashes"], {k: payload[k] for k in ["config_hash", "dataset_hash", "predictions_hash", "matrices_hash", "events_hash"]})
    write_json(artefacts["result"], payload)
    artefacts["report"].write_text(
        "\n".join(
            [
                "# Markov v1 Synthetic Report",
                f"- match_count: {payload['coverage']['match_count']}",
                f"- snapshot_count: {payload['coverage']['snapshot_count']}",
                f"- event_count: {payload['coverage']['event_count']}",
                f"- c_factor: {config.context_factor_value}",
            ]
        ),
        encoding="utf-8",
    )
    payload["artefacts"] = {k: str(v) for k, v in artefacts.items()}
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = Path("artifacts/phase_4_2_markov_v1_synthetic")
    result = run_synthetic_markov_v1(out)
    assert result["coverage"]["match_count"] >= 1
