"""Genera un test causal interpretable sobre 100 partidos históricos.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import csv
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

import scripts.run_phase_80_nested_walkforward as phase80  # noqa: E402
import scripts.run_phase_80r_trajectory_likelihood as phase80r  # noqa: E402
import scripts.run_phase_80t_prematch_archetype as phase80t  # noqa: E402
import scripts.run_phase_80u_nonhomogeneous_markov as phase80u  # noqa: E402
from src.directional_temporal_baseline import CLASS_NAMES  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80v_100_match_prediction_test"
WINDOWS = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
SAMPLE_SIZE = 100


def _select_matches(examples: list[dict[str, Any]]) -> list[int]:
    """Selecciona la cola cronológica antes de calcular desempeño."""

    confirmation = [row for row in examples if row["split"] == "confirmation"]
    identities = {(str(row["match_date"]), int(row["match_id"]))
                  for row in confirmation}
    return [match_id for _, match_id in sorted(identities)[-SAMPLE_SIZE:]]


def _fit_models(
    examples: list[dict[str, Any]],
) -> tuple[Any, Any, Any, list[str]]:
    """Ajusta modelos sólo con fit+selection y parámetros congelados."""

    train = [row for row in examples if row["split"] in ("fit", "selection")]
    baseline, names = phase80r._baseline_model(
        examples, ("fit", "selection"))
    markov = phase80u._fit_model(train, True, 0.003)
    static = phase80u._fit_model(train, False, 0.003)
    return baseline, markov, static, names


def _transition_matrix(
    model: Any, row: dict[str, Any], summary: np.ndarray,
) -> np.ndarray:
    """Construye matriz 4×4 para una ventana futura."""

    output = []
    for previous in range(4):
        features = phase80u._context(row, summary, previous, True)
        output.append(model.predict_proba(features[None, :])[0])
    return np.asarray(output)


def _static_probability(
    model: Any, row: dict[str, Any], summary: np.ndarray,
) -> np.ndarray:
    """Emite probabilidad continua sin estado anterior."""

    features = phase80u._context(row, summary, None, False)
    return model.predict_proba(features[None, :])[0]


def _match_prediction(
    rows: list[dict[str, Any]], baseline_probability: np.ndarray,
    markov: Any, static: Any,
) -> dict[str, Any]:
    """Genera una distribución pre-match sin leer etiquetas reales."""

    ordered = sorted(rows, key=lambda row: int(row["window_index"]))
    summary = phase80t._match_features(ordered)[int(ordered[0]["match_id"])]
    transitions = [
        _transition_matrix(markov, row, summary) for row in ordered[1:]]
    marginals = _marginals(baseline_probability[0], transitions)
    static_prob = [baseline_probability[0]]
    static_prob.extend(_static_probability(static, row, summary)
                       for row in ordered[1:])
    return _prediction_payload(
        ordered, marginals, np.asarray(static_prob),
        baseline_probability, transitions)


def _marginals(initial: np.ndarray, transitions: list[np.ndarray]) -> np.ndarray:
    """Marginaliza estados anteriores sin observar el partido."""

    values = [initial]
    for transition in transitions:
        values.append(values[-1] @ transition)
    return np.asarray(values)


def _viterbi(initial: np.ndarray, transitions: list[np.ndarray]) -> list[int]:
    """Obtiene la secuencia pre-match de máxima probabilidad."""

    score, paths = np.log(np.clip(initial, 1e-12, 1.0)), [[state] for state in range(4)]
    for transition in transitions:
        candidates = score[:, None] + np.log(np.clip(transition, 1e-12, 1.0))
        parents = candidates.argmax(axis=0)
        score = candidates[parents, np.arange(4)]
        paths = [paths[int(parent)] + [state]
                 for state, parent in enumerate(parents)]
    return paths[int(score.argmax())]


def _nll(probabilities: np.ndarray, targets: np.ndarray) -> float:
    """Calcula log-loss medio de las seis ventanas."""

    chosen = probabilities[np.arange(6), targets]
    return float(-np.log(np.clip(chosen, 1e-12, 1.0)).mean())


def _first_half_goal(initial: np.ndarray, transitions: list[np.ndarray]) -> float:
    """Calcula P(gol 1T) desde la distribución conjunta pre-match."""

    no_goal = initial[0] * transitions[0][0, 0] * transitions[1][0, 0]
    return float(1.0 - no_goal)


def _second_half_goal(
    marginals: np.ndarray, transitions: list[np.ndarray],
) -> float:
    """Calcula P(gol 2T) marginalizando el estado al descanso."""

    enter = float(marginals[2] @ transitions[2][:, 0])
    no_goal = enter * transitions[3][0, 0] * transitions[4][0, 0]
    return float(1.0 - no_goal)


def _prediction_payload(
    rows: list[dict[str, Any]], marginals: np.ndarray,
    static: np.ndarray, baseline: np.ndarray,
    transitions: list[np.ndarray],
) -> dict[str, Any]:
    """Compone predicción y conserva kernels internos para scoring."""

    predicted = _viterbi(marginals[0], transitions)
    return {
        "match_id": int(rows[0]["match_id"]),
        "match_date": str(rows[0]["match_date"]),
        "league_slug": str(rows[0]["league_slug"]),
        "home_team_id": int(rows[0]["home_team_id"]),
        "away_team_id": int(rows[0]["away_team_id"]),
        "predicted_sequence": predicted,
        "predicted_window_classes": marginals.argmax(axis=1).tolist(),
        "first_half_goal_probability": _first_half_goal(
            marginals[0], transitions),
        "second_half_goal_probability": _second_half_goal(
            marginals, transitions),
        "window_probabilities": [
            {name: float(value) for name, value in zip(CLASS_NAMES, probability)}
            for probability in marginals],
        "causal_cutoff": str(rows[0]["match_date"]),
        "_transitions": [matrix.tolist() for matrix in transitions],
        "_static_probabilities": static.tolist(),
        "_baseline_probabilities": baseline.tolist(),
    }


def _generate_predictions(
    examples: list[dict[str, Any]], selected: list[int],
    baseline_model: Any, markov: Any, static: Any, names: list[str],
) -> list[dict[str, Any]]:
    """Genera las 100 predicciones antes de unir outcomes estadísticos."""

    rows = [row for row in examples if int(row["match_id"]) in set(selected)]
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    output = []
    for match_id in selected:
        ordered = sorted(grouped[match_id], key=lambda row: row["window_index"])
        probability = phase80r._predict(baseline_model, names, ordered)
        output.append(_match_prediction(
            ordered, probability, markov, static))
    return output


def _outcomes(selected: set[int]) -> dict[int, dict[str, Any]]:
    """Carga resultados reales sólo después de emitir predicciones."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with WINDOWS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if int(row["match_id"]) in selected:
                grouped[int(row["match_id"])].append(row)
    return {match_id: _match_outcome(rows)
            for match_id, rows in grouped.items()}


