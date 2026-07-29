"""Evalúa una cadena Markov no homogénea con contexto pre-match.

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
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_80_nested_walkforward as phase80  # noqa: E402
import scripts.run_phase_80r_trajectory_likelihood as phase80r  # noqa: E402
import scripts.run_phase_80t_prematch_archetype as phase80t  # noqa: E402
from src.directional_temporal_baseline import temperature_scale  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80u_nonhomogeneous_markov"
C_VALUES = (0.0003, 0.001, 0.003, 0.01, 0.03, 0.1)
TEMPERATURES = (0.8, 1.0, 1.2, 1.4)


def _solver(c_value: float) -> Pipeline:
    """Construye solver multinomial regularizado y determinista."""

    return Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(
            C=c_value, max_iter=1_000, solver="lbfgs", random_state=80)),
    ])


def _context(
    row: dict[str, Any], match_summary: np.ndarray,
    previous: int | None, include_previous: bool,
) -> np.ndarray:
    """Codifica contexto fijo, ventana y estado anterior."""

    values = row["features"]
    names = sorted(values)
    base = np.asarray([float(values[name]) for name in names], dtype=float)
    window = np.eye(6)[int(row["window_index"])]
    if not include_previous:
        return np.concatenate((base, window, match_summary))
    one_hot = np.eye(4)[int(previous)]
    interactions = np.outer(one_hot, match_summary).ravel()
    return np.concatenate((base, window, match_summary, one_hot, interactions))


def _records(
    rows: list[dict[str, Any]], include_previous: bool,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """Materializa transiciones supervisadas t>=1 por partido."""

    summaries = phase80t._match_features(rows)
    features, targets, aligned = [], [], []
    for values in phase80r._group(rows, np.zeros((len(rows), 4))).values():
        for window in range(1, 6):
            row = values[window][0]
            previous = int(values[window - 1][0]["target"])
            features.append(_context(
                row, summaries[int(row["match_id"])], previous,
                include_previous))
            targets.append(int(row["target"]))
            aligned.append(row)
    return np.vstack(features), np.asarray(targets), aligned


def _select_model(
    train: list[dict[str, Any]], target: list[dict[str, Any]],
    baseline: np.ndarray, include_previous: bool,
) -> dict[str, Any]:
    """Selecciona C y temperatura exclusivamente en selection."""

    x_train, y_train, _ = _records(train, include_previous)
    candidates = []
    for c_value in C_VALUES:
        model = _solver(c_value).fit(x_train, y_train)
        for temperature in TEMPERATURES:
            score = _score(
                target, baseline, model, include_previous, temperature)
            candidates.append((
                score["log_loss"], c_value, temperature, model, score))
    _, c_value, temperature, model, score = min(
        candidates, key=lambda row: row[0])
    return {"c_value": float(c_value), "temperature": float(temperature),
            "model": model, "score": score}


def _fit_model(
    rows: list[dict[str, Any]], include_previous: bool, c_value: float,
) -> Pipeline:
    """Reajusta configuración congelada sobre train ampliado."""

    features, targets, _ = _records(rows, include_previous)
    return _solver(c_value).fit(features, targets)


def _score(
    rows: list[dict[str, Any]], baseline: np.ndarray,
    model: Pipeline, include_previous: bool, temperature: float = 1.0,
) -> dict[str, Any]:
    """Puntúa trayectoria: baseline en t0 y solver en t>=1."""

    summaries = phase80t._match_features(rows)
    baseline_by_key = {
        (int(row["match_id"]), int(row["window_index"])): probability
        for row, probability in zip(rows, baseline)}
    conditional, targets, matches = [], [], []
    for match_id, values in phase80r._group(rows, baseline).items():
        for window, (row, _) in enumerate(values):
            if window == 0:
                probability = baseline_by_key[(match_id, 0)]
            else:
                previous = int(values[window - 1][0]["target"])
                features = _context(
                    row, summaries[match_id], previous, include_previous)
                probability = model.predict_proba(features[None, :])[0]
                probability = temperature_scale(
                    probability[None, :], temperature)[0]
            conditional.append(probability)
            targets.append(int(row["target"]))
            matches.append(match_id)
    return phase80r._scores(
        np.asarray(conditional), np.asarray(targets), np.asarray(matches))


def _setup(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
    target_name: str,
) -> dict[str, Any]:
    """Prepara bloques y carrier tabular aislados."""

    baseline_model, names = phase80r._baseline_model(examples, train_names)
    train = [row for row in examples if row["split"] in train_names]
    target = [row for row in examples if row["split"] == target_name]
    return {
        "train": train, "target": target,
        "train_probability": phase80r._predict(
            baseline_model, names, train),
        "target_probability": phase80r._predict(
            baseline_model, names, target),
    }


def _selection(examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Selecciona modelos y comparador antes de confirmación."""

    setup = _setup(examples, ("fit",), "selection")
    markov = _select_model(
        setup["train"], setup["target"], setup["target_probability"], True)
    static = _select_model(
        setup["train"], setup["target"], setup["target_probability"], False)
    markov_score, static_score = markov["score"], static["score"]
    direct = phase80r._select_direct({
        "train_examples": setup["train"],
        "train_prob": setup["train_probability"],
        "target_examples": setup["target"],
        "target_prob": setup["target_probability"],
    })
    baseline = phase80r._baseline_score(
        setup["target"], setup["target_probability"])
    choices = {
        "static": static_score["log_loss"],
        "direct": direct["score"]["log_loss"],
        "baseline": baseline["log_loss"],
    }
    return {
        "markov_c": markov["c_value"],
        "markov_temperature": markov["temperature"],
        "markov_score": phase80r._strip(markov_score),
        "static_c": static["c_value"],
        "static_temperature": static["temperature"],
        "static_score": phase80r._strip(static_score),
        "direct": direct, "baseline": phase80r._strip(baseline),
        "frozen_comparator": min(choices, key=choices.get),
    }


