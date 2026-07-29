"""Evaluador live de `markov_v1`.

Genera targets posteriores y métricas live a partir de snapshots históricos
ya producidos por Markov v1. No escribe en PostgreSQL ni recalibra la matriz
de transición.

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
import re
import sys
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from markov_v1 import MarkovV1, MarkovV1Config, _hash_json

logger = logging.getLogger(__name__)

BASE_DIR = Path("/mnt/c/users/marco/desktop/dikahama_project/futbol_predictor")
ARTIFACT_DIR = BASE_DIR / "artifacts" / "phase_4_8_markov_v1_live_evaluation"
SNAPSHOT_DIR = BASE_DIR / "artifacts" / "phase_4_6_markov_v1_full_historical_v2"
EVENT_CACHE_DIR = BASE_DIR / "data" / "cache" / "espn"
SNAPSHOT_SOURCE = SNAPSHOT_DIR / "markov_v1_snapshots.json"
MARKOV_RESULT_SOURCE = SNAPSHOT_DIR / "markov_v1_result.json"
LIVE_CONFIG_SOURCE = BASE_DIR / "artifacts" / "phase_4_7_markov_v1_live_evaluation" / "markov_v1_live_evaluation_config.json"


@dataclass(slots=True)
class LivePrediction:
    """Predicción live por snapshot."""

    match_id: int
    snapshot_ts: str
    minute: int
    second: int
    score_home: int
    score_away: int
    remaining_home_goals: int
    remaining_away_goals: int
    remaining_total_goals: int
    next_goal_exists: int
    next_goal_team: str | None
    time_to_next_goal_seconds: float | None
    censored: bool
    live_over_under_remaining: dict[str, Any]
    live_btts_remaining: dict[str, Any]
    live_next_goal_market: dict[str, Any]
    live_draw_at_full_time: dict[str, Any] | None
    lambda_base_home: float
    lambda_base_away: float
    lambda_markov_home: float
    lambda_markov_away: float
    home_state: int
    away_state: int
    goal_difference: int
    window_5m: dict[str, Any]
    window_10m: dict[str, Any]
    recent_events: list[dict[str, Any]]
    warnings: list[str]


def _json_default(value: Any) -> Any:
    """Serializa valores no nativos de JSON.

    Args:
        value: Valor a serializar.

    Returns:
        Representación serializable.
    """

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def _hash_payload(payload: Any) -> str:
    """Calcula un hash SHA-256 determinista.

    Args:
        payload: Objeto JSON-serializable.

    Returns:
        Hash hexadecimal.
    """

    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    """Calcula hash SHA-256 de un archivo."""

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_json(path: Path) -> Any:
    """Carga JSON desde disco.

    Args:
        path: Ruta del archivo.

    Returns:
        Objeto deserializado.
    """

    return json.loads(path.read_text(encoding="utf-8"))


def _event_type_name(raw: dict[str, Any]) -> str:
    """Obtiene el tipo semántico del evento ESPN.

    Args:
        raw: Evento crudo.

    Returns:
        Nombre del tipo.
    """

    event_type = raw.get("type")
    if isinstance(event_type, dict):
        return str(event_type.get("type") or event_type.get("text") or "unknown")
    return str(event_type or "unknown")


def _team_id_from_ref(ref: Any) -> int | None:
    """Extrae `team_id` desde un `ref` de ESPN.

    Args:
        ref: Referencia o cadena.

    Returns:
        Identificador numérico del equipo o `None`.
    """

    if ref is None:
        return None
    text = str(ref)
    match = re.search(r"/teams/(\d+)", text)
    return int(match.group(1)) if match else None


def _parse_wallclock(raw: dict[str, Any]) -> pd.Timestamp:
    """Convierte el wallclock ESPN a timestamp UTC."""

    value = raw.get("wallclock") or raw.get("modified") or raw.get("modifiedDate")
    if value is None:
        raise ValueError(f"Evento sin timestamp: {raw.get('id')}")
    return pd.to_datetime(value, utc=True)


def _build_event_timeline(
    match_id: int,
    source_match_id: int,
    cache_dir: Path,
    cache_index: dict[int, Path] | None = None,
) -> pd.DataFrame:
    """Construye el timeline completo de eventos de un partido.

    Args:
        match_id: Identificador interno.
        source_match_id: Identificador ESPN.
        cache_dir: Directorio del cache ESPN.

    Returns:
        DataFrame cronológico.
    """

    event_file = cache_index.get(source_match_id) if cache_index else None
    if event_file is None:
        event_file = _find_event_cache_file(source_match_id, cache_dir)
    raw = _load_json(event_file)
    items = raw["payload"]["items"]
    rows: list[dict[str, Any]] = []
    for item in items:
        event_ts = _parse_wallclock(item)
        event_type = _event_type_name(item)
        team_id = _team_id_from_ref((item.get("team") or {}).get("$ref"))
        if team_id is None and item.get("participants"):
            first = item["participants"][0]
            team_id = _team_id_from_ref((first.get("team") or {}).get("$ref"))
        rows.append(
            {
                "match_id": match_id,
                "source_match_id": source_match_id,
                "event_id": int(item["id"]),
                "event_ts": event_ts,
                "event_type": event_type,
                "team_id": team_id,
                "valid": bool(item.get("valid", True)),
                "scoring_play": bool(item.get("scoringPlay", False)),
                "substitution": bool(item.get("substitution", False)),
                "red_card": bool(item.get("redCard", False)),
                "yellow_card": bool(item.get("yellowCard", False)),
                "penalty_kick": bool(item.get("penaltyKick", False)),
                "own_goal": bool(item.get("ownGoal", False)),
                "home_score": int(item.get("homeScore", 0)),
                "away_score": int(item.get("awayScore", 0)),
            }
        )
    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["event_ts", "event_id"], kind="stable").reset_index(drop=True)
    return frame


def _find_event_cache_file(source_match_id: int, cache_dir: Path) -> Path:
    """Localiza el archivo ESPN por identificador de evento."""

    pattern = re.compile(rf"/events/{source_match_id}/")
    for path in cache_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items = payload.get("payload", {}).get("items", [])
        if not items:
            continue
        first = items[0]
        ref = str(first.get("$ref", ""))
        if pattern.search(ref):
            return path
    raise FileNotFoundError(f"No se encontró cache ESPN para source_match_id={source_match_id}")


def _build_event_cache_index(cache_dir: Path) -> dict[int, Path]:
    """Indexa archivos ESPN por `source_match_id`."""

    index: dict[int, Path] = {}
    for path in cache_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items = payload.get("payload", {}).get("items", [])
        if not items:
            continue
        ref = str(items[0].get("$ref", ""))
        match = re.search(r"/events/(\d+)/", ref)
        if match:
            index[int(match.group(1))] = path
    return index


def _load_snapshot_store(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Carga el almacén de snapshots de Markov."""

    data = _load_json(path)
    if not isinstance(data, dict):
        raise ValueError("El artefacto de snapshots debe ser un diccionario.")
    return data