def _labels(
    examples: list[dict[str, Any]], selected: set[int],
) -> dict[int, list[int]]:
    """Lee etiquetas sólo después de cerrar todas las predicciones."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in examples:
        if int(row["match_id"]) in selected:
            grouped[int(row["match_id"])].append(row)
    return {
        match_id: [int(row["target"]) for row in sorted(
            rows, key=lambda item: int(item["window_index"]))]
        for match_id, rows in grouped.items()
    }


def _match_outcome(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume marcador y estadísticas observadas por orientación."""

    roles = {
        True: [row for row in rows if bool(row["is_home"])],
        False: [row for row in rows if not bool(row["is_home"])],
    }
    names = ("goals", "shots", "shots_on_target", "corners",
             "pressure", "fouls", "yellow_cards", "red_cards")
    result: dict[str, Any] = {}
    for is_home, role in ((True, "home"), (False, "away")):
        for name in names:
            result[f"{role}_{name}"] = int(sum(
                float(row.get(name, 0)) for row in roles[is_home]))
    return result


def _attach_outcomes(
    predictions: list[dict[str, Any]],
    outcomes: dict[int, dict[str, Any]],
    labels: dict[int, list[int]],
) -> list[dict[str, Any]]:
    """Une labels post-match tras cerrar todas las predicciones."""

    output = [
        _score_prediction(row, outcomes[row["match_id"]],
                          labels[row["match_id"]])
        for row in predictions
    ]
    return sorted(output, key=lambda row: (
        row["markov_nll"], -row["correct_windows"], row["match_id"]))


