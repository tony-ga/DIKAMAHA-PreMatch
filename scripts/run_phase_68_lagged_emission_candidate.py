"""Prueba emisiones Markov alineadas temporalmente con el estado anterior."""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import _entropy, _profile, _probabilities, _state_rows, _transition_index, _time
from scripts.run_phase_63_initial_state_calibration import _records
from scripts.run_phase_65_markov_position_audit import _add_stats, _group, _loss, _rate, _state_rate, _bootstrap, _transition

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
MODEL = ROOT / "artifacts/phase_63_initial_state_calibration_v1/state0_classifier.joblib"
TRANSITIONS = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1/transition_matrices.json"
OUTPUT = ROOT / "artifacts/phase_68_lagged_emission_candidate_v1"
SMOOTHING = 2.0
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Separa desarrollo y bloque walk-forward posterior."""

    grouped = _group(rows)
    ordered = sorted((str(values[0]["match_date"]), match_id) for match_id, values in grouped.items())
    split = int(len(ordered) * 0.60)
    development = ordered[:split]
    fixtures = []
    for kickoff, match_id in ordered[split:]:
        values = grouped[match_id]
        home = next(row for row in values if bool(row["is_home"]))
        away = next(row for row in values if not bool(row["is_home"]))
        fixtures.append({"match_id": match_id, "league_slug": str(home["league_slug"]), "kickoff_ts": kickoff, "home_team_id": int(home["team_id"]), "away_team_id": int(away["team_id"]), "provider_status": "post"})
    history = [row for row in rows if (str(row["match_date"]), int(row["match_id"])) <= development[-1]]
    return history, fixtures, development[-1][0]


def _add_lagged(rows: list[dict[str, Any]], lagged: dict[tuple[str, bool, int, str], list[float]]) -> None:
    """Acumula goles de la ventana actual condicionados al estado previo."""

    grouped = defaultdict(list)
    for row in rows:
        grouped[(int(row["match_id"]), int(row["team_id"]))].append(row)
    for values in grouped.values():
        ordered = sorted(values, key=lambda row: int(row["window_index"]))
        for previous, current in zip(ordered, ordered[1:]):
            key = (str(current["league_slug"]), bool(current["is_home"]), int(current["window_index"]), str(previous["state"]))
            lagged[key][0] += float(current["goals"])
            lagged[key][1] += 1.0


def _lagged_rate(totals: dict[tuple[str, bool, int], list[float]], venues: dict[tuple[bool, int], list[float]], lagged: dict[tuple[str, bool, int, str], list[float]], key: tuple[str, bool, int], state: str) -> float:
    """Obtiene emisión suavizada desde el estado de la ventana previa."""

    base = _rate(totals, venues, key)
    values = lagged.get((*key, state), [0.0, 0.0])
    return (values[0] + SMOOTHING * base) / (values[1] + SMOOTHING)


def _trajectory(fixture: dict[str, Any], home: dict[str, float], away: dict[str, float], totals: dict[tuple[str, bool, int], list[float]], state_totals: dict[tuple[str, bool, int, str], list[float]], venues: dict[tuple[bool, int], list[float]], lagged: dict[tuple[str, bool, int, str], list[float]], transitions: dict[str, dict[tuple[Any, ...], dict[str, Any]]], state0_same_window: bool) -> tuple[float, float]:
    """Simula primera mitad con emisión alineada al estado previo."""

    alive = {(home_state, away_state): home[home_state] * away[away_state] for home_state in home for away_state in away}
    baseline_rate = 0.0
    for window in range(3):
        home_key = (str(fixture["league_slug"]), True, window)
        away_key = (str(fixture["league_slug"]), False, window)
        baseline_rate += _rate(totals, venues, home_key) + _rate(totals, venues, away_key)
        next_alive: dict[tuple[str, str], float] = defaultdict(float)
        for (home_state, away_state), weight in alive.items():
            if window == 0 and state0_same_window:
                home_rate = _state_rate(totals, state_totals, venues, home_key, home_state)
                away_rate = _state_rate(totals, state_totals, venues, away_key, away_state)
            elif window == 0:
                home_rate = _rate(totals, venues, home_key)
                away_rate = _rate(totals, venues, away_key)
            else:
                home_rate = _lagged_rate(totals, venues, lagged, home_key, home_state)
                away_rate = _lagged_rate(totals, venues, lagged, away_key, away_state)
            survived = weight * math.exp(-(home_rate + away_rate))
            if window == 2:
                next_alive[(home_state, away_state)] += survived
                continue
            home_next, _ = _transition(transitions, str(fixture["league_slug"]), int(fixture["home_team_id"]), True, window, home_state, away_state)
            away_next, _ = _transition(transitions, str(fixture["league_slug"]), int(fixture["away_team_id"]), False, window, away_state, home_state)
            for next_home in home:
                for next_away in away:
                    next_alive[(next_home, next_away)] += survived * home_next[next_home] * away_next[next_away]
        alive = next_alive
    return 1.0 - sum(alive.values()), 1.0 - math.exp(-baseline_rate)


def _predict(fixture: dict[str, Any], histories: dict[int, list[dict[str, Any]]], totals: dict[tuple[str, bool, int], list[float]], state_totals: dict[tuple[str, bool, int, str], list[float]], venues: dict[tuple[bool, int], list[float]], lagged: dict[tuple[str, bool, int, str], list[float]], model: Any, transitions: dict[str, dict[tuple[Any, ...], dict[str, Any]]]) -> dict[str, Any]:
    """Genera las variantes de emisión antes del target."""

    home = _probabilities(model, {"is_home": 1.0, **_profile(histories[int(fixture["home_team_id"])][-5:])})
    away = _probabilities(model, {"is_home": 0.0, **_profile(histories[int(fixture["away_team_id"])][-5:])})
    shifted, baseline = _trajectory(fixture, home, away, totals, state_totals, venues, lagged, transitions, False)
    same_first, _ = _trajectory(fixture, home, away, totals, state_totals, venues, lagged, transitions, True)
    return {"fixture": fixture, "shifted_probability": shifted, "same_first_probability": same_first, "baseline_probability": baseline, "state_0_home": home, "state_0_away": away, "entropy_home": _entropy(home), "entropy_away": _entropy(away), "target_used_before_prediction": False}


def _metrics(rows: list[dict[str, Any]], key: str, start: int = 0, end: int | None = None) -> dict[str, Any]:
    """Calcula métricas de una variante temporal."""

    selected = rows[start:end]
    markov, baseline, brier, baseline_brier, deltas = [], [], [], [], []
    for row in selected:
        actual = bool(row["actual_first_half_goal"])
        ml, mb = _loss(row[key], actual)
        bl, bb = _loss(row["baseline_probability"], actual)
        markov.append(ml)
        baseline.append(bl)
        brier.append(mb)
        baseline_brier.append(bb)
        deltas.append(bl - ml)
    return {"matches": len(selected), "markov_log_loss": float(np.mean(markov)), "baseline_log_loss": float(np.mean(baseline)), "markov_brier": float(np.mean(brier)), "baseline_brier": float(np.mean(baseline_brier)), "improvement": _bootstrap(deltas)}


def run() -> dict[str, Any]:
    """Evalúa emisiones desplazadas en desarrollo/holdout temporal."""

    raw = _load(WINDOWS)
    history_raw, fixtures, cutoff = _fixtures(raw)
    grouped = _group(raw)
    state_history = _state_rows(history_raw)
    histories: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in _records(state_history):
        histories[int(record["team_id"])].append(record)
    totals: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    state_totals: dict[tuple[str, bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    venues: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    lagged: dict[tuple[str, bool, int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    _add_stats(state_history, totals, state_totals, venues)
    _add_lagged(state_history, lagged)
    model = joblib.load(MODEL)
    transitions = _transition_index(_load(TRANSITIONS))
    predictions = []
    for fixture in fixtures:
        prediction = _predict(fixture, histories, totals, state_totals, venues, lagged, model, transitions)
        target = grouped[int(fixture["match_id"])]
        actual = sum(float(row["goals"]) for row in target if int(row["window_index"]) < 3) > 0
        predictions.append({**prediction, "actual_first_half_goal": actual})
        target_state = _state_rows(target)
        _add_stats(target_state, totals, state_totals, venues)
        _add_lagged(target_state, lagged)
        for record in _records(target_state):
            histories[int(record["team_id"])].append(record)
    split = len(predictions) // 2
    validation = {key: _metrics(predictions, key, 0, split) for key in ("shifted_probability", "same_first_probability")}
    selected = min(validation, key=lambda key: (validation[key]["markov_log_loss"], key))
    holdout = _metrics(predictions, selected, split)
    all_metrics = _metrics(predictions, selected, 0)
    metrics = {"development_matches": len({int(row["match_id"]) for row in history_raw}), "audit_matches": len(predictions), "development_cutoff": cutoff, "validation_all": validation, "selected_variant": selected, "holdout_selected": holdout, "all_selected": all_metrics, "validation_first_half_count": split}
    audit = {"classification": "lagged_emission_candidate_requires_confirmation" if holdout["markov_log_loss"] < holdout["baseline_log_loss"] else "lagged_emission_no_incremental_value", "target_used_before_prediction": False, "walk_forward_order": True, "router_modified": False, "markov_promoted": False}
    result = {"config": {"smoothing": SMOOTHING, "market": "first_half_goal", "variants": ["shifted_probability", "same_first_probability"]}, "metrics": metrics, "predictions": predictions, "audit": audit}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = ["# Fase 68 — emisión Markov temporalmente alineada", "", f"**Clasificación:** `{audit['classification']}`", "", f"- variante seleccionada: `{selected}`", f"- holdout Markov: `{holdout['markov_log_loss']}`", f"- holdout baseline: `{holdout['baseline_log_loss']}`", f"- mejora: `{holdout['improvement']['mean']}`", f"- IC: `{holdout['improvement']['ci_95']}`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 68 emisión alineada: %s", audit["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["audit"]["classification"].startswith("lagged_emission_") else 1)