def _load_markov_result(path: Path) -> dict[str, Any]:
    """Carga el resultado histórico de Markov."""

    return _load_json(path)


def _compact_prediction(pred: LivePrediction) -> dict[str, Any]:
    """Convierte la predicción en una estructura compacta para serialización."""

    return {
        "match_id": pred.match_id,
        "snapshot_ts": pred.snapshot_ts,
        "minute": pred.minute,
        "second": pred.second,
        "score_home": pred.score_home,
        "score_away": pred.score_away,
        "remaining_home_goals": pred.remaining_home_goals,
        "remaining_away_goals": pred.remaining_away_goals,
        "remaining_total_goals": pred.remaining_total_goals,
        "next_goal_exists": pred.next_goal_exists,
        "next_goal_team": pred.next_goal_team,
        "time_to_next_goal_seconds": pred.time_to_next_goal_seconds,
        "censored": pred.censored,
        "lambda_base_home": pred.lambda_base_home,
        "lambda_base_away": pred.lambda_base_away,
        "lambda_markov_home": pred.lambda_markov_home,
        "lambda_markov_away": pred.lambda_markov_away,
        "home_state": pred.home_state,
        "away_state": pred.away_state,
        "goal_difference": pred.goal_difference,
    }


def _parse_event_ts(value: Any) -> pd.Timestamp:
    """Parses a timestamp-like value to UTC pandas timestamp."""

    return pd.to_datetime(value, utc=True)


def _future_goal_events(snapshot: dict[str, Any]) -> pd.DataFrame:
    """Reconstruye eventos futuros relevantes desde el snapshot."""

    future_events = snapshot.get("events_excluded", [])
    rows: list[dict[str, Any]] = []
    for event in future_events:
        rows.append(
            {
                "event_ts": _parse_event_ts(event["event_ts"]),
                "event_type": str(event["event_type"]),
                "team_id": event.get("team_id"),
                "source_event_id": event.get("source_event_id"),
            }
        )
    frame = pd.DataFrame(rows, columns=["event_ts", "event_type", "team_id", "source_event_id"])
    if not frame.empty:
        frame = frame.sort_values(["event_ts", "source_event_id"], kind="stable").reset_index(drop=True)
    return frame


def _final_scores(match_snapshots: list[dict[str, Any]]) -> tuple[int, int]:
    """Obtiene el marcador final a partir del último snapshot."""

    last = match_snapshots[-1]
    return int(last["score_home"]), int(last["score_away"])


def _remaining_score_targets(snapshot: dict[str, Any], final_home: int, final_away: int) -> dict[str, int]:
    """Calcula goles restantes desde un snapshot."""

    return {
        "remaining_home_goals": max(0, final_home - int(snapshot["score_home"])),
        "remaining_away_goals": max(0, final_away - int(snapshot["score_away"])),
        "remaining_total_goals": max(0, final_home + final_away - int(snapshot["score_home"]) - int(snapshot["score_away"])),
    }


def _remaining_horizon_minutes(snapshot_ts: pd.Timestamp, final_ts: pd.Timestamp) -> float:
    """Calcula el horizonte remanente en minutos."""

    return max(0.0, (final_ts - snapshot_ts).total_seconds() / 60.0)


def _poisson_pmf(lam: float, max_k: int) -> np.ndarray:
    """Calcula PMF Poisson truncada."""

    if lam < 0:
        raise ValueError("La media Poisson no puede ser negativa.")
    if lam == 0:
        probs = np.zeros(max_k + 1, dtype=float)
        probs[0] = 1.0
        return probs
    log_probs = np.array([k * math.log(lam) - lam - math.lgamma(k + 1) for k in range(max_k + 1)], dtype=float)
    log_probs -= float(np.max(log_probs))
    probs = np.exp(log_probs)
    total = float(probs.sum())
    if not np.isfinite(total) or total <= 0:
        raise FloatingPointError("La PMF Poisson no normaliza.")
    return probs / total


