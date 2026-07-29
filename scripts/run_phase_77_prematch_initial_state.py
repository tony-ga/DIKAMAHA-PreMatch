"""Predice el estado inicial 76R usando sólo historia pre-match.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_63_initial_state_calibration import _profile  # noqa: E402
from scripts.run_phase_76_domain_robust_reaudit import _engineer  # noqa: E402
from scripts.run_phase_76_latent_state_discovery import _arrays, _read_joint  # noqa: E402
from src.directional_temporal_baseline import (  # noqa: E402
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
    temperature_scale,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_77_prematch_initial_state"
WINDOWS = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
PARAMETERS = ROOT / "artifacts/phase_76_crossfit_reaudit/model_parameters.json"
STATES = 4
METRICS = ("goals", "shots", "shots_on_target", "corners", "pressure",
           "fouls", "yellow_cards", "red_cards")


def _state_targets() -> dict[tuple[int, int], int]:
    """Infiere targets state_0 con parámetros congelados de 76R."""

    parameters = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    records, output = _read_joint(), {}
    for split in ("fit", "selection", "confirmation"):
        rows = [row for row in records if row["split"] == split]
        data = _engineer(_arrays(records, split))
        scaled = (data["x"] - parameters["scaler_mean"]) / parameters["scaler_scale"]
        logits = scaled @ np.asarray(parameters["coefficients"])
        risks = 1.0 / (1.0 + np.exp(-(logits + parameters["intercept"])))
        states = np.digitize(risks, np.asarray(parameters["boundaries"]))
        for row, state in zip(rows, states):
            if int(row["window_index"]) == 0:
                output[(int(row["match_id"]), int(row["team_id"]))] = int(state)
    return output


def _match_rows() -> list[list[dict[str, Any]]]:
    """Agrega seis ventanas por equipo y conserva partidos completos."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for line in WINDOWS.open(encoding="utf-8"):
        row = json.loads(line)
        grouped[(int(row["match_id"]), int(row["team_id"]))].append(row)
    matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for (match_id, team_id), rows in grouped.items():
        first = rows[0]
        totals = {name: sum(float(row[name]) for row in rows) for name in METRICS}
        matches[match_id].append({
            "match_id": match_id, "team_id": team_id,
            "match_date": str(first["match_date"]), "split": str(first["split"]),
            "league_slug": str(first["league_slug"]),
            "is_home": float(bool(first["is_home"])), **totals,
        })
    return sorted(matches.values(), key=lambda rows: (
        rows[0]["match_date"], rows[0]["match_id"]))


def _prior(
    counts: Counter[int],
    parent: np.ndarray | None = None,
    strength: float = 20.0,
) -> np.ndarray:
    """Suaviza un prior categórico hacia su padre."""

    base = parent if parent is not None else np.full(STATES, 1.0 / STATES)
    observed = np.asarray([counts[state] for state in range(STATES)], dtype=float)
    return (observed + strength * base) / (observed.sum() + strength)


def _features(
    row: dict[str, Any],
    rival: dict[str, Any],
    histories: dict[int, list[dict[str, Any]]],
    baseline: np.ndarray,
) -> list[float]:
    """Construye features de ambos equipos estrictamente anteriores."""

    own = _profile(histories[int(row["team_id"])][-5:])
    away = _profile(histories[int(rival["team_id"])][-5:])
    names = ("history_count", *[f"avg_{name}" for name in METRICS])
    return [float(row["is_home"]), *baseline.tolist(),
            *[float(own[name]) for name in names],
            *[float(away[name]) for name in names]]


def _state_counts(history: list[dict[str, Any]]) -> list[int]:
    """Cuenta aperturas históricas por estado."""

    counts = Counter(int(row["state0"]) for row in history)
    return [counts[state] for state in range(STATES)]


def _team_probability(row: dict[str, Any], strength: float) -> np.ndarray:
    """Suaviza aperturas del equipo hacia liga+localía."""

    counts = np.asarray(row["own_state_counts"], dtype=float)
    baseline = np.asarray(row["baseline"], dtype=float)
    return (counts + strength * baseline) / (counts.sum() + strength)


def _records() -> list[dict[str, Any]]:
    """Congela predictores antes de actualizar cada partido."""

    targets, histories = _state_targets(), defaultdict(list)
    global_counts: Counter[int] = Counter()
    league_counts: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    output = []
    for match in _match_rows():
        for row in match:
            rival = next(item for item in match if item is not row)
            parent = _prior(global_counts)
            key = (str(row["league_slug"]), int(row["is_home"]))
            baseline = _prior(league_counts[key], parent)
            target = targets[(int(row["match_id"]), int(row["team_id"]))]
            output.append({**row, "target": target,
                           "features": _features(row, rival, histories, baseline),
                           "baseline": baseline.tolist(),
                           "own_state_counts": _state_counts(
                               histories[int(row["team_id"])][-20:])})
        for row in match:
            target = targets[(int(row["match_id"]), int(row["team_id"]))]
            histories[int(row["team_id"])].append({**row, "state0": target})
            global_counts[target] += 1
            league_counts[(str(row["league_slug"]), int(row["is_home"]))][target] += 1
    return output


def _metric(probabilities: np.ndarray, targets: np.ndarray) -> dict[str, float]:
    """Calcula métricas multiclase de un bloque."""

    return {"log_loss": multiclass_log_loss(probabilities, targets),
            "brier": multiclass_brier(probabilities, targets),
            "ece": expected_calibration_error(probabilities, targets)}


