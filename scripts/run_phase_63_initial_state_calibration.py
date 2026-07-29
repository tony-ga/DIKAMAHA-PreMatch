"""Calibra un clasificador multinomial pre-match para ``state_0``.

Las variables se construyen sólo con partidos anteriores al kickoff. La
cohorte independiente de Fase 62 no se lee para ajustar ni seleccionar.

Requirements:
    - numpy
    - scikit-learn
    - joblib

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.state_labeling_v1 import StateLabelingConfig, label

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
COHORT = ROOT / "artifacts/phase_62_independent_cohort_lock_v1/cohort.json"
OUTPUT = ROOT / "artifacts/phase_63_initial_state_calibration_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
METRIC_STATES = tuple(sorted(STATES))
FEATURES = ("is_home", "history_count", "avg_goals", "avg_shots", "avg_shots_on_target", "avg_corners", "avg_pressure", "avg_fouls", "avg_yellow_cards", "avg_red_cards")
LOGGER = logging.getLogger(__name__)


def _key(row: dict[str, Any]) -> tuple[int, int]:
    """Construye clave partido-equipo."""

    return int(row["match_id"]), int(row["team_id"])


def _profile(history: list[dict[str, Any]]) -> dict[str, float]:
    """Resume los últimos cinco partidos sin usar el partido objetivo."""

    if not history:
        return {"history_count": 0.0, **{f"avg_{name}": 0.0 for name in ("goals", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards")}}
    names = ("goals", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards")
    return {"history_count": float(len(history)), **{f"avg_{name}": sum(float(row[name]) for row in history) / len(history) for name in names}}


def _records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega ventanas por equipo y deriva target state_0."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_key(row)].append(row)
    output = []
    for key, match_rows in grouped.items():
        ordered = sorted(match_rows, key=lambda row: int(row["window_index"]))
        first = ordered[0]
        state, _ = label(first, StateLabelingConfig())
        totals = {name: sum(float(row[name]) for row in ordered) for name in ("goals", "shots", "shots_on_target", "corners", "pressure", "fouls", "yellow_cards", "red_cards")}
        output.append({"match_id": key[0], "team_id": key[1], "match_date": str(first["match_date"]), "league_slug": str(first["league_slug"]), "is_home": float(bool(first["is_home"])), "target": state, **totals})
    return sorted(output, key=lambda row: (row["match_date"], int(row["match_id"]), int(row["team_id"])))


def _with_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Añade perfiles históricos en orden temporal estricto."""

    history: dict[int, list[dict[str, Any]]] = defaultdict(list)
    output = []
    for row in rows:
        profile = _profile(history[int(row["team_id"])][-5:])
        output.append({**row, **profile})
        history[int(row["team_id"])].append(row)
    return output


def _splits(rows: list[dict[str, Any]]) -> dict[str, set[int]]:
    """Divide partidos completos en desarrollo, validación y confirmación."""

    matches = sorted({(row["match_date"], int(row["match_id"])) for row in rows})
    first, second = int(len(matches) * 0.60), int(len(matches) * 0.80)
    return {"development": {item[1] for item in matches[:first]}, "validation": {item[1] for item in matches[first:second]}, "confirmation": {item[1] for item in matches[second:]}}


def _matrix(rows: list[dict[str, Any]]) -> list[list[float]]:
    """Convierte registros a la matriz numérica del clasificador."""

    return [[float(row[name]) for name in FEATURES] for row in rows]


def _prior(rows: list[dict[str, Any]], league: str | None = None) -> dict[str, float]:
    """Calcula un prior suavizado desde desarrollo solamente."""

    selected = [row for row in rows if league is None or row["league_slug"] == league]
    counts = Counter(row["target"] for row in selected)
    total = sum(counts.values()) + 8.0
    return {state: (counts[state] + 8.0 / len(STATES)) / total for state in STATES}


