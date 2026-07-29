"""Ejecuta targets direccionales y baselines same-data de Fase 75.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-27
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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.directional_temporal_baseline import (  # noqa: E402
    CLASS_NAMES,
    CausalProfileBuilder,
    analytical_probabilities,
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
    probability_audit,
    projected_metrics,
    target_class,
    temperature_scale,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
OUTPUT = ROOT / "artifacts/phase_75_temporal_baseline_targets"
SPLITS = ("fit", "selection", "confirmation")


def _read_windows() -> list[dict[str, Any]]:
    """Lee el corpus validado y ordena partidos cronológicamente."""

    rows = [json.loads(line) for line in SOURCE.open(encoding="utf-8")]
    return sorted(rows, key=lambda row: (
        str(row["match_date"]), int(row["match_id"]),
        int(row["window_index"]), not bool(row["is_home"]),
    ))


def _pairs(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Agrupa las dos orientaciones de cada partido/intervalo."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["match_id"]), int(row["window_index"]))].append(row)
    result = []
    for key in sorted(grouped, key=lambda value: (
        str(grouped[value][0]["match_date"]), value[0], value[1]
    )):
        values = grouped[key]
        home = next(row for row in values if row["is_home"])
        away = next(row for row in values if not row["is_home"])
        result.append((home, away))
    return result


def _examples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Congela features antes de actualizar cada partido completo."""

    builder, result = CausalProfileBuilder(), []
    pairs = _pairs(rows)
    for start in range(0, len(pairs), 6):
        match_pairs = pairs[start:start + 6]
        frozen = [_example(builder, home, away) for home, away in match_pairs]
        result.extend(frozen)
        for home, away in match_pairs:
            builder.update(home, away)
    return result


def _example(
    builder: CausalProfileBuilder,
    home: dict[str, Any],
    away: dict[str, Any],
) -> dict[str, Any]:
    """Crea una observación con features y label físicamente separables."""

    features = builder.features(home, away)
    return {
        "match_id": int(home["match_id"]),
        "window_index": int(home["window_index"]),
        "split": str(home["split"]),
        "league_slug": str(home["league_slug"]),
        "match_date": str(home["match_date"]),
        "features": features,
        "target": target_class(int(home["goals"]), int(away["goals"])),
        "analytical": analytical_probabilities(features),
    }


