"""Genera predicciones Markov candidatas congeladas para una cohorte futura.

El cálculo usa perfiles causales, transiciones de Fase 40 y emisiones de gol
por estado. Sólo estima ``first_half_goal`` y nunca modifica el router oficial.

Requirements:
    - joblib
    - scikit-learn

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib

from scripts.run_phase_63_initial_state_calibration import (
    FEATURES,
    STATES,
    _matrix,
    _profile,
    _records,
    _with_profiles,
)
from src.state_labeling_v1 import StateLabelingConfig, label

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
COHORT = ROOT / "artifacts/phase_62_independent_cohort_lock_v1/cohort.json"
MODEL = ROOT / "artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transition_matrices.json"
BASELINE = ROOT / "artifacts/phase_56_multileague_upcoming_flow_v1/audit.json"
OUTPUT = ROOT / "artifacts/phase_63_frozen_markov_candidate_v1"
LOGGER = logging.getLogger(__name__)
SMOOTHING = 2.0


def _read(path: Path) -> Any:
    """Lee JSON UTF-8 desde un artefacto versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    """Calcula el hash del archivo para provenance."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _time(value: str) -> datetime:
    """Normaliza una fecha ISO a datetime con zona horaria."""

    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _state_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Añade el estado etiquetado de cada ventana histórica."""

    config = StateLabelingConfig()
    return [{**row, "state": label(row, config)[0]} for row in rows]


def _match_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega estado inicial y target de gol por partido histórico."""

    records = _records(rows)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[int(row["match_id"])].append(row)
    output = []
    for match_id, teams in grouped.items():
        if len(teams) != 2:
            continue
        ordered = sorted(teams, key=lambda item: not bool(item["is_home"]))
        windows = [row for row in rows if int(row["match_id"]) == match_id and int(row["window_index"]) < 3]
        output.append({"match_id": match_id, "match_date": ordered[0]["match_date"], "home_state": ordered[0]["target"], "away_state": ordered[1]["target"], "first_half_goal": bool(sum(float(row["goals"]) for row in windows) > 0), "league_slug": ordered[0]["league_slug"]})
    return output


def _feature_row(team: int, is_home: bool, cutoff: datetime, records: list[dict[str, Any]]) -> dict[str, float]:
    """Construye el perfil causal de un equipo antes del kickoff."""

    history = [row for row in records if int(row["team_id"]) == team and _time(row["match_date"]) < cutoff]
    history = sorted(history, key=lambda row: (_time(row["match_date"]), int(row["match_id"])))
    profile = _profile(history[-5:])
    return {"is_home": float(is_home), **profile}


def _probabilities(model: Any, features: dict[str, float]) -> dict[str, float]:
    """Alinea la salida del clasificador a las cuatro clases del contrato."""

    raw = model.predict_proba(_matrix([features]))[0]
    classes = list(model[-1].classes_)
    values = {state: 1e-9 for state in STATES}
    for index, state in enumerate(classes):
        values[str(state)] = float(raw[index])
    total = sum(values.values())
    return {state: value / total for state, value in values.items()}


def _transition_index(matrices: dict[str, list[dict[str, Any]]]) -> dict[str, dict[tuple[Any, ...], dict[str, Any]]]:
    """Indexa las matrices por tier y contexto."""

    return {tier: {tuple(item["context"]): item for item in values} for tier, values in matrices.items()}


def _transition(index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], league: str, team: int, venue: bool, window: int, state: str, opponent: str) -> dict[str, float]:
    """Obtiene transición con backoff team→liga→ventana→global."""

    keys = [("team", (team, league, venue, window, "level", state, opponent)), ("competition", (league, venue, window, "level", state, opponent)), ("window", (league, window, state, opponent)), ("global", (state, opponent))]
    for tier, key in keys:
        item = index.get(tier, {}).get(key)
        if item and int(item.get("support", 0)) >= (12 if tier == "team" else 10 if tier == "competition" else 8 if tier == "window" else 1):
            return {state_name: float(value) for state_name, value in item["probabilities"].items()}
    return {state: 1.0 / len(STATES) for state in STATES}


def _rate_stats(rows: list[dict[str, Any]], cutoff: datetime) -> dict[str, dict[str, float]]:
    """Calcula tasas suavizadas por liga, localía, ventana y estado."""

    selected = [row for row in rows if _time(row["match_date"]) < cutoff]
    totals: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    states: dict[tuple[str, bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in selected:
        key = (str(row["league_slug"]), bool(row["is_home"]), int(row["window_index"]))
        totals[key][0] += float(row["goals"])
        totals[key][1] += 1.0
        states[(*key, str(row["state"]))][0] += float(row["goals"])
        states[(*key, str(row["state"]))][1] += 1.0
    output = {}
    for key, values in states.items():
        parent = totals[key[:3]]
        base = (parent[0] + SMOOTHING) / (parent[1] + SMOOTHING)
        output[json.dumps(key, separators=(",", ":"))] = (values[0] + SMOOTHING * base) / (values[1] + SMOOTHING)
    return {"state": output, "overall": {json.dumps(key, separators=(",", ":")): (value[0] + SMOOTHING) / (value[1] + SMOOTHING) for key, value in totals.items()}}


def _lookup_rate(stats: dict[str, dict[str, float]], key: tuple[Any, ...], state: str | None = None) -> float:
    """Recupera tasa de estado u overall con backoff global."""

    encoded = json.dumps((*key, state), separators=(",", ":")) if state else json.dumps(key, separators=(",", ":"))
    pool = stats["state"] if state else stats["overall"]
    if encoded in pool:
        return float(pool[encoded])
    candidates = [value for raw, value in pool.items() if raw.endswith(f",{key[-1]}]")] if not state else []
    return float(sum(candidates) / len(candidates)) if candidates else 0.10


def _markov_probability(fixture: dict[str, Any], initial: tuple[dict[str, float], dict[str, float]], rows: list[dict[str, Any]], index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], cutoff: datetime) -> tuple[float, float, int]:
    """Propaga supervivencia sin gol durante las tres ventanas iniciales."""

    stats = _rate_stats(rows, cutoff)
    home_probs, away_probs = initial
    alive: dict[tuple[str, str], float] = {(home, away): home_probs[home] * away_probs[away] for home in STATES for away in STATES}
    baseline_rate = 0.0
    for window in range(3):
        next_alive: dict[tuple[str, str], float] = defaultdict(float)
        for (home_state, away_state), weight in alive.items():
            home_key = (fixture["league_slug"], True, window)
            away_key = (fixture["league_slug"], False, window)
            home_rate = _lookup_rate(stats, home_key, home_state)
            away_rate = _lookup_rate(stats, away_key, away_state)
            baseline_rate += weight * (_lookup_rate(stats, home_key) + _lookup_rate(stats, away_key))
            survived = weight * math.exp(-(home_rate + away_rate))
            if window == 2:
                next_alive[(home_state, away_state)] += survived
                continue
            home_next = _transition(index, str(fixture["league_slug"]), int(fixture["home_team_id"]), True, window, home_state, away_state)
            away_next = _transition(index, str(fixture["league_slug"]), int(fixture["away_team_id"]), False, window, away_state, home_state)
            for next_home in STATES:
                for next_away in STATES:
                    next_alive[(next_home, next_away)] += survived * home_next[next_home] * away_next[next_away]
        alive = next_alive
    return max(0.0, min(1.0, 1.0 - sum(alive.values()))), baseline_rate, len(alive)


def _entropy(probabilities: dict[str, float]) -> float:
    """Calcula entropía natural del estado inicial."""

    return -sum(value * math.log(value) for value in probabilities.values() if value > 0.0)


def _baseline_map(audit: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Indexa las salidas baseline previas por partido."""

    return {int(item["fixture"]["match_id"]): item for item in audit["results"] if "prediction" in item}