def _league_probs(train: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[list[float]]:
    """Expande priors por liga con backoff global."""

    global_prior = _prior(train)
    leagues = {str(row["league_slug"]) for row in train}
    priors = {league: _prior(train, league) for league in leagues}
    return [[priors.get(str(row["league_slug"]), global_prior)[state] for state in STATES] for row in rows]


def _metric_order(probabilities: list[list[float]]) -> list[list[float]]:
    """Ordena columnas según el contrato de sklearn para log-loss."""

    return [[row[STATES.index(state)] for state in METRIC_STATES] for row in probabilities]


def _model_probs(model: Any, rows: list[dict[str, Any]]) -> list[list[float]]:
    """Alinea probabilidades cuando una clase es ausente en desarrollo."""

    raw = model.predict_proba(_matrix(rows))
    classes = list(model[-1].classes_)
    output = []
    for values in raw:
        row = [1e-9] * len(STATES)
        for index, state in enumerate(STATES):
            if state in classes:
                row[index] = float(values[classes.index(state)])
        total = sum(row)
        output.append([value / total for value in row])
    return output


def _evaluate(model: Any, train: list[dict[str, Any]], rows: list[dict[str, Any]], split: str) -> dict[str, Any]:
    """Evalúa modelo y baselines sobre un split temporal."""

    x, y = _matrix(rows), [str(row["target"]) for row in rows]
    model_loss = log_loss(y, _metric_order(_model_probs(model, rows)), labels=list(METRIC_STATES))
    global_prior = _prior(train)
    global_loss = log_loss(y, _metric_order([[global_prior[state] for state in STATES] for _ in rows]), labels=list(METRIC_STATES))
    league_loss = log_loss(y, _metric_order(_league_probs(train, rows)), labels=list(METRIC_STATES))
    return {"split": split, "rows": len(rows), "model_logloss": model_loss, "global_logloss": global_loss, "league_logloss": league_loss}


def run() -> dict[str, Any]:
    """Ajusta el clasificador y publica métricas temporales aisladas."""

    rows = _with_profiles(_records(json.loads(WINDOWS.read_text(encoding="utf-8"))))
    splits = _splits(rows)
    development = [row for row in rows if int(row["match_id"]) in splits["development"]]
    model = make_pipeline(StandardScaler(), CalibratedClassifierCV(LogisticRegression(max_iter=1000, random_state=42), method="sigmoid", cv=3))
    model.fit(_matrix(development), [row["target"] for row in development])
    metrics = [_evaluate(model, development, [row for row in rows if int(row["match_id"]) in splits[name]], name) for name in ("development", "validation", "confirmation")]
    cohort = json.loads(COHORT.read_text(encoding="utf-8"))
    support = dict(Counter(row["target"] for row in development))
    missing = [state for state in STATES if state not in support]
    classification = "initial_state_candidate_ready_with_sparse_class_support" if missing else "initial_state_candidate_ready_for_independent_evaluation"
    audit = {"classification": classification, "rows": len(rows), "matches": len({int(row["match_id"]) for row in rows}), "development_matches": len(splits["development"]), "validation_matches": len(splits["validation"]), "confirmation_matches": len(splits["confirmation"]), "development_class_support": support, "development_missing_classes": missing, "metrics": metrics, "features": list(FEATURES), "causal_profile_window": 5, "independent_cohort_locked": cohort["fixture_count"], "targets_used_before_prediction": False, "router_modified": False, "markov_promoted": False, "model_hash": hashlib.sha256(json.dumps(metrics, sort_keys=True).encode()).hexdigest()}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, OUTPUT / "state0_classifier.joblib")
    (OUTPUT / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 63 — calibración inicial state_0", "", f"**Clasificación:** `{audit['classification']}`", "", f"- partidos: `{audit['matches']}`", f"- splits desarrollo/validación/confirmación: `{audit['development_matches']}/{audit['validation_matches']}/{audit['confirmation_matches']}`", f"- cohorte independiente reservada: `{cohort['fixture_count']}`", f"- métricas: `{metrics}`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 63 state_0: %s", audit["classification"])
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"].startswith("initial_state_candidate_ready") else 1)

# Version: 1.0.0
# Created: 2026-07-27
