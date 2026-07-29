"""Reaudita estados factoriales style_state × match_regime.

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
from sklearn.metrics import normalized_mutual_info_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_76_crossfit_reaudit as phase76  # noqa: E402
import scripts.run_phase_77_prematch_initial_state as phase77  # noqa: E402
from scripts.run_phase_76_predictive_state_reaudit import _duration  # noqa: E402
from src.directional_temporal_baseline import (  # noqa: E402
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
)
from src.latent_state_discovery import (  # noqa: E402
    league_order_stability,
    next_goal_risk,
    occupancy,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_77_dual_state_reaudit"
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
STATE_COUNT = 6
REGIME_QUANTILES = (0.15, 0.85)


def _style_score(row: dict[str, Any]) -> float:
    """Resume ventaja persistente de actividad pre-match."""

    values = row["features"]
    own = values[7] + 2 * values[8] + 0.5 * values[9] + 0.2 * values[10]
    rival = values[16] + 2 * values[17] + 0.5 * values[18] + 0.2 * values[19]
    return float(own - rival)


def _style_map() -> dict[tuple[int, int], dict[str, Any]]:
    """Materializa contexto pre-match causal por partido-equipo."""

    return {(int(row["match_id"]), int(row["team_id"])): row
            for row in phase77._records()}


def _records(split: str, source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filtra registros direccionales conservando orden."""

    return [row for row in source if row["split"] == split]