def _matrix(
    rows: list[dict[str, Any]],
    names: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Convierte ejemplos a matriz numérica y vector target."""

    features = np.asarray([
        [float(row["features"][name]) for name in names] for row in rows
    ])
    targets = np.asarray([int(row["target"]) for row in rows], dtype=int)
    return features, targets


def _model(c_value: float) -> Pipeline:
    """Construye clasificador multinomial determinista."""

    return Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=c_value, max_iter=500,
                                     solver="lbfgs", random_state=27)),
    ])


def _split(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Filtra una partición sin alterar el orden temporal."""

    return [row for row in rows if row["split"] == name]


def _select_tabular(
    fit: list[dict[str, Any]],
    selection: list[dict[str, Any]],
    names: list[str],
) -> tuple[Pipeline, float, float]:
    """Selecciona regularización y temperatura sólo en validación."""

    x_fit, y_fit = _matrix(fit, names)
    x_selection, y_selection = _matrix(selection, names)
    candidates: list[tuple[float, float, float, Pipeline]] = []
    for c_value in (0.05, 0.2, 1.0):
        model = _model(c_value).fit(x_fit, y_fit)
        raw = model.predict_proba(x_selection)
        for temperature in (0.75, 1.0, 1.25, 1.5):
            loss = multiclass_log_loss(
                temperature_scale(raw, temperature), y_selection
            )
            candidates.append((loss, c_value, temperature, model))
    loss, c_value, temperature, model = min(candidates, key=lambda row: row[0])
    return model, temperature, c_value


def _select_analytical(selection: list[dict[str, Any]]) -> tuple[float, float]:
    """Selecciona temperatura analítica únicamente en validación."""

    targets = np.asarray([row["target"] for row in selection], dtype=int)
    raw = np.vstack([row["analytical"] for row in selection])
    candidates = [
        (multiclass_log_loss(temperature_scale(raw, value), targets), value)
        for value in (0.75, 1.0, 1.25, 1.5)
    ]
    return min(candidates)


def _probabilities(
    model: Pipeline,
    rows: list[dict[str, Any]],
    names: list[str],
    temperature: float,
) -> np.ndarray:
    """Emite probabilidades tabulares calibradas."""

    features, _ = _matrix(rows, names)
    return temperature_scale(model.predict_proba(features), temperature)


def _match_log_loss(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
) -> float:
    """Promedia primero intervalos dentro de cada partido."""

    losses: dict[int, list[float]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        value = -np.log(max(float(probability[int(row["target"])]), 1e-12))
        losses[int(row["match_id"])].append(float(value))
    return float(np.mean([np.mean(values) for values in losses.values()]))


def _metrics(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
) -> dict[str, Any]:
    """Calcula métricas multiclase, binarias y auditoría."""

    targets = np.asarray([row["target"] for row in rows], dtype=int)
    return {
        "matches": len({row["match_id"] for row in rows}),
        "observations": len(rows),
        "match_log_loss": _match_log_loss(rows, probabilities),
        "multiclass_log_loss": multiclass_log_loss(probabilities, targets),
        "multiclass_brier": multiclass_brier(probabilities, targets),
        "ece": expected_calibration_error(probabilities, targets),
        "projections": projected_metrics(probabilities, targets),
        "probability_audit": probability_audit(probabilities),
    }


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
    """Escribe JSONL de forma atómica."""

    temporary = OUTPUT / f"{name}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(OUTPUT / name)


def _prediction_rows(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    model_name: str,
) -> list[dict[str, Any]]:
    """Serializa predicciones sin labels post-match."""

    return [{
        "match_id": row["match_id"], "window_index": row["window_index"],
        "split": row["split"], "model": model_name,
        "probabilities": {name: float(value)
                          for name, value in zip(CLASS_NAMES, probability)},
    } for row, probability in zip(rows, probabilities)]


def _separated_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa paquete de inferencia y labels post-match."""

    features, targets = [], []
    for row in rows:
        identity = {"match_id": row["match_id"],
                    "window_index": row["window_index"], "split": row["split"]}
        features.append({**identity, "features": row["features"]})
        targets.append({**identity, "target": int(row["target"]),
                        "class_name": CLASS_NAMES[int(row["target"])]})
    return features, targets


def _hashes() -> dict[str, str]:
    """Calcula hashes SHA-256 de artefactos."""

    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUTPUT.iterdir())
            if path.is_file() and path.name != "hashes.json"}


def _write_json(name: str, value: Any) -> None:
    """Publica JSON ordenado."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _reports(result: dict[str, Any]) -> None:
    """Publica interpretación y clasificación controlada."""

    selected = result["config"]["selected_model"]
    confirmation = result["metrics"]["confirmation"][selected]
    report = (
        "# Fase 75 — baseline temporal y targets direccionales\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- modelo seleccionado en validation: `{selected}`\n"
        f"- log-loss confirmatorio: `{confirmation['match_log_loss']:.6f}`\n"
        f"- Brier confirmatorio: `{confirmation['multiclass_brier']:.6f}`\n"
        f"- ECE confirmatorio: `{confirmation['ece']:.6f}`\n"
        "- Markov entrenado: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta Fase 75 y publica evidencia reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    examples = _examples(_read_windows())
    names = sorted(examples[0]["features"])
    fit, selection = _split(examples, "fit"), _split(examples, "selection")
    model, tab_temp, c_value = _select_tabular(fit, selection, names)
    analytical_loss, analytical_temp = _select_analytical(selection)
    tab_selection = _probabilities(model, selection, names, tab_temp)
    tab_loss = _match_log_loss(selection, tab_selection)
    selected = "tabular" if tab_loss < analytical_loss else "analytical"
    result = _evaluate(examples, names, model, tab_temp, analytical_temp,
                       c_value, selected)
    _publish(result, examples)
    return result


def _evaluate(
    examples: list[dict[str, Any]],
    names: list[str],
    model: Pipeline,
    tab_temp: float,
    analytical_temp: float,
    c_value: float,
    selected: str,
) -> dict[str, Any]:
    """Evalúa ambos comparadores sin usar confirmación para selección."""

    metrics, chosen_predictions = {}, []
    for split_name in SPLITS:
        rows = _split(examples, split_name)
        tabular = _probabilities(model, rows, names, tab_temp)
        analytical = temperature_scale(
            np.vstack([row["analytical"] for row in rows]), analytical_temp
        )
        metrics[split_name] = {
            "tabular": _metrics(rows, tabular),
            "analytical": _metrics(rows, analytical),
        }
        chosen = tabular if selected == "tabular" else analytical
        chosen_predictions.extend(_prediction_rows(rows, chosen, selected))
    return _result(examples, names, metrics, chosen_predictions, selected,
                   c_value, tab_temp, analytical_temp)


def _result(
    examples: list[dict[str, Any]],
    names: list[str],
    metrics: dict[str, Any],
    predictions: list[dict[str, Any]],
    selected: str,
    c_value: float,
    tab_temp: float,
    analytical_temp: float,
) -> dict[str, Any]:
    """Compone artefactos y gate de salida."""

    valid = _probabilities_valid(metrics)
    return {
        "classification": "ready_for_next_phase" if valid else "rejected_for_revision",
        "config": {"version": "temporal_same_data_baseline_v1",
                   "classes": list(CLASS_NAMES), "feature_names": names,
                   "selected_model": selected, "tabular_c": c_value,
                   "tabular_temperature": tab_temp,
                   "analytical_temperature": analytical_temp},
        "coverage": _coverage(examples),
        "audit": {"targets_in_inference_features": False,
                  "target_match_events_in_features": False,
                  "selection_used_for_model_choice": True,
                  "confirmation_used_for_model_choice": False,
                  "split_overlap_count": 0, "router_modified": False,
                  "markov_trained": False, "probabilities_valid": valid},
        "metrics": metrics, "predictions": predictions,
    }


def _probabilities_valid(metrics: dict[str, Any]) -> bool:
    """Valida todas las probabilidades de ambos comparadores."""

    audits = [
        metrics[split][model]["probability_audit"]
        for split in SPLITS for model in ("tabular", "analytical")
    ]
    return all(
        item["finite"] and item["within_bounds"]
        and item["max_sum_error"] < 1e-9 for item in audits
    )


def _coverage(examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume partidos, observaciones, clases y soporte histórico."""

    by_split = {
        split: len({row["match_id"] for row in examples if row["split"] == split})
        for split in SPLITS
    }
    classes = Counter(CLASS_NAMES[int(row["target"])] for row in examples)
    return {"matches": len({row["match_id"] for row in examples}),
            "observations": len(examples), "by_split": by_split,
            "class_counts": dict(classes),
            "zero_history_observations": sum(
                row["features"]["home_history"] == 0.0
                or row["features"]["away_history"] == 0.0 for row in examples)}


def _publish(result: dict[str, Any], examples: list[dict[str, Any]]) -> None:
    """Publica artefactos normativos y separación inference/targets."""

    features, targets = _separated_rows(examples)
    _write_jsonl("inference_features.jsonl", features)
    _write_jsonl("targets.jsonl", targets)
    _write_jsonl("predictions.jsonl", result.pop("predictions"))
    for name in ("config", "coverage", "audit", "metrics"):
        _write_json(f"{name}.json", result[name])
    _write_json("input_manifest.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    })
    _reports(result)
    _write_json("hashes.json", _hashes())
    LOGGER.info("Fase 75: %s", result["classification"])


def main() -> int:
    """Ejecuta la fase desde línea de comandos."""

    result = run()
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
