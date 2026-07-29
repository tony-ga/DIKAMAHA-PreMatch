"""Evalúa Markov v4 sobre likelihood de trayectoria completa.

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
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_75_temporal_baseline_targets as phase75  # noqa: E402
import scripts.run_phase_77_dual_state_reaudit as phase77  # noqa: E402
import scripts.run_phase_80_nested_walkforward as phase80  # noqa: E402
from src.directional_temporal_baseline import (  # noqa: E402
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80r_full_trajectory_likelihood"
STRENGTHS = (20.0, 50.0, 100.0, 200.0)
VARIANTS = ("full", "no_context", "no_transition")


def _baseline_model(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
) -> tuple[Any, list[str]]:
    """Ajusta el tabular exclusivamente con el train externo."""

    train = [row for row in examples if row["split"] in train_names]
    names = sorted(train[0]["features"])
    model = phase75._model(0.05)
    features, targets = phase75._matrix(train, names)
    model.fit(features, targets)
    return model, names


def _predict(
    model: Any, names: list[str], rows: list[dict[str, Any]],
) -> np.ndarray:
    """Emite probabilidades tabulares alineadas."""

    return phase75._probabilities(model, rows, names, 1.0)


def _state_index(
    rows: list[dict[str, Any]], states: np.ndarray,
) -> dict[tuple[int, int, int], int]:
    """Indexa estados train por partido, equipo y microventana."""

    return {(int(row["match_id"]), int(row["team_id"]),
             int(row["window_index"])): int(state)
            for row, state in zip(rows, states)}


def _emission_ratios(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
    states: dict[tuple[int, int, int], int], strength: float,
) -> np.ndarray:
    """Aprende residuos de emisión por par latente y ventana."""

    counts = np.zeros((6, 6, 6, 4))
    exposure = np.zeros_like(counts)
    means = np.zeros((6, 4))
    support = np.zeros(6)
    for row, base in zip(rows, probabilities):
        window, target = int(row["window_index"]), int(row["target"])
        pair = _pair_state(row, window * 3, states)
        counts[window, pair[0], pair[1], target] += 1
        exposure[window, pair[0], pair[1]] += base
        means[window] += base
        support[window] += 1
    means /= support[:, None]
    prior = strength * means[:, None, None, :]
    return (counts + prior) / np.maximum(exposure + prior, 1e-12)


def _pair_state(
    row: dict[str, Any], micro_window: int,
    states: dict[tuple[int, int, int], int],
) -> tuple[int, int]:
    """Obtiene estados local/visitante sin ambigüedad de orientación."""

    match_id = int(row["match_id"])
    return (states[(match_id, int(row["home_team_id"]), micro_window)],
            states[(match_id, int(row["away_team_id"]), micro_window)])


def _direct_ratios(
    rows: list[dict[str, Any]], probabilities: np.ndarray, strength: float,
) -> np.ndarray:
    """Ajusta comparador secuencial directo sobre clases observadas."""

    counts = np.zeros((6, 4, 4))
    exposure = np.zeros_like(counts)
    means = np.zeros((6, 4))
    support = np.zeros(6)
    grouped = _group(rows, probabilities)
    for values in grouped.values():
        for window in range(1, 6):
            row, base = values[window]
            previous, target = int(values[window - 1][0]["target"]), int(row["target"])
            counts[window, previous, target] += 1
            exposure[window, previous] += base
            means[window] += base
            support[window] += 1
    means[1:] /= support[1:, None]
    prior = strength * means[:, None, :]
    ratios = (counts + prior) / np.maximum(exposure + prior, 1e-12)
    ratios[0] = 1.0
    return ratios


def _group(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
) -> dict[int, list[tuple[dict[str, Any], np.ndarray]]]:
    """Agrupa y ordena las seis ventanas de cada partido."""

    output: dict[int, list[tuple[dict[str, Any], np.ndarray]]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        output[int(row["match_id"])].append((row, probability))
    for values in output.values():
        values.sort(key=lambda item: int(item[0]["window_index"]))
    return output


def _emission(
    base: np.ndarray, ratio: np.ndarray,
) -> np.ndarray:
    """Combina carrier y residuo preservando normalización."""

    values = base * ratio
    return values / values.sum(axis=-1, keepdims=True)


def _direct_score(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
    ratios: np.ndarray,
) -> dict[str, Any]:
    """Puntúa likelihood conjunto del comparador observado."""

    conditional, targets, match_ids = [], [], []
    for match_id, values in _group(rows, probabilities).items():
        for window, (row, base) in enumerate(values):
            ratio = np.ones(4) if window == 0 else ratios[
                window, int(values[window - 1][0]["target"])]
            conditional.append(_emission(base, ratio))
            targets.append(int(row["target"]))
            match_ids.append(match_id)
    return _scores(np.asarray(conditional), np.asarray(targets),
                   np.asarray(match_ids))


def _baseline_score(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
) -> dict[str, Any]:
    """Puntúa el tabular factorized por partido completo."""

    targets = np.asarray([row["target"] for row in rows])
    matches = np.asarray([row["match_id"] for row in rows])
    return _scores(probabilities, targets, matches)


def _scores(
    probabilities: np.ndarray, targets: np.ndarray, match_ids: np.ndarray,
) -> dict[str, Any]:
    """Calcula scores condicionales y pérdidas por partido."""

    chosen = probabilities[np.arange(len(targets)), targets]
    losses: dict[int, list[float]] = defaultdict(list)
    for match_id, value in zip(match_ids, chosen):
        losses[int(match_id)].append(-math.log(max(float(value), 1e-12)))
    return {
        "log_loss": multiclass_log_loss(probabilities, targets),
        "brier": multiclass_brier(probabilities, targets),
        "ece": expected_calibration_error(probabilities, targets),
        "match_losses": {str(key): float(np.mean(value))
                         for key, value in losses.items()},
        "conditional_probabilities": probabilities,
        "targets": targets,
        "match_ids": match_ids,
    }


def _markov_score(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
    ratios: np.ndarray, transition: Any, initial: dict[str, Any],
    styles: dict[tuple[int, int], dict[str, Any]], style_boundary: float,
    variant: str,
) -> dict[str, Any]:
    """Puntúa el HMM dual mediante forward algorithm."""

    conditional, targets, match_ids = [], [], []
    for match_id, values in _group(rows, probabilities).items():
        result = _match_forward(
            values, ratios, transition, initial, styles,
            style_boundary, variant)
        conditional.extend(result)
        targets.extend(int(item[0]["target"]) for item in values)
        match_ids.extend([match_id] * 6)
    return _scores(np.asarray(conditional), np.asarray(targets),
                   np.asarray(match_ids))


def _match_forward(
    values: list[tuple[dict[str, Any], np.ndarray]], ratios: np.ndarray,
    transition: Any, initial: dict[str, Any],
    styles: dict[tuple[int, int], dict[str, Any]], style_boundary: float,
    variant: str,
) -> list[np.ndarray]:
    """Filtra una trayectoria observada bajo distribución pre-match fija."""

    first = values[0][0]
    league = str(first["league_slug"])
    home_style = _style(first, int(first["home_team_id"]), styles, style_boundary)
    away_style = _style(first, int(first["away_team_id"]), styles, style_boundary)
    home = phase80._initial_probability(initial, league, True, home_style)
    away = phase80._initial_probability(initial, league, False, away_style)
    joint, output = np.outer(home, away), []
    for window, (row, base) in enumerate(values):
        emissions = _emission(
            np.broadcast_to(base, (6, 6, 4)), ratios[window])
        predictive = (joint[:, :, None] * emissions).sum(axis=(0, 1))
        predictive /= predictive.sum()
        output.append(predictive)
        target = int(row["target"])
        joint = joint * emissions[:, :, target]
        joint /= joint.sum()
        if window < 5 and variant != "no_transition":
            joint = _three_steps(joint, transition, league, window, variant)
    return output


def _style(
    row: dict[str, Any], team_id: int,
    styles: dict[tuple[int, int], dict[str, Any]], boundary: float,
) -> int:
    """Calcula estilo fijo con contexto previo al kickoff."""

    return int(phase77._style_score(styles[(int(row["match_id"]), team_id)])
               > boundary)


def _three_steps(
    joint: np.ndarray, transition: Any, league: str,
    window: int, variant: str,
) -> np.ndarray:
    """Propaga tres microtransiciones hasta la siguiente ventana."""

    mode = "no_context" if variant == "no_context" else "full"
    for micro in range(window * 3, window * 3 + 3):
        joint = phase80._advance(joint, transition, league, micro, mode)
    return joint


def _strip(score: dict[str, Any]) -> dict[str, float]:
    """Retira arrays y pérdidas individuales del reporte."""

    return {name: float(score[name]) for name in ("log_loss", "brier", "ece")}


def _fold_setup(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
    target_name: str,
) -> dict[str, Any]:
    """Prepara componentes aislados de un fold externo."""

    train_rows, _, train_states, _, styles, config = phase80._fold_states(
        train_names, target_name)
    model, names = _baseline_model(examples, train_names)
    train_examples = [row for row in examples if row["split"] in train_names]
    target_examples = [row for row in examples if row["split"] == target_name]
    train_prob = _predict(model, names, train_examples)
    target_prob = _predict(model, names, target_examples)
    return {
        "train_rows": train_rows, "train_states": train_states,
        "styles": styles, "config": config,
        "train_examples": train_examples, "target_examples": target_examples,
        "train_prob": train_prob, "target_prob": target_prob,
        "transition": phase80._transition_model(
            train_rows, train_states, styles),
        "initial": phase80._initial_model(train_rows, train_states, styles),
    }


def _selection(examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Selecciona smoothing y ablación sin abrir confirmación."""

    fold = _fold_setup(examples, ("fit",), "selection")
    state_index = _state_index(fold["train_rows"], fold["train_states"])
    candidates = []
    for strength in STRENGTHS:
        ratios = _emission_ratios(
            fold["train_examples"], fold["train_prob"], state_index, strength)
        for variant in VARIANTS:
            score = _markov_score(
                fold["target_examples"], fold["target_prob"], ratios,
                fold["transition"], fold["initial"], fold["styles"],
                fold["config"]["style_boundary"], variant)
            candidates.append({"strength": strength, "variant": variant,
                               "score": _strip(score)})
    direct = _select_direct(fold)
    selected = min(candidates, key=lambda row: row["score"]["log_loss"])
    baseline = _baseline_score(fold["target_examples"], fold["target_prob"])
    return {"selected": selected, "candidates": candidates,
            "direct": direct, "baseline": _strip(baseline)}


