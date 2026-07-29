"""Ejecuta calibración prequential de nueve mercados.

# Requirements:
# numpy>=2
# scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_probability_calibration import PlattCalibrator  # noqa: E402

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / (
    "artifacts/phase_94_historical_500_semiofficial/"
    "ranked_500_predictions.json")
OUTPUT = ROOT / "artifacts/phase_95_market_probability_calibration"
WARMUP = 100
BOOTSTRAP = 10_000


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read() -> list[dict[str, Any]]:
    """Lee y ordena la cohorte por kickoff e identidad."""

    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    return sorted(rows, key=lambda row: (
        str(row["match_date"]), int(row["match_id"])))


def _calibrate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Emite cada calibración usando únicamente historia anterior."""

    history: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    output = []
    for index, row in enumerate(rows):
        predictions = _calibrate_match(row, history, index)
        if index >= WARMUP:
            output.append({
                "match_id": int(row["match_id"]),
                "match_date": str(row["match_date"]),
                "league_slug": str(row["league_slug"]),
                "markets": predictions,
            })
        _update_history(row, history)
    return output


def _calibrate_match(
    row: dict[str, Any], history: dict[str, list[tuple[float, bool]]],
    index: int,
) -> dict[str, Any]:
    """Calibra todas las líneas antes de revelar el target actual."""

    output = {}
    for name, market in sorted(row["markets"].items()):
        probability = float(market["probability"])
        calibrated = probability
        if index >= WARMUP:
            previous = history[name]
            calibrated = _fit_predict(previous, probability)
        output[name] = _score(
            probability, calibrated, bool(market["actual"]))
    return output


def _fit_predict(
    history: list[tuple[float, bool]], probability: float,
) -> float:
    """Ajusta Platt sobre historia expansiva y predice una observación."""

    probabilities = [row[0] for row in history]
    outcomes = [row[1] for row in history]
    if len(set(outcomes)) != 2:
        return probability
    return PlattCalibrator().fit(probabilities, outcomes).predict(probability)


def _update_history(
    row: dict[str, Any], history: dict[str, list[tuple[float, bool]]],
) -> None:
    """Revela outcomes sólo después de emitir el partido."""

    for name, market in row["markets"].items():
        history[name].append((
            float(market["probability"]), bool(market["actual"])))


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario acotado."""

    probability = min(max(probability, 1e-12), 1.0 - 1e-12)
    return -math.log(probability if actual else 1.0 - probability)


def _score(
    raw: float, calibrated: float, actual: bool,
) -> dict[str, Any]:
    """Puntúa probabilidades raw y calibradas."""

    return {
        "raw_probability": raw, "calibrated_probability": calibrated,
        "actual": actual,
        "raw_log_loss": _loss(raw, actual),
        "calibrated_log_loss": _loss(calibrated, actual),
        "raw_brier": (raw - float(actual)) ** 2,
        "calibrated_brier": (calibrated - float(actual)) ** 2,
        "raw_correct": (raw >= 0.5) == actual,
        "calibrated_correct": (calibrated >= 0.5) == actual,
    }


def _ece(values: list[dict[str, Any]], key: str) -> float:
    """Calcula ECE binario con diez bins fijos."""

    total, error = len(values), 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        upper = lower + 0.1
        selected = [
            row for row in values
            if lower <= float(row[key]) < upper
            or upper >= 1.0 and float(row[key]) == 1.0]
        if selected:
            confidence = float(np.mean([row[key] for row in selected]))
            observed = float(np.mean([row["actual"] for row in selected]))
            error += len(selected) / total * abs(confidence - observed)
    return error


def _aggregate(values: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas probabilísticas y clasificatorias."""

    return {
        "predictions": len(values),
        "raw_log_loss": float(np.mean([row["raw_log_loss"] for row in values])),
        "calibrated_log_loss": float(np.mean([
            row["calibrated_log_loss"] for row in values])),
        "raw_brier": float(np.mean([row["raw_brier"] for row in values])),
        "calibrated_brier": float(np.mean([
            row["calibrated_brier"] for row in values])),
        "raw_accuracy": float(np.mean([row["raw_correct"] for row in values])),
        "calibrated_accuracy": float(np.mean([
            row["calibrated_correct"] for row in values])),
        "raw_ece": _ece(values, "raw_probability"),
        "calibrated_ece": _ece(values, "calibrated_probability"),
    }