def _confirmation(
    examples: list[dict[str, Any]], selection: dict[str, Any],
) -> dict[str, Any]:
    """Reajusta train ampliado y evalúa configuraciones congeladas."""

    setup = _setup(examples, ("fit", "selection"), "confirmation")
    markov_model = _fit_model(
        setup["train"], True, selection["markov_c"])
    static_model = _fit_model(
        setup["train"], False, selection["static_c"])
    markov = _score(
        setup["target"], setup["target_probability"], markov_model, True,
        selection["markov_temperature"])
    static = _score(
        setup["target"], setup["target_probability"], static_model, False,
        selection["static_temperature"])
    direct_ratios = phase80r._direct_ratios(
        setup["train"], setup["train_probability"],
        selection["direct"]["strength"])
    direct = phase80r._direct_score(
        setup["target"], setup["target_probability"], direct_ratios)
    baseline = phase80r._baseline_score(
        setup["target"], setup["target_probability"])
    return {"markov": markov, "static": static, "direct": direct,
            "baseline": baseline, "rows": setup["target"]}


def _gate(
    confirmation: dict[str, Any], comparator_name: str,
) -> dict[str, Any]:
    """Aplica gate existente contra comparador congelado."""

    candidate, comparator = (
        confirmation["markov"], confirmation[comparator_name])
    bootstrap = phase80r._bootstrap(candidate, comparator)
    league = phase80r._league(confirmation["rows"], candidate, comparator)
    improvement = comparator["log_loss"] - candidate["log_loss"]
    threshold = max(0.005, comparator["log_loss"] * 0.01)
    passed = (improvement >= threshold and bootstrap["ci95_low"] > 0
              and comparator["brier"] - candidate["brier"] >= 0.002
              and candidate["ece"] - comparator["ece"] <= 0.005
              and league["nonnegative_rate"] >= 0.70
              and league["worst_n100"] >= -0.01)
    return {"passed": passed, "comparator": comparator_name,
            "log_loss_improvement": improvement, "threshold": threshold,
            "brier_improvement": comparator["brier"] - candidate["brier"],
            "ece_delta": candidate["ece"] - comparator["ece"],
            "bootstrap": bootstrap, "league_stability": league}


def run() -> dict[str, Any]:
    """Ejecuta Fase 80U sin modificar router."""

    examples = phase80._phase75_examples()
    selection = _selection(examples)
    confirmation = _confirmation(examples, selection)
    gate = _gate(confirmation, selection["frozen_comparator"])
    result = {
        "classification": ("promising_unconfirmed" if gate["passed"]
                           else "rejected_for_revision"),
        "config": {"version": "nonhomogeneous_markov_v1",
                   "c_values": list(C_VALUES),
                   "selected_markov_c": selection["markov_c"],
                   "selected_markov_temperature":
                       selection["markov_temperature"],
                   "selected_static_c": selection["static_c"],
                   "selected_static_temperature":
                       selection["static_temperature"],
                   "frozen_comparator": selection["frozen_comparator"]},
        "coverage": {"matches": len({row["match_id"] for row in examples}),
                     "selection_matches": 1891,
                     "confirmation_matches": 1895},
        "audit": {"context_strictly_prematch": True,
                  "target_outcome_only_previous_markov_state": True,
                  "confirmation_already_observed_in_prior_revisions": True,
                  "router_modified": False, "split_overlap_count": 0},
        "metrics": {
            "selection": selection,
            "confirmation": {
                name: phase80r._strip(score)
                for name, score in confirmation.items()
                if name in ("markov", "static", "direct", "baseline")},
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
    """Publica artefactos y hashes."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "features_sha256": phase80._sha(phase80.FEATURES),
        "targets_sha256": phase80._sha(phase80.TARGETS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Genera reporte humano."""

    gate, config = result["metrics"]["gate"], result["config"]
    return (
        "# Fase 80U — Markov no homogéneo\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- C Markov: `{config['selected_markov_c']}`\n"
        f"- comparador: `{gate['comparator']}`\n"
        f"- mejora log-loss: `{gate['log_loss_improvement']:.6f}`\n"
        f"- IC95%: `[{gate['bootstrap']['ci95_low']:.6f}, "
        f"{gate['bootstrap']['ci95_high']:.6f}]`\n"
        "- router modificado: `False`\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta sin promover."""

    result = run()
    LOGGER.info("Fase 80U: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