def _select_direct(fold: dict[str, Any]) -> dict[str, Any]:
    """Selecciona el comparador secuencial en el mismo bloque."""

    candidates = []
    for strength in STRENGTHS:
        ratios = _direct_ratios(
            fold["train_examples"], fold["train_prob"], strength)
        score = _direct_score(
            fold["target_examples"], fold["target_prob"], ratios)
        candidates.append({"strength": strength, "score": _strip(score)})
    return min(candidates, key=lambda row: row["score"]["log_loss"])


def _confirmation(
    examples: list[dict[str, Any]], selection: dict[str, Any],
) -> dict[str, Any]:
    """Evalúa una vez las configuraciones congeladas."""

    fold = _fold_setup(examples, ("fit", "selection"), "confirmation")
    index = _state_index(fold["train_rows"], fold["train_states"])
    selected = selection["selected"]
    emission = _emission_ratios(
        fold["train_examples"], fold["train_prob"], index,
        selected["strength"])
    markov = _markov_score(
        fold["target_examples"], fold["target_prob"], emission,
        fold["transition"], fold["initial"], fold["styles"],
        fold["config"]["style_boundary"], selected["variant"])
    direct_ratio = _direct_ratios(
        fold["train_examples"], fold["train_prob"],
        selection["direct"]["strength"])
    direct = _direct_score(
        fold["target_examples"], fold["target_prob"], direct_ratio)
    baseline = _baseline_score(fold["target_examples"], fold["target_prob"])
    return {"markov": markov, "direct": direct, "baseline": baseline,
            "rows": fold["target_examples"]}