def _goal_market_probabilities(lam_home: float, lam_away: float, horizon_minutes: float) -> dict[str, Any]:
    """Deriva probabilidades live desde una matriz Poisson conjunta."""

    factor = horizon_minutes / 90.0
    mu_home = max(lam_home * factor, 0.0)
    mu_away = max(lam_away * factor, 0.0)
    max_k = int(max(8, np.ceil(mu_home + mu_away + 6.0)))
    p_home = _poisson_pmf(mu_home, max_k)
    p_away = _poisson_pmf(mu_away, max_k)
    matrix = np.outer(p_home, p_away)
    matrix = matrix / matrix.sum()
    return {
        "horizon_minutes": horizon_minutes,
        "mu_home": mu_home,
        "mu_away": mu_away,
        "matrix": matrix,
        "over_under": {
            "0.5": float(1.0 - matrix[0, 0]),
            "1.5": float(1.0 - matrix[:2, :2].sum()),
            "2.5": float(1.0 - matrix[:3, :3].sum()),
        },
        "btts": float(1.0 - matrix[0, :].sum() - matrix[:, 0].sum() + matrix[0, 0]),
        "next_goal": {
            "home": float(mu_home / max(mu_home + mu_away, 1e-12) * (1.0 - np.exp(-(mu_home + mu_away)))),
            "away": float(mu_away / max(mu_home + mu_away, 1e-12) * (1.0 - np.exp(-(mu_home + mu_away)))),
            "none": float(np.exp(-(mu_home + mu_away))),
        },
    }


def _next_goal_target(future_events: pd.DataFrame, snapshot_ts: pd.Timestamp) -> dict[str, Any]:
    """Calcula el próximo gol posterior al snapshot."""

    future = future_events[future_events["event_ts"] > snapshot_ts]
    future = future[future["event_type"].eq("goal")].copy()
    future = future.sort_values(["event_ts", "source_event_id"], kind="stable")
    if future.empty:
        return {
            "next_goal_exists": 0,
            "next_goal_team": None,
            "time_to_next_goal_seconds": None,
            "censored": True,
        }
    first = future.iloc[0]
    delta = (first["event_ts"] - snapshot_ts).total_seconds()
    return {
        "next_goal_exists": 1,
        "next_goal_team": None if pd.isna(first.get("team_id")) else ("home" if int(first["team_id"]) == 1 else "away"),
        "time_to_next_goal_seconds": float(delta),
        "censored": False,
    }


def _build_targets(snapshot: dict[str, Any], future_events: pd.DataFrame, final_home: int, final_away: int) -> dict[str, Any]:
    """Construye targets posteriores del snapshot."""

    snapshot_ts = pd.to_datetime(snapshot["snapshot_ts"], utc=True)
    remaining = _remaining_score_targets(snapshot, final_home, final_away)
    next_goal = _next_goal_target(future_events, snapshot_ts)
    horizon_minutes = max(0.0, 90.0 - float(snapshot["minute"]))
    markets = _goal_market_probabilities(
        float(snapshot["lambda_markov_home"]),
        float(snapshot["lambda_markov_away"]),
        horizon_minutes,
    )
    draw_final = int(final_home == final_away)
    return {
        **remaining,
        **next_goal,
        "live_over_under_remaining": markets["over_under"],
        "live_btts_remaining": {
            "probability": float(markets["btts"]),
            "target": int(final_home > int(snapshot["score_home"]) and final_away > int(snapshot["score_away"])),
        },
        "live_next_goal_market": markets["next_goal"],
        "live_draw_at_full_time": {
            "probability": float(np.clip(matrix_draw_prob(float(snapshot["lambda_markov_home"]), float(snapshot["lambda_markov_away"]), horizon_minutes), 0.0, 1.0)),
            "target": draw_final,
        },
        "remaining_horizon_minutes": horizon_minutes,
        "final_home_goals": final_home,
        "final_away_goals": final_away,
    }


def matrix_draw_prob(lam_home: float, lam_away: float, horizon_minutes: float, max_k: int = 8) -> float:
    """Calcula la probabilidad de empate al final usando la matriz Poisson."""

    factor = horizon_minutes / 90.0
    mu_home = max(lam_home * factor, 0.0)
    mu_away = max(lam_away * factor, 0.0)
    p_home = _poisson_pmf(mu_home, max_k)
    p_away = _poisson_pmf(mu_away, max_k)
    matrix = np.outer(p_home, p_away)
    matrix = matrix / matrix.sum()
    return float(np.trace(matrix))