def _bootstrap(rows: list[dict[str, Any]]) -> list[float]:
    """Bootstrap pareado por partido de la mejora de log-loss."""

    deltas = [
        float(np.mean([
            market["raw_log_loss"] - market["calibrated_log_loss"]
            for market in row["markets"].values()]))
        for row in rows
    ]
    generator = random.Random(9500)
    samples = sorted(float(np.mean([
        generator.choice(deltas) for _ in deltas]))
        for _ in range(BOOTSTRAP))
    return [samples[249], samples[9749]]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume cada mercado y el bloque completo."""

    names = sorted(rows[0]["markets"])
    markets = {
        name: _aggregate([row["markets"][name] for row in rows])
        for name in names
    }
    total = _aggregate([
        row["markets"][name] for row in rows for name in names])
    total["bootstrap_log_loss_improvement_ci95"] = _bootstrap(rows)
    recommended = [
        name for name, value in markets.items()
        if value["calibrated_log_loss"] <= value["raw_log_loss"]
        and value["calibrated_brier"] <= value["raw_brier"]
        and value["calibrated_ece"] <= value["raw_ece"]
    ]
    return {"markets": markets, "total": total,
            "calibration_recommended_markets": recommended}


def _write(name: str, payload: Any) -> None:
    """Escribe JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _report(result: dict[str, Any]) -> str:
    """Renderiza el resultado de calibración."""

    total = result["metrics"]["total"]
    lines = [
        "# Fase 95 — calibración causal de mercados", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- partidos evaluados: `{result['coverage']['evaluation_matches']}`",
        f"- decisiones: `{total['predictions']}`",
        f"- log-loss raw/calibrado: `{total['raw_log_loss']:.6f}` / "
        f"`{total['calibrated_log_loss']:.6f}`",
        f"- Brier raw/calibrado: `{total['raw_brier']:.6f}` / "
        f"`{total['calibrated_brier']:.6f}`",
        f"- ECE raw/calibrado: `{total['raw_ece']:.6f}` / "
        f"`{total['calibrated_ece']:.6f}`",
        f"- líneas recomendadas: "
        f"`{result['metrics']['calibration_recommended_markets']}`",
        "- router modificado: `False`", "- ventaja económica declarada: `False`",
    ]
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    """Ejecuta calibración expansiva, scoring y publicación."""

    source_rows = _read()
    rows = _calibrate(source_rows)
    metrics = _metrics(rows)
    total = metrics["total"]
    gate = (
        total["calibrated_log_loss"] <= total["raw_log_loss"]
        and total["calibrated_brier"] <= total["raw_brier"]
        and total["calibrated_ece"] <= total["raw_ece"])
    result = _result(rows, metrics, gate)
    _publish(result)
    return result


def _result(
    rows: list[dict[str, Any]], metrics: dict[str, Any], gate: bool,
) -> dict[str, Any]:
    """Construye el contrato de fase."""

    return {
        "classification": "validated" if gate else "rejected_for_revision",
        "config": {
            "version": "market_platt_prequential_v1",
            "warmup_matches": WARMUP,
            "bootstrap_replicates": BOOTSTRAP,
            "calibrator": "platt_logit_regularization_1_0",
        },
        "coverage": {
            "source_matches": 500, "warmup_matches": WARMUP,
            "evaluation_matches": len(rows),
            "decisions": sum(len(row["markets"]) for row in rows),
            "markets": len(rows[0]["markets"]),
        },
        "audit": {
            "strictly_prior_fit": True, "target_revealed_after_prediction": True,
            "complete_match_unit": True, "router_modified": False,
            "odds_used": False, "roi_or_clv_computed": False,
            "aggregate_gate_pass": gate,
        },
        "metrics": metrics, "predictions": rows,
    }


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos completos y hashes."""

    payloads = {
        "config.json": result["config"], "coverage.json": result["coverage"],
        "audit.json": result["audit"], "metrics.json": result["metrics"],
        "calibrated_predictions.json": result["predictions"],
        "input_manifest.json": {"phase94_sha256": _sha(SOURCE)},
    }
    for name, payload in payloads.items():
        _write(name, payload)
    report = _report(result)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    RESULT = run()
    assert RESULT["coverage"]["evaluation_matches"] == 400
    assert RESULT["coverage"]["decisions"] == 3600
    assert RESULT["audit"]["strictly_prior_fit"]
    LOGGER.info("Fase 95: %s", RESULT["classification"])


# Version: 1.0.0
# Created: 2026-07-29