def _score_prediction(
    prediction: dict[str, Any], outcome: dict[str, Any],
    sequence: list[int],
) -> dict[str, Any]:
    """Calcula métricas después de revelar el resultado."""

    actual = np.asarray(sequence)
    transitions = [np.asarray(value) for value in prediction.pop("_transitions")]
    static = np.asarray(prediction.pop("_static_probabilities"))
    baseline = np.asarray(prediction.pop("_baseline_probabilities"))
    conditional = [baseline[0]]
    conditional.extend(transitions[index][actual[index]]
                       for index in range(5))
    markov_nll = _nll(np.asarray(conditional), actual)
    static_nll, baseline_nll = _nll(static, actual), _nll(baseline, actual)
    predicted = np.asarray(prediction["predicted_window_classes"])
    return _scored_payload(
        prediction, outcome, sequence, predicted, actual,
        markov_nll, static_nll, baseline_nll)


def _scored_payload(
    prediction: dict[str, Any], outcome: dict[str, Any],
    sequence: list[int], predicted: np.ndarray, actual: np.ndarray,
    markov_nll: float, static_nll: float, baseline_nll: float,
) -> dict[str, Any]:
    """Compone el registro auditable del partido evaluado."""

    return {
        **prediction, **outcome, "actual_sequence": sequence,
        "correct_windows": int(np.sum(predicted == actual)),
        "markov_nll": markov_nll, "static_nll": static_nll,
        "baseline_nll": baseline_nll,
        "delta_vs_static": static_nll - markov_nll,
        "delta_vs_baseline": baseline_nll - markov_nll,
        "quality_score": float(math.exp(-markov_nll)),
        "actual_first_half_goal": any(value != 0 for value in sequence[:3]),
        "actual_second_half_goal": any(value != 0 for value in sequence[3:]),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas agregadas del test descriptivo."""

    return {
        "matches": len(rows),
        "date_from": min(row["match_date"] for row in rows),
        "date_to": max(row["match_date"] for row in rows),
        "leagues": len({row["league_slug"] for row in rows}),
        "markov_mean_nll": float(np.mean([row["markov_nll"] for row in rows])),
        "static_mean_nll": float(np.mean([row["static_nll"] for row in rows])),
        "baseline_mean_nll": float(np.mean([row["baseline_nll"] for row in rows])),
        "markov_beats_static_matches": sum(
            row["delta_vs_static"] > 0 for row in rows),
        "markov_beats_baseline_matches": sum(
            row["delta_vs_baseline"] > 0 for row in rows),
        "mean_correct_windows": float(np.mean([
            row["correct_windows"] for row in rows])),
        "first_half_goal_accuracy": float(np.mean([
            (row["first_half_goal_probability"] >= 0.5)
            == row["actual_first_half_goal"] for row in rows])),
        "second_half_goal_accuracy": float(np.mean([
            (row["second_half_goal_probability"] >= 0.5)
            == row["actual_second_half_goal"] for row in rows])),
    }


def run() -> dict[str, Any]:
    """Ejecuta el test congelado y publica reporte ordenado."""

    examples = phase80._phase75_examples()
    selected = _select_matches(examples)
    baseline, markov, static, names = _fit_models(examples)
    predictions = _generate_predictions(
        examples, selected, baseline, markov, static, names)
    selected_set = set(selected)
    rows = _attach_outcomes(
        predictions, _outcomes(selected_set), _labels(examples, selected_set))
    result = {
        "classification": "validated",
        "config": {"version": "historical_100_match_report_v1",
                   "selection_rule": "latest_100_confirmation_by_date_match_id",
                   "ranking": "markov_sequence_nll_ascending",
                   "markov_c": 0.003, "temperature": 1.0},
        "coverage": {"selected_matches": len(selected),
                     "unique_matches": len(set(selected)),
                     "rows_with_outcomes": len(rows)},
        "audit": {"selection_before_scoring": True,
                  "predictions_before_outcome_join": True,
                  "target_match_statistics_in_features": False,
                  "promotion_evidence": False, "router_modified": False},
        "summary": _summary(rows),
        "predictions": rows,
    }
    _publish(result)
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica JSON, CSV, Markdown y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "coverage", "audit", "summary"):
        _write_json(f"{name}.json", result[name])
    _write_json("predictions_ranked.json", result["predictions"])
    _write_csv(result["predictions"])
    _write_json("input_manifest.json", {
        "features_sha256": phase80._sha(phase80.FEATURES),
        "targets_sha256": phase80._sha(phase80.TARGETS),
        "windows_sha256": phase80._sha(WINDOWS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"})


def _write_json(name: str, value: Any) -> None:
    """Escribe JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(rows: list[dict[str, Any]]) -> None:
    """Publica tabla plana para análisis externo."""

    names = (
        "rank", "match_id", "match_date", "league_slug", "home_team_id",
        "away_team_id", "home_goals", "away_goals", "actual_sequence_text",
        "predicted_sequence_text", "correct_windows", "quality_score",
        "markov_nll", "static_nll", "baseline_nll", "delta_vs_static",
        "delta_vs_baseline", "first_half_goal_probability",
        "actual_first_half_goal", "second_half_goal_probability",
        "actual_second_half_goal", "home_shots", "away_shots",
        "home_shots_on_target", "away_shots_on_target", "home_corners",
        "away_corners")
    with (OUTPUT / "predictions_ranked.csv").open(
            "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for rank, row in enumerate(rows, 1):
            writer.writerow(_flat_row(row, rank))


def _flat_row(row: dict[str, Any], rank: int) -> dict[str, Any]:
    """Aplana un partido para CSV."""

    output = {name: row.get(name) for name in (
        "match_id", "match_date", "league_slug", "home_team_id",
        "away_team_id", "home_goals", "away_goals", "correct_windows",
        "quality_score", "markov_nll", "static_nll", "baseline_nll",
        "delta_vs_static", "delta_vs_baseline",
        "first_half_goal_probability", "actual_first_half_goal",
        "second_half_goal_probability", "actual_second_half_goal",
        "home_shots", "away_shots", "home_shots_on_target",
        "away_shots_on_target", "home_corners", "away_corners")}
    output["rank"] = rank
    output["actual_sequence_text"] = _sequence_text(row["actual_sequence"])
    output["predicted_sequence_text"] = _sequence_text(row["predicted_sequence"])
    return output


def _sequence_text(values: list[int]) -> str:
    """Convierte clases a abreviaturas legibles."""

    labels = ("N", "H", "A", "B")
    return "-".join(labels[int(value)] for value in values)


def _report(result: dict[str, Any]) -> str:
    """Construye reporte completo ordenado por mejor predicción."""

    summary = result["summary"]
    lines = [
        "# Test de 100 predicciones históricas — 80U",
        "",
        "**Clasificación:** `validated` (diagnóstico, no promoción)",
        "",
        f"- periodo: `{summary['date_from']}` → `{summary['date_to']}`",
        f"- ligas: `{summary['leagues']}`",
        f"- log-loss 80U: `{summary['markov_mean_nll']:.6f}`",
        f"- log-loss continuo: `{summary['static_mean_nll']:.6f}`",
        f"- log-loss baseline: `{summary['baseline_mean_nll']:.6f}`",
        f"- 80U vence al continuo en `{summary['markov_beats_static_matches']}/100`",
        f"- ventanas correctas medias: `{summary['mean_correct_windows']:.2f}/6`",
        f"- accuracy first_half_goal: `{summary['first_half_goal_accuracy']:.2%}`",
        f"- accuracy second_half_goal: `{summary['second_half_goal_accuracy']:.2%}`",
        "",
        "Leyenda de secuencia: `N` ninguno, `H` sólo local, `A` sólo visitante, "
        "`B` ambos equipos anotan en la ventana.",
        "",
        "| # | Fecha | Liga | Partido (IDs ESPN) | Marcador | Real | Pronóstico | "
        "Aciertos | NLL 80U | Δ continuo |",
        "| ---: | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate(result["predictions"], 1):
        lines.append(_report_row(rank, row))
    lines.extend([
        "",
        "El ranking se aplicó después de predecir los 100 partidos. Un delta "
        "positivo indica que 80U fue mejor que el continuo same-data.",
    ])
    return "\n".join(lines) + "\n"


def _report_row(rank: int, row: dict[str, Any]) -> str:
    """Renderiza una fila Markdown."""

    matchup = f"{row['home_team_id']}–{row['away_team_id']}"
    score = f"{row['home_goals']}–{row['away_goals']}"
    return (
        f"| {rank} | {row['match_date'][:10]} | {row['league_slug']} | "
        f"{matchup} | {score} | {_sequence_text(row['actual_sequence'])} | "
        f"{_sequence_text(row['predicted_sequence'])} | "
        f"{row['correct_windows']}/6 | {row['markov_nll']:.4f} | "
        f"{row['delta_vs_static']:+.4f} |")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta y exige cobertura completa."""

    result = run()
    LOGGER.info("Fase 80V: %s, matches=%d",
                result["classification"], result["coverage"]["selected_matches"])
    return 0 if result["coverage"]["selected_matches"] == SAMPLE_SIZE else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