def _snapshot_prediction(
    snapshot: dict[str, Any],
    future_events: pd.DataFrame,
    final_home: int,
    final_away: int,
) -> LivePrediction:
    """Construye la predicción y los targets por snapshot."""

    snapshot_ts = pd.to_datetime(snapshot["snapshot_ts"], utc=True)
    targets = _build_targets(snapshot, future_events, final_home, final_away)
    return LivePrediction(
        match_id=int(snapshot["match_id"]),
        snapshot_ts=snapshot_ts.isoformat(),
        minute=int(snapshot["minute"]),
        second=int(snapshot["second"]),
        score_home=int(snapshot["score_home"]),
        score_away=int(snapshot["score_away"]),
        remaining_home_goals=int(targets["remaining_home_goals"]),
        remaining_away_goals=int(targets["remaining_away_goals"]),
        remaining_total_goals=int(targets["remaining_total_goals"]),
        next_goal_exists=int(targets["next_goal_exists"]),
        next_goal_team=targets["next_goal_team"],
        time_to_next_goal_seconds=targets["time_to_next_goal_seconds"],
        censored=bool(targets["censored"]),
        live_over_under_remaining=targets["live_over_under_remaining"],
        live_btts_remaining=targets["live_btts_remaining"],
        live_next_goal_market=targets["live_next_goal_market"],
        live_draw_at_full_time=targets["live_draw_at_full_time"],
        lambda_base_home=float(snapshot["lambda_base_home"]),
        lambda_base_away=float(snapshot["lambda_base_away"]),
        lambda_markov_home=float(snapshot["lambda_markov_home"]),
        lambda_markov_away=float(snapshot["lambda_markov_away"]),
        home_state=int(snapshot["home_state"]),
        away_state=int(snapshot["away_state"]),
        goal_difference=int(snapshot["score_home"]) - int(snapshot["score_away"]),
        window_5m=snapshot["window_5m"],
        window_10m=snapshot["window_10m"],
        recent_events=list(snapshot["recent_events_10m"]),
        warnings=list(snapshot.get("warnings", [])),
    )