def _assign(
    train: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    train_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]],
    c_value: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Aprende cortes sólo en train y asigna seis estados factoriales."""

    model = phase76._fit(train, c_value, (0.1, 0.5, 0.9))
    train_risk = model.risk(train["x"])
    regime_bounds = np.quantile(train_risk, REGIME_QUANTILES)
    style_values = np.asarray([
        _style_score(styles[(row["match_id"], row["team_id"])])
        for row in train_rows])
    style_bound = float(np.median(style_values))
    train_states = _combine(
        train_rows, train_risk, styles, style_bound, regime_bounds)
    target_states = _combine(
        target_rows, model.risk(target["x"]), styles,
        style_bound, regime_bounds)
    config = {"style_boundary": style_bound,
              "regime_boundaries": regime_bounds.tolist(), "c_value": c_value}
    return train_states, target_states, config


def _combine(
    rows: list[dict[str, Any]],
    risks: np.ndarray,
    styles: dict[tuple[int, int], dict[str, Any]],
    style_bound: float,
    regime_bounds: np.ndarray,
) -> np.ndarray:
    """Combina estilo binario conocido y régimen dinámico ternario."""

    style = np.asarray([
        _style_score(styles[(row["match_id"], row["team_id"])]) > style_bound
        for row in rows], dtype=int)
    regime = np.digitize(risks, regime_bounds)
    return style * 3 + regime


def _nmi(
    train: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    train_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]],
    c_value: float,
) -> float:
    """Mide identidad usando dos mitades temporales comparables."""

    left, right = phase76._temporal_halves(train)
    match_ids = train["match_ids"] // 1_000_000
    midpoint = len(set(match_ids.tolist())) // 2
    ordered = list(dict.fromkeys(match_ids.tolist()))
    masks = [np.isin(match_ids, ordered[:midpoint]),
             np.isin(match_ids, ordered[midpoint:])]
    row_parts = [[row for row, keep in zip(train_rows, mask) if keep]
                 for mask in masks]
    assignments = [
        _assign(part, target, rows, target_rows, styles, c_value)[1]
        for part, rows in ((left, row_parts[0]), (right, row_parts[1]))
    ]
    return float(normalized_mutual_info_score(assignments[0], assignments[1]))


def _state_metrics(
    train: dict[str, np.ndarray], target: dict[str, np.ndarray],
    train_rows: list[dict[str, Any]], target_rows: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]], c_value: float,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray, dict[str, Any]]:
    """Evalúa semántica, estabilidad y duración OOS."""

    fit_states, states, config = _assign(
        train, target, train_rows, target_rows, styles, c_value)
    risks, support = next_goal_risk(states, target["next_goals"], STATE_COUNT)
    metrics = {"spread": float(np.ptp(risks)), "risk": risks.tolist(),
               "support": support.tolist(),
               "minimum_occupancy": min(occupancy(states, STATE_COUNT).values()),
               "fold_nmi": _nmi(train, target, train_rows, target_rows,
                                styles, c_value),
               "league_order": league_order_stability(
                   target["leagues"], states, target["next_goals"], risks),
               "duration": _duration(
                   train, target, fit_states, states, STATE_COUNT)}
    return metrics, fit_states, states, config


def _first(
    rows: list[dict[str, Any]],
    states: np.ndarray,
) -> list[tuple[dict[str, Any], int]]:
    """Conserva una observación state_0 por partido-equipo."""

    return [(row, int(state)) for row, state in zip(rows, states)
            if int(row["window_index"]) == 0]


def _distribution(counts: Counter[int], parent: np.ndarray) -> np.ndarray:
    """Suaviza seis estados con fuerza conservadora."""

    observed = np.asarray([
        counts.get(state, 0) for state in range(STATE_COUNT)])
    return (observed + 20.0 * parent) / (observed.sum() + 20.0)


def _initial_metrics(
    train: list[tuple[dict[str, Any], int]],
    target: list[tuple[dict[str, Any], int]],
    styles: dict[tuple[int, int], dict[str, Any]],
    style_boundary: float,
) -> dict[str, Any]:
    """Compara prior liga/localía contra distribución condicionada por estilo."""

    global_counts = Counter(state for _, state in train)
    global_prior = _distribution(
        global_counts, np.full(STATE_COUNT, 1.0 / STATE_COUNT))
    league, conditioned = defaultdict(Counter), defaultdict(Counter)
    for row, state in train:
        role = int(styles[(row["match_id"], row["team_id"])]["is_home"])
        league[(row["league_slug"], role)][state] += 1
        conditioned[(row["league_slug"], role, state // 3)][state] += 1
    probabilities, baseline, targets = [], [], []
    for row, state in target:
        context = styles[(row["match_id"], row["team_id"])]
        key = (row["league_slug"], int(context["is_home"]))
        base = _distribution(league[key], global_prior)
        style = int(_style_score(context) > style_boundary)
        probabilities.append(_style_distribution(conditioned[
            (key[0], key[1], style)], base, style))
        baseline.append(base)
        targets.append(state)
    return _probability_metrics(
        np.asarray(probabilities), np.asarray(baseline), np.asarray(targets))


def _style_distribution(
    counts: Counter[int], baseline: np.ndarray, style: int,
) -> np.ndarray:
    """Restringe masa al estilo conocido y suaviza su régimen."""

    mask = np.asarray([state // 3 == style for state in range(STATE_COUNT)])
    parent = baseline * mask
    parent /= parent.sum()
    observed = np.asarray([
        counts.get(state, 0) for state in range(STATE_COUNT)])
    values = observed + 20.0 * parent
    return values / values.sum()


def _probability_metrics(
    model: np.ndarray, baseline: np.ndarray, targets: np.ndarray,
) -> dict[str, Any]:
    """Calcula gate de predictibilidad inicial."""

    candidate = _scores(model, targets)
    reference = _scores(baseline, targets)
    return {"model": candidate, "baseline": reference,
            "relative_log_loss_improvement":
                1.0 - candidate["log_loss"] / reference["log_loss"]}


def _scores(probabilities: np.ndarray, targets: np.ndarray) -> dict[str, float]:
    """Resume log-loss, Brier y calibración."""

    return {"log_loss": multiclass_log_loss(probabilities, targets),
            "brier": multiclass_brier(probabilities, targets),
            "ece": expected_calibration_error(probabilities, targets)}


def _fold(
    name: str, train_names: tuple[str, ...], target_name: str,
    blocks: dict[str, dict[str, np.ndarray]], source: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]], c_value: float,
) -> dict[str, Any]:
    """Ejecuta un fold temporal completo."""

    train = phase76._concat([blocks[item] for item in train_names])
    target = blocks[target_name]
    train_rows = [row for row in source if row["split"] in train_names]
    target_rows = _records(target_name, source)
    state, fit_states, states, config = _state_metrics(
        train, target, train_rows, target_rows, styles, c_value)
    initial = _initial_metrics(
        _first(train_rows, fit_states), _first(target_rows, states),
        styles, config["style_boundary"])
    return {"name": name, "state_metrics": state,
            "initial_state_metrics": initial, "config": config,
            "eligible": _eligible(state, initial)}


def _eligible(state: dict[str, Any], initial: dict[str, Any]) -> bool:
    """Exige gates de Fases 76 y 77 simultáneamente."""

    return bool(state["spread"] >= 0.05
                and state["minimum_occupancy"] >= 0.05
                and state["fold_nmi"] >= 0.70
                and state["league_order"]["rate"] >= 0.75
                and state["duration"]["improvement"] > 0.0
                and initial["relative_log_loss_improvement"] >= 0.01
                and initial["model"]["brier"] <= initial["baseline"]["brier"]
                and initial["model"]["ece"] <= initial["baseline"]["ece"])


def run() -> dict[str, Any]:
    """Ejecuta la reauditoría dual sin modificar el router."""

    source, styles = phase76._read_joint(), _style_map()
    blocks = {name: phase76._engineer(phase76._arrays(source, name))
              for name in ("fit", "selection", "confirmation")}
    folds = [_fold("selection_oos", ("fit",), "selection",
                   blocks, source, styles, 0.000003),
             _fold("confirmation_oos", ("fit", "selection"), "confirmation",
                   blocks, source, styles, 0.000001)]
    parameters, assignments = _final_package(blocks, source, styles)
    classification = ("ready_for_next_phase" if all(
        row["eligible"] for row in folds) else "rejected_for_revision")
    result = {"classification": classification,
              "config": {"version": "dual_style_regime_v1",
                         "states": STATE_COUNT,
                         "regime_quantiles": list(REGIME_QUANTILES)},
              "coverage": {"matches": len({row["match_id"] for row in source}),
                           "leagues": len({row["league_slug"] for row in source}),
                           "rows": len(source)},
              "audit": {"style_strictly_prematch": True,
                        "target_match_events_in_style": False,
                        "router_modified": False},
              "metrics": {"folds": folds},
              "model_parameters": parameters,
              "assignments": assignments}
    _publish(result)
    return result


def _final_package(
    blocks: dict[str, dict[str, np.ndarray]],
    source: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Ajusta todo desarrollo y serializa estados para Fase 78."""

    data = phase76._concat(list(blocks.values()))
    model = phase76._fit(data, 0.000001, (0.1, 0.5, 0.9))
    risks = model.risk(data["x"])
    regime = np.quantile(risks, REGIME_QUANTILES)
    style = float(np.median([
        _style_score(styles[(row["match_id"], row["team_id"])])
        for row in source]))
    states = _combine(source, risks, styles, style, regime)
    parameters = _serialize_model(model, style, regime)
    assignments = [{"match_id": row["match_id"], "team_id": row["team_id"],
                    "window_index": row["window_index"], "split": row["split"],
                    "state": int(state), "risk_score": float(risk)}
                   for row, state, risk in zip(source, states, risks)]
    return parameters, assignments