def _select(
    train: list[dict[str, Any]],
    selection: list[dict[str, Any]],
) -> tuple[Any, float, float]:
    """Selecciona regularización y temperatura sólo en selection."""

    x_train, y_train = _matrix(train)
    x_selection, y_selection = _matrix(selection)
    candidates = []
    for c_value in (0.001, 0.01, 0.1, 1.0):
        model = make_pipeline(StandardScaler(), LogisticRegression(
            C=c_value, max_iter=1000, random_state=27)).fit(x_train, y_train)
        raw = model.predict_proba(x_selection)
        for temperature in (0.75, 1.0, 1.25, 1.5):
            loss = multiclass_log_loss(
                temperature_scale(raw, temperature), y_selection)
            candidates.append((loss, c_value, temperature, model))
    _, c_value, temperature, model = min(candidates, key=lambda row: row[0])
    return model, float(c_value), float(temperature)


def _matrix(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Convierte registros en arrays alineados."""

    return (np.asarray([row["features"] for row in rows], dtype=float),
            np.asarray([row["target"] for row in rows], dtype=int))


def _evaluate(model: Any, temperature: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara modelo y prior jerárquico."""

    features, targets = _matrix(rows)
    model_prob = temperature_scale(model.predict_proba(features), temperature)
    baseline_prob = np.asarray([row["baseline"] for row in rows], dtype=float)
    model_metrics, baseline_metrics = _metric(model_prob, targets), _metric(baseline_prob, targets)
    improvement = 1.0 - model_metrics["log_loss"] / baseline_metrics["log_loss"]
    return {"rows": len(rows), "matches": len({row["match_id"] for row in rows}),
            "model": model_metrics, "baseline": baseline_metrics,
            "relative_log_loss_improvement": improvement,
            "probabilities": model_prob}


def run() -> dict[str, Any]:
    """Entrena, evalúa y publica Fase 77."""

    rows = _records()
    splits = {name: [row for row in rows if row["split"] == name]
              for name in ("fit", "selection", "confirmation")}
    model, c_value, temperature = _select(splits["fit"], splits["selection"])
    evaluations = {name: _evaluate(model, temperature, splits[name])
                   for name in ("selection", "confirmation")}
    team_diagnostic = {
        name: _team_diagnostic(splits[name]) for name in
        ("selection", "confirmation")
    }
    passed = all(_eligible(item) for item in evaluations.values())
    result = _result(
        rows, evaluations, team_diagnostic, c_value, temperature, passed)
    _publish(result)
    return result


def _eligible(value: dict[str, Any]) -> bool:
    """Aplica el gate completo de estado inicial."""

    return bool(value["relative_log_loss_improvement"] >= 0.01
                and value["model"]["brier"] <= value["baseline"]["brier"]
                and value["model"]["ece"] <= value["baseline"]["ece"])


def _team_diagnostic(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Mide el mejor shrinkage de equipo sin usarlo para promoción."""

    targets = np.asarray([row["target"] for row in rows], dtype=int)
    values = []
    for strength in (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0):
        probabilities = np.asarray([
            _team_probability(row, strength) for row in rows])
        values.append((multiclass_log_loss(probabilities, targets), strength))
    loss, strength = min(values)
    return {"best_log_loss": float(loss), "best_strength": float(strength)}


def _result(
    rows: list[dict[str, Any]], evaluations: dict[str, Any],
    team_diagnostic: dict[str, Any],
    c_value: float, temperature: float, passed: bool,
) -> dict[str, Any]:
    """Compone resultado sin probabilidades no serializables."""

    clean = {name: {key: value for key, value in item.items()
                    if key != "probabilities"}
             for name, item in evaluations.items()}
    return {"classification": ("ready_for_next_phase" if passed
                               else "rejected_for_revision"),
            "config": {"version": "prematch_initial_state_v4",
                       "c_value": c_value, "temperature": temperature,
                       "states": STATES, "recent_matches": 5},
            "coverage": {"rows": len(rows),
                         "matches": len({row["match_id"] for row in rows}),
                         "leagues": len({row["league_slug"] for row in rows})},
            "audit": {"target_match_events_in_features": False,
                      "profiles_frozen_before_update": True,
                      "core_backoff_available": True,
                      "router_modified": False},
            "metrics": clean, "team_prior_diagnostic": team_diagnostic}


def _write(name: str, value: Any) -> None:
    """Escribe JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(
        value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos normativos y hashes."""

    for name in ("config", "coverage", "audit", "metrics",
                 "team_prior_diagnostic"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "windows_sha256": hashlib.sha256(WINDOWS.read_bytes()).hexdigest(),
        "state_parameters_sha256": hashlib.sha256(PARAMETERS.read_bytes()).hexdigest()})
    report = "# Fase 77 — estado inicial pre-match\n\n"
    report += f"**Clasificación:** `{result['classification']}`\n\n"
    for name, value in result["metrics"].items():
        report += (f"- {name}: mejora log-loss "
                   f"`{value['relative_log_loss_improvement']:.2%}`, "
                   f"Brier `{value['model']['brier']:.6f}`, "
                   f"ECE `{value['model']['ece']:.6f}`\n")
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def main() -> int:
    """Ejecuta Fase 77 y devuelve error si falla algún gate."""

    result = run()
    LOGGER.info("Fase 77: %s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