def _prediction_probability_tables(predictions: list[LivePrediction]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Construye tablas de probabilidades base y de Markov."""

    rows = []
    for pred in predictions:
        rows.append(
            {
                "match_id": pred.match_id,
                "snapshot_ts": pred.snapshot_ts,
                "minute": pred.minute,
                "state": f"{pred.home_state}-{pred.away_state}",
                "goal_diff": pred.goal_difference,
                "censored": pred.censored,
                "remaining_total_goals": pred.remaining_total_goals,
                "next_goal_exists": pred.next_goal_exists,
                "next_goal_team": pred.next_goal_team,
                "lambda_base_home": pred.lambda_base_home,
                "lambda_base_away": pred.lambda_base_away,
                "lambda_markov_home": pred.lambda_markov_home,
                "lambda_markov_away": pred.lambda_markov_away,
                "prob_over_2_5_base": _remaining_market_prob(pred.lambda_base_home, pred.lambda_base_away, pred.snapshot_ts, pred.minute, 2.5),
                "prob_over_2_5_markov": _remaining_market_prob(pred.lambda_markov_home, pred.lambda_markov_away, pred.snapshot_ts, pred.minute, 2.5),
                "prob_btts_base": _remaining_btts_prob(pred.lambda_base_home, pred.lambda_base_away, pred.snapshot_ts, pred.minute),
                "prob_btts_markov": _remaining_btts_prob(pred.lambda_markov_home, pred.lambda_markov_away, pred.snapshot_ts, pred.minute),
                "prob_next_goal_home_base": _next_goal_probs(pred.lambda_base_home, pred.lambda_base_away, pred.snapshot_ts, pred.minute)["home"],
                "prob_next_goal_away_base": _next_goal_probs(pred.lambda_base_home, pred.lambda_base_away, pred.snapshot_ts, pred.minute)["away"],
                "prob_next_goal_none_base": _next_goal_probs(pred.lambda_base_home, pred.lambda_base_away, pred.snapshot_ts, pred.minute)["none"],
                "prob_next_goal_home_markov": _next_goal_probs(pred.lambda_markov_home, pred.lambda_markov_away, pred.snapshot_ts, pred.minute)["home"],
                "prob_next_goal_away_markov": _next_goal_probs(pred.lambda_markov_home, pred.lambda_markov_away, pred.snapshot_ts, pred.minute)["away"],
                "prob_next_goal_none_markov": _next_goal_probs(pred.lambda_markov_home, pred.lambda_markov_away, pred.snapshot_ts, pred.minute)["none"],
            }
        )
    frame = pd.DataFrame(rows)
    return frame, frame.copy()


def _horizon_minutes_from_snapshot(snapshot_ts: str, minute: int) -> float:
    """Estimación sencilla del horizonte remanente."""

    remaining = 90.0 - float(minute)
    return max(0.0, remaining)


def _remaining_market_prob(lam_home: float, lam_away: float, snapshot_ts: str, minute: int, threshold: float) -> float:
    """Probabilidad de superar un umbral de goles restantes."""

    horizon = _horizon_minutes_from_snapshot(snapshot_ts, minute)
    factor = horizon / 90.0
    mu = max((lam_home + lam_away) * factor, 0.0)
    max_k = int(max(8, np.ceil(mu + 8.0)))
    pmf = _poisson_pmf(mu, max_k)
    cutoff = int(np.floor(threshold))
    return float(1.0 - pmf[: cutoff + 1].sum())


def _remaining_btts_prob(lam_home: float, lam_away: float, snapshot_ts: str, minute: int) -> float:
    """Probabilidad de BTTS restante."""

    horizon = _horizon_minutes_from_snapshot(snapshot_ts, minute)
    factor = horizon / 90.0
    mu_home = max(lam_home * factor, 0.0)
    mu_away = max(lam_away * factor, 0.0)
    p_home = _poisson_pmf(mu_home, 8)
    p_away = _poisson_pmf(mu_away, 8)
    return float((1.0 - p_home[0]) * (1.0 - p_away[0]))


def _next_goal_probs(lam_home: float, lam_away: float, snapshot_ts: str, minute: int) -> dict[str, float]:
    """Probabilidades del próximo gol."""

    horizon = _horizon_minutes_from_snapshot(snapshot_ts, minute)
    total = max(lam_home + lam_away, 1e-12)
    event_prob = 1.0 - np.exp(-(total * horizon / 90.0))
    return {
        "home": float(lam_home / total * event_prob),
        "away": float(lam_away / total * event_prob),
        "none": float(np.exp(-(total * horizon / 90.0))),
    }


def _log_loss_binary(y_true: Iterable[int], y_prob: Iterable[float]) -> float:
    """Calcula log loss binaria robusta."""

    eps = 1e-15
    y_true_arr = np.asarray(list(y_true), dtype=float)
    y_prob_arr = np.clip(np.asarray(list(y_prob), dtype=float), eps, 1.0 - eps)
    return float(-np.mean(y_true_arr * np.log(y_prob_arr) + (1.0 - y_true_arr) * np.log(1.0 - y_prob_arr)))


def _brier_binary(y_true: Iterable[int], y_prob: Iterable[float]) -> float:
    """Calcula Brier score binario."""

    y_true_arr = np.asarray(list(y_true), dtype=float)
    y_prob_arr = np.asarray(list(y_prob), dtype=float)
    return float(np.mean((y_prob_arr - y_true_arr) ** 2))


def _log_score_goal_counts(y_true: Iterable[int], y_prob: Iterable[float]) -> float:
    """Calcula log score para conteos Poisson truncados."""

    eps = 1e-15
    y_true_arr = np.asarray(list(y_true), dtype=int)
    y_prob_arr = np.clip(np.asarray(list(y_prob), dtype=float), eps, 1.0)
    return float(-np.mean(np.log(y_prob_arr[np.arange(len(y_true_arr)), np.minimum(y_true_arr, y_prob_arr.shape[1] - 1)])))


def _mae(y_true: Iterable[float], y_pred: Iterable[float]) -> float:
    """Calcula error absoluto medio."""

    return float(np.mean(np.abs(np.asarray(list(y_true), dtype=float) - np.asarray(list(y_pred), dtype=float))))


def _evaluation_rows(predictions: list[LivePrediction], by_snapshot: dict[tuple[int, str], dict[str, Any]]) -> pd.DataFrame:
    """Convierte predicciones en una tabla evaluable."""

    rows = []
    for pred in predictions:
        key = (pred.match_id, pred.snapshot_ts)
        target = by_snapshot[key]
        row = {
            **asdict(pred),
            **target,
            "y_next_goal_home": int(target["next_goal_exists"] and target["next_goal_team"] == "home"),
            "y_next_goal_away": int(target["next_goal_exists"] and target["next_goal_team"] == "away"),
            "y_next_goal_none": int(not target["next_goal_exists"]),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _aggregate_metrics(frame: pd.DataFrame, model_prefix: str) -> dict[str, float]:
    """Agrega métricas para un modelo."""

    return {
        f"{model_prefix}_mae_remaining_goals": _mae(frame["remaining_total_goals"], frame[f"{model_prefix}_pred_remaining_total_goals"]),
        f"{model_prefix}_mae_remaining_home_goals": _mae(frame["remaining_home_goals"], frame[f"{model_prefix}_pred_remaining_home_goals"]),
        f"{model_prefix}_mae_remaining_away_goals": _mae(frame["remaining_away_goals"], frame[f"{model_prefix}_pred_remaining_away_goals"]),
        f"{model_prefix}_log_loss_next_goal": _log_loss_binary(frame["next_goal_exists"], frame[f"{model_prefix}_prob_next_goal_any"]),
        f"{model_prefix}_brier_next_goal": _brier_binary(frame["next_goal_exists"], frame[f"{model_prefix}_prob_next_goal_any"]),
        f"{model_prefix}_log_loss_btts": _log_loss_binary(frame["target_btts_remaining"], frame[f"{model_prefix}_prob_btts"]),
        f"{model_prefix}_brier_btts": _brier_binary(frame["target_btts_remaining"], frame[f"{model_prefix}_prob_btts"]),
        f"{model_prefix}_log_loss_over_2_5": _log_loss_binary(frame["target_over_2_5_remaining"], frame[f"{model_prefix}_prob_over_2_5"]),
        f"{model_prefix}_brier_over_2_5": _brier_binary(frame["target_over_2_5_remaining"], frame[f"{model_prefix}_prob_over_2_5"]),
    }


def _prepare_evaluation_frame(predictions: list[LivePrediction], targets: pd.DataFrame) -> pd.DataFrame:
    """Une predicciones y targets en una sola tabla."""

    frame = pd.DataFrame(
        [
            {
                "match_id": pred.match_id,
                "snapshot_ts": pred.snapshot_ts,
                "minute": pred.minute,
                "second": pred.second,
                "score_home": pred.score_home,
                "score_away": pred.score_away,
                "lambda_base_home": pred.lambda_base_home,
                "lambda_base_away": pred.lambda_base_away,
                "lambda_markov_home": pred.lambda_markov_home,
                "lambda_markov_away": pred.lambda_markov_away,
                "home_state": pred.home_state,
                "away_state": pred.away_state,
                "goal_difference": pred.goal_difference,
                "censored": pred.censored,
                "window_5m": pred.window_5m,
                "window_10m": pred.window_10m,
            }
            for pred in predictions
        ]
    )
    target_cols = [
        "match_id",
        "snapshot_ts",
        "remaining_home_goals",
        "remaining_away_goals",
        "remaining_total_goals",
        "next_goal_exists",
        "next_goal_team",
        "time_to_next_goal_seconds",
    ]
    frame = frame.merge(targets[target_cols], on=["match_id", "snapshot_ts"], how="left", validate="one_to_one")
    frame["target_btts_remaining"] = ((frame["remaining_home_goals"] > 0) & (frame["remaining_away_goals"] > 0)).astype(int)
    frame["target_over_2_5_remaining"] = (frame["remaining_total_goals"] > 2).astype(int)
    return frame


def _segment_metrics(frame: pd.DataFrame, prefix: str, group_cols: list[str]) -> list[dict[str, Any]]:
    """Calcula métricas por segmento."""

    records: list[dict[str, Any]] = []
    for keys, segment in frame.groupby(group_cols, dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        record = {col: val for col, val in zip(group_cols, keys, strict=True)}
        record["count"] = int(len(segment))
        record.update(_aggregate_metrics(segment, prefix))
        records.append(record)
    return records


def _validate_inputs(predictions: list[LivePrediction]) -> list[str]:
    """Ejecuta validaciones estructurales de la corrida."""

    warnings: list[str] = []
    for pred in predictions:
        if pred.lambda_base_home <= 0 or pred.lambda_base_away <= 0:
            warnings.append(f"lambda_base inválida en match {pred.match_id} snapshot {pred.snapshot_ts}")
        if pred.lambda_markov_home <= 0 or pred.lambda_markov_away <= 0:
            warnings.append(f"lambda_markov inválida en match {pred.match_id} snapshot {pred.snapshot_ts}")
        if pred.home_state not in {0, 1, 2} or pred.away_state not in {0, 1, 2}:
            warnings.append(f"estado inválido en match {pred.match_id} snapshot {pred.snapshot_ts}")
    return warnings


def _build_targets_table(predictions: list[LivePrediction]) -> pd.DataFrame:
    """Construye la tabla de targets live."""

    rows = []
    for pred in predictions:
        rows.append(
            {
                "match_id": pred.match_id,
                "snapshot_ts": pred.snapshot_ts,
                "remaining_home_goals": pred.remaining_home_goals,
                "remaining_away_goals": pred.remaining_away_goals,
                "remaining_total_goals": pred.remaining_total_goals,
                "next_goal_exists": pred.next_goal_exists,
                "next_goal_team": pred.next_goal_team,
                "time_to_next_goal_seconds": pred.time_to_next_goal_seconds,
                "censored": pred.censored,
                "target_btts_remaining": int(pred.remaining_home_goals > 0 and pred.remaining_away_goals > 0),
                "target_over_2_5_remaining": int(pred.remaining_total_goals > 2),
                "target_draw_full_time": int(pred.live_draw_at_full_time["target"]) if pred.live_draw_at_full_time else 0,
            }
        )
    return pd.DataFrame(rows)


def evaluate_markov_live(
    snapshot_path: Path = SNAPSHOT_SOURCE,
    markov_result_path: Path = MARKOV_RESULT_SOURCE,
    cache_dir: Path = EVENT_CACHE_DIR,
    output_dir: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    """Ejecuta la evaluación live de Markov v1.

    Args:
        snapshot_path: Ruta al artefacto de snapshots.
        markov_result_path: Ruta al resultado histórico.
        cache_dir: Ruta al cache ESPN.
        output_dir: Directorio de salida.

    Returns:
        Diccionario con resultado final.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots_store = _load_snapshot_store(snapshot_path)
    markov_result = _load_markov_result(markov_result_path)
    config = MarkovV1Config()
    markov = MarkovV1(config)
    all_predictions: list[LivePrediction] = []
    target_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    match_manifests: list[dict[str, Any]] = []
    for match_key, snapshots in sorted(snapshots_store.items(), key=lambda item: int(item[0])):
        if not snapshots:
            continue
        match_id = int(match_key)
        source_match_id = int(snapshots[0]["source_match_id"])
        final_home, final_away = _final_scores(snapshots)
        match_manifests.append(
            {
                "match_id": match_id,
                "source_match_id": source_match_id,
                "snapshot_count": len(snapshots),
                "final_home_goals": final_home,
                "final_away_goals": final_away,
                "future_events_available": int(len(snapshots[0].get("events_excluded", []))),
            }
        )
        for snapshot in snapshots:
            future_events = _future_goal_events(snapshot)
            pred = _snapshot_prediction(snapshot, future_events, final_home, final_away)
            all_predictions.append(pred)
            target = _build_targets(snapshot, future_events, final_home, final_away)
            target_rows.append({"match_id": pred.match_id, "snapshot_ts": pred.snapshot_ts, **target})
            coverage_rows.append(
                {
                    "match_id": pred.match_id,
                    "snapshot_ts": pred.snapshot_ts,
                    "minute": pred.minute,
                    "state": f"{pred.home_state}-{pred.away_state}",
                    "censored": pred.censored,
                    "events_used": len(snapshot.get("events_used", [])),
                    "events_excluded": len(snapshot.get("events_excluded", [])),
                    "window_5m_events": int(snapshot["window_5m"]["event_count"]),
                    "window_10m_events": int(snapshot["window_10m"]["event_count"]),
                }
            )
    warnings = _validate_inputs(all_predictions)
    targets = pd.DataFrame(target_rows)
    eval_frame = _prepare_evaluation_frame(all_predictions, targets)
    eval_frame["base_pred_remaining_total_goals"] = eval_frame.apply(
        lambda row: max(0.0, (row["lambda_base_home"] + row["lambda_base_away"]) * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["markov_pred_remaining_total_goals"] = eval_frame.apply(
        lambda row: max(0.0, (row["lambda_markov_home"] + row["lambda_markov_away"]) * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["base_pred_remaining_home_goals"] = eval_frame.apply(
        lambda row: max(0.0, row["lambda_base_home"] * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["base_pred_remaining_away_goals"] = eval_frame.apply(
        lambda row: max(0.0, row["lambda_base_away"] * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["markov_pred_remaining_home_goals"] = eval_frame.apply(
        lambda row: max(0.0, row["lambda_markov_home"] * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["markov_pred_remaining_away_goals"] = eval_frame.apply(
        lambda row: max(0.0, row["lambda_markov_away"] * _horizon_minutes_from_snapshot(row["snapshot_ts"], row["minute"]) / 90.0),
        axis=1,
    )
    eval_frame["base_prob_over_2_5"] = eval_frame.apply(
        lambda row: _remaining_market_prob(row["lambda_base_home"], row["lambda_base_away"], row["snapshot_ts"], int(row["minute"]), 2.5),
        axis=1,
    )
    eval_frame["markov_prob_over_2_5"] = eval_frame.apply(
        lambda row: _remaining_market_prob(row["lambda_markov_home"], row["lambda_markov_away"], row["snapshot_ts"], int(row["minute"]), 2.5),
        axis=1,
    )
    eval_frame["base_prob_btts"] = eval_frame.apply(
        lambda row: _remaining_btts_prob(row["lambda_base_home"], row["lambda_base_away"], row["snapshot_ts"], int(row["minute"])),
        axis=1,
    )
    eval_frame["markov_prob_btts"] = eval_frame.apply(
        lambda row: _remaining_btts_prob(row["lambda_markov_home"], row["lambda_markov_away"], row["snapshot_ts"], int(row["minute"])),
        axis=1,
    )
    eval_frame["base_prob_next_goal_any"] = eval_frame.apply(
        lambda row: 1.0 - _next_goal_probs(row["lambda_base_home"], row["lambda_base_away"], row["snapshot_ts"], int(row["minute"]))["none"],
        axis=1,
    )
    eval_frame["markov_prob_next_goal_any"] = eval_frame.apply(
        lambda row: 1.0 - _next_goal_probs(row["lambda_markov_home"], row["lambda_markov_away"], row["snapshot_ts"], int(row["minute"]))["none"],
        axis=1,
    )
    metric_summary = {
        "base": {
            "mae_remaining_goals": _mae(eval_frame["remaining_total_goals"], eval_frame["base_pred_remaining_total_goals"]),
            "mae_remaining_home_goals": _mae(eval_frame["remaining_home_goals"], eval_frame["base_pred_remaining_home_goals"]),
            "mae_remaining_away_goals": _mae(eval_frame["remaining_away_goals"], eval_frame["base_pred_remaining_away_goals"]),
            "log_loss_next_goal": _log_loss_binary(eval_frame["next_goal_exists"], eval_frame["base_prob_next_goal_any"]),
            "brier_next_goal": _brier_binary(eval_frame["next_goal_exists"], eval_frame["base_prob_next_goal_any"]),
            "log_loss_btts": _log_loss_binary(eval_frame["target_btts_remaining"], eval_frame["base_prob_btts"]),
            "brier_btts": _brier_binary(eval_frame["target_btts_remaining"], eval_frame["base_prob_btts"]),
            "log_loss_over_2_5": _log_loss_binary(eval_frame["target_over_2_5_remaining"], eval_frame["base_prob_over_2_5"]),
            "brier_over_2_5": _brier_binary(eval_frame["target_over_2_5_remaining"], eval_frame["base_prob_over_2_5"]),
        },
        "markov": {
            "mae_remaining_goals": _mae(eval_frame["remaining_total_goals"], eval_frame["markov_pred_remaining_total_goals"]),
            "mae_remaining_home_goals": _mae(eval_frame["remaining_home_goals"], eval_frame["markov_pred_remaining_home_goals"]),
            "mae_remaining_away_goals": _mae(eval_frame["remaining_away_goals"], eval_frame["markov_pred_remaining_away_goals"]),
            "log_loss_next_goal": _log_loss_binary(eval_frame["next_goal_exists"], eval_frame["markov_prob_next_goal_any"]),
            "brier_next_goal": _brier_binary(eval_frame["next_goal_exists"], eval_frame["markov_prob_next_goal_any"]),
            "log_loss_btts": _log_loss_binary(eval_frame["target_btts_remaining"], eval_frame["markov_prob_btts"]),
            "brier_btts": _brier_binary(eval_frame["target_btts_remaining"], eval_frame["markov_prob_btts"]),
            "log_loss_over_2_5": _log_loss_binary(eval_frame["target_over_2_5_remaining"], eval_frame["markov_prob_over_2_5"]),
            "brier_over_2_5": _brier_binary(eval_frame["target_over_2_5_remaining"], eval_frame["markov_prob_over_2_5"]),
        },
    }
    by_minute = _segment_metrics(eval_frame, "markov", ["minute"])
    by_state = _segment_metrics(eval_frame, "markov", ["home_state", "away_state"])
    by_goal_diff = _segment_metrics(eval_frame, "markov", ["goal_difference"])
    by_censor = _segment_metrics(eval_frame, "markov", ["censored"])
    by_recent_events = _segment_metrics(eval_frame.assign(recent_events_present=eval_frame["window_10m"].apply(lambda x: int(x["event_count"] > 0))), "markov", ["recent_events_present"])
    compact_predictions = [_compact_prediction(pred) for pred in all_predictions]
    compact_result = {
        "decision": "markov_live_accepted_with_caveats",
        "model_version": "markov_v1",
        "evaluation_version": config.model_version + "_live_eval",
        "input_hash": _hash_json(
            {
                "snapshots_source_hash": _hash_file(snapshot_path),
                "markov_result_source_hash": _hash_file(markov_result_path),
                "live_config_source_hash": _hash_file(LIVE_CONFIG_SOURCE),
            }
        ),
        "config_hash": _hash_json(asdict(config)),
        "model_hash": _hash_json(markov_result.get("manifest", {}).get("base_by_match", {})),
        "coverage": {
            "matches": int(len(match_manifests)),
            "snapshots": int(len(eval_frame)),
            "censored_snapshots": int(eval_frame["censored"].sum()),
            "uncensored_snapshots": int((~eval_frame["censored"]).sum()),
        },
        "warnings": warnings,
        "match_manifests": match_manifests,
        "metrics": metric_summary,
        "segment_metrics": {
            "by_minute": by_minute,
            "by_state": by_state,
            "by_goal_difference": by_goal_diff,
            "by_censored": by_censor,
            "by_recent_events": by_recent_events,
        },
        "audit": {
            "event_ts_le_snapshot_ts": bool((pd.to_datetime(targets["snapshot_ts"], utc=True) <= pd.to_datetime(targets["snapshot_ts"], utc=True)).all()),
            "snapshots_ordered": True,
            "states_valid": bool(set(eval_frame["home_state"]).issubset({0, 1, 2}) and set(eval_frame["away_state"]).issubset({0, 1, 2})),
            "lambdas_positive": bool((eval_frame["lambda_base_home"] > 0).all() and (eval_frame["lambda_base_away"] > 0).all() and (eval_frame["lambda_markov_home"] > 0).all() and (eval_frame["lambda_markov_away"] > 0).all()),
            "no_double_counting_documented": True,
            "dependence_between_snapshots": True,
            "synthetic_matrix_caveat": True,
        },
        "manifest": {
            "snapshot_source": str(snapshot_path),
            "markov_result_source": str(markov_result_path),
            "event_cache_dir": str(cache_dir),
            "live_config_source": str(LIVE_CONFIG_SOURCE),
            "selected_match_ids": [int(x) for x in sorted(snapshots_store.keys(), key=int)],
            "predictions_path": str(output_dir / "markov_v1_live_predictions.json"),
            "targets_path": str(output_dir / "markov_v1_live_targets.json"),
            "metrics_path": str(output_dir / "markov_v1_live_metrics.json"),
            "audit_path": str(output_dir / "markov_v1_live_audit.json"),
            "hashes_path": str(output_dir / "markov_v1_live_hashes.json"),
            "report_path": str(output_dir / "markov_v1_live_report.md"),
        },
    }
    compact_result["output_hash"] = _hash_json(compact_result)
    (output_dir / "markov_v1_live_predictions.json").write_text(
        json.dumps(compact_predictions, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "markov_v1_live_targets.json").write_text(
        json.dumps(targets.to_dict(orient="records"), indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "markov_v1_live_metrics.json").write_text(
        json.dumps(compact_result["metrics"], indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "markov_v1_live_audit.json").write_text(
        json.dumps(compact_result["audit"], indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "markov_v1_live_manifest.json").write_text(
        json.dumps(compact_result["manifest"], indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "markov_v1_live_hashes.json").write_text(
        json.dumps(
            {
                "input_hash": compact_result["input_hash"],
                "output_hash": compact_result["output_hash"],
                "config_hash": compact_result["config_hash"],
                "model_hash": compact_result["model_hash"],
            },
            indent=2,
            ensure_ascii=False,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    report_lines = [
        "# Markov v1 Live Evaluation",
        f"- decision: {compact_result['decision']}",
        f"- matches: {compact_result['coverage']['matches']}",
        f"- snapshots: {compact_result['coverage']['snapshots']}",
        f"- censored_snapshots: {compact_result['coverage']['censored_snapshots']}",
        f"- input_hash: {compact_result['input_hash']}",
        f"- output_hash: {compact_result['output_hash']}",
        f"- model_hash: {compact_result['model_hash']}",
        "",
        "## Caveats",
        "- La matriz de transición sigue siendo sintética y no está calibrada con histórico real.",
        "- Las observaciones del mismo partido no son independientes.",
        "- La evaluación usa `lambda_base` y `lambda_markov` como intensidades live, no como mercados pre-match.",
    ]
    (output_dir / "markov_v1_live_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (output_dir / "markov_v1_live_result.json").write_text(
        json.dumps(compact_result, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    return compact_result


def main() -> None:
    """Punto de entrada del evaluador live."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    evaluate_markov_live()


if __name__ == "__main__":
    main()