def _predict_fixture(fixture: dict[str, Any], records: list[dict[str, Any]], windows: list[dict[str, Any]], model: Any, index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], baseline: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Genera una salida aislada y completamente pre-match."""

    cutoff = _time(fixture["kickoff_ts"])
    home_features = _feature_row(int(fixture["home_team_id"]), True, cutoff, records)
    away_features = _feature_row(int(fixture["away_team_id"]), False, cutoff, records)
    home_probs, away_probs = _probabilities(model, home_features), _probabilities(model, away_features)
    probability, baseline_rate, support = _markov_probability(fixture, (home_probs, away_probs), windows, index, cutoff)
    item = baseline[int(fixture["match_id"])]
    return {"fixture": fixture, "baseline_reference": item["prediction"], "state_0": {"home": home_probs, "away": away_probs, "home_history_count": home_features["history_count"], "away_history_count": away_features["history_count"], "home_entropy": _entropy(home_probs), "away_entropy": _entropy(away_probs)}, "markov_residual": {"market": "first_half_goal", "probability": probability, "baseline_first_half_goal_no_state": 1.0 - math.exp(-baseline_rate), "baseline_rate_first_half": baseline_rate, "transition_supporting_states": support}, "target_data_used": False, "results_observed": False, "play_by_play_requested": False, "classification": "frozen_candidate_not_promoted"}


def run() -> dict[str, Any]:
    """Congela las nueve predicciones candidatas y publica su auditoría."""

    windows = _state_rows(_read(WINDOWS))
    records = _with_profiles(_records(windows))
    cohort = _read(COHORT)
    model = joblib.load(MODEL)
    index = _transition_index(_read(TRANSITIONS))
    baseline = _baseline_map(_read(BASELINE))
    predictions = [_predict_fixture(fixture, records, windows, model, index, baseline) for fixture in cohort["fixtures"]]
    audit = {"classification": "frozen_markov_candidate_predictions_ready", "prediction_count": len(predictions), "market": "first_half_goal", "cohort_hash": cohort["cohort_hash"], "cohort_locked_at_utc": cohort["locked_at_utc"], "target_data_used": False, "results_observed": False, "play_by_play_requested": False, "official_router_modified": False, "markov_promoted": False, "state0_missing_class_support": ["repliegue"], "model_hash": _sha256(MODEL), "windows_hash": _sha256(WINDOWS), "transition_matrices_hash": _sha256(TRANSITIONS)}
    hashes = {"audit.json": hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest(), "predictions.json": hashlib.sha256(json.dumps(predictions, sort_keys=True).encode()).hexdigest()}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "predictions.json").write_text(json.dumps(predictions, indent=2, sort_keys=True), encoding="utf-8")
    (OUTPUT / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 63 — predicciones Markov candidatas congeladas", "", f"**Clasificación:** `{audit['classification']}`", "", f"- fixtures congelados: `{len(predictions)}`", "- mercado candidato: `first_half_goal`", "- objetivo observado: `False`", "- router oficial modificado: `False`", "- Markov promovido: `False`", "- siguiente gate: `evaluación posterior al kickoff con log-loss y bootstrap por partido`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Predicciones candidatas congeladas: %s", len(predictions))
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"] == "frozen_markov_candidate_predictions_ready" else 1)