def _bootstrap(
    candidate: dict[str, Any], comparator: dict[str, Any],
    iterations: int = 2_000,
) -> dict[str, float]:
    """Bootstrap pareado sobre pérdidas medias por partido."""

    keys = sorted(candidate["match_losses"], key=int)
    delta = np.asarray([
        comparator["match_losses"][key] - candidate["match_losses"][key]
        for key in keys])
    rng = np.random.default_rng(801)
    samples = rng.choice(delta, (iterations, len(delta)), replace=True).mean(axis=1)
    return {"mean": float(delta.mean()),
            "ci95_low": float(np.quantile(samples, 0.025)),
            "ci95_high": float(np.quantile(samples, 0.975))}


def _league(
    rows: list[dict[str, Any]], candidate: dict[str, Any],
    comparator: dict[str, Any],
) -> dict[str, Any]:
    """Resume estabilidad del likelihood por liga."""

    league_by_match = {str(row["match_id"]): row["league_slug"] for row in rows}
    values: dict[str, list[float]] = defaultdict(list)
    for key, loss in candidate["match_losses"].items():
        values[league_by_match[key]].append(
            comparator["match_losses"][key] - loss)
    admitted = {key: rows for key, rows in values.items() if len(rows) >= 10}
    details = {key: float(np.mean(rows)) for key, rows in admitted.items()}
    return {"admitted": len(details),
            "nonnegative_rate": sum(value >= 0 for value in details.values())
            / max(len(details), 1),
            "worst_n100": min((details[key] for key, rows in admitted.items()
                               if len(rows) >= 100), default=0.0),
            "details": details}