def _serialize_model(
    model: Any, style: float, regime: np.ndarray,
) -> dict[str, Any]:
    """Convierte el modelo final en parámetros portables."""

    if model.scaler is None or model.classifier is None:
        raise RuntimeError("dual_state_model_not_fitted")
    return {"states": STATE_COUNT, "style_boundary": style,
            "regime_boundaries": regime.tolist(),
            "regime_c_value": model.c_value,
            "scaler_mean": model.scaler.mean_.tolist(),
            "scaler_scale": model.scaler.scale_.tolist(),
            "coefficients": model.classifier.coef_[0].tolist(),
            "intercept": float(model.classifier.intercept_[0])}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(
        value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato, reporte y hashes."""

    assignments = result.pop("assignments")
    for name in ("config", "coverage", "audit", "metrics",
                 "model_parameters"):
        _write(f"{name}.json", result[name])
    _write_jsonl("state_assignments.jsonl", assignments)
    _write("input_manifest.json", {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "phase76_parameters_sha256": hashlib.sha256(
            phase77.PARAMETERS.read_bytes()).hexdigest()})
    report = "# Reauditoría dual Fases 76–77\n\n"
    report += f"**Clasificación:** `{result['classification']}`\n\n"
    for fold in result["metrics"]["folds"]:
        state, initial = fold["state_metrics"], fold["initial_state_metrics"]
        report += (f"- {fold['name']}: spread `{state['spread']:.6f}`, "
                   f"NMI `{state['fold_nmi']:.6f}`, mejora state_0 "
                   f"`{initial['relative_log_loss_improvement']:.2%}`\n")
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
    """Publica asignaciones de forma atómica."""

    temporary = OUTPUT / f"{name}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(OUTPUT / name)


def main() -> int:
    """Ejecuta y exige aprobación conjunta."""

    result = run()
    LOGGER.info("Reauditoría dual: %s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