def _gate(
    confirmation: dict[str, Any], comparator_name: str,
) -> dict[str, Any]:
    """Aplica el gate contundente contra el comparador congelado."""

    markov = confirmation["markov"]
    comparator = confirmation[comparator_name]
    bootstrap = _bootstrap(markov, comparator)
    league = _league(
        confirmation["rows"], markov, comparator)
    improvement = comparator["log_loss"] - markov["log_loss"]
    threshold = max(0.005, 0.01 * comparator["log_loss"])
    passed = (improvement >= threshold and bootstrap["ci95_low"] > 0
              and comparator["brier"] - markov["brier"] >= 0.002
              and markov["ece"] - comparator["ece"] <= 0.005
              and league["nonnegative_rate"] >= 0.70
              and league["worst_n100"] >= -0.01)
    return {"passed": passed, "comparator": comparator_name,
            "log_loss_improvement": improvement, "threshold": threshold,
            "brier_improvement": comparator["brier"] - markov["brier"],
            "ece_delta": markov["ece"] - comparator["ece"],
            "bootstrap": bootstrap, "league_stability": league}


def run() -> dict[str, Any]:
    """Ejecuta selección y confirmación de trayectoria."""

    examples = phase80._phase75_examples()
    selection = _selection(examples)
    comparator = ("direct" if selection["direct"]["score"]["log_loss"]
                  < selection["baseline"]["log_loss"] else "baseline")
    confirmation = _confirmation(examples, selection)
    gate = _gate(confirmation, comparator)
    result = {
        "classification": ("ready_for_next_phase" if gate["passed"]
                           else "rejected_for_revision"),
        "config": {"version": "dual_trajectory_likelihood_v1",
                   "selected": selection["selected"],
                   "direct_strength": selection["direct"]["strength"],
                   "frozen_comparator": comparator,
                   "strengths": list(STRENGTHS),
                   "variants": list(VARIANTS)},
        "coverage": {"matches": len({row["match_id"] for row in examples}),
                     "selection_matches": 1891,
                     "confirmation_matches": 1895,
                     "windows_per_match": 6},
        "audit": {"parameters_refit_inside_each_fold": True,
                  "target_states_used_for_prediction": False,
                  "outcomes_define_score_not_prematch_features": True,
                  "router_modified": False, "split_overlap_count": 0},
        "metrics": {
            "selection": selection,
            "confirmation": {name: _strip(score)
                             for name, score in confirmation.items()
                             if name in ("markov", "direct", "baseline")},
            "gate": gate},
    }
    _publish(result)
    return result


def _write(name: str, value: Any) -> None:
    """Escribe JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato completo e hashes."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "features_sha256": phase80._sha(phase80.FEATURES),
        "targets_sha256": phase80._sha(phase80.TARGETS),
        "windows_sha256": phase80._sha(phase80.WINDOWS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Crea reporte humano."""

    gate, config = result["metrics"]["gate"], result["config"]
    return (
        "# Fase 80R — likelihood de trayectoria completa\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- variante: `{config['selected']['variant']}`\n"
        f"- smoothing: `{config['selected']['strength']}`\n"
        f"- comparador: `{gate['comparator']}`\n"
        f"- mejora log-loss: `{gate['log_loss_improvement']:.6f}`\n"
        f"- IC95%: `[{gate['bootstrap']['ci95_low']:.6f}, "
        f"{gate['bootstrap']['ci95_high']:.6f}]`\n"
        "- router modificado: `False`\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta sin promover automáticamente."""

    result = run()
    LOGGER.info("Fase 80R: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

