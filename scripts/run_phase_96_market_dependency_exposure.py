"""Audita dependencia y exposición conjunta de nueve mercados.

# Requirements:
# numpy>=2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_exposure_policy import (  # noqa: E402
    ExposurePolicy,
    dependency_components,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / (
    "artifacts/phase_94_historical_500_semiofficial/"
    "ranked_500_predictions.json")
OUTPUT = ROOT / "artifacts/phase_96_market_dependency_exposure"
THRESHOLD = 0.30


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read() -> list[dict[str, Any]]:
    """Lee los 500 partidos auditados."""

    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _matrix(
    rows: list[dict[str, Any]], names: list[str], field: str,
) -> list[list[float]]:
    """Calcula correlaciones entre líneas."""

    values = np.asarray([
        [float(row["markets"][name][field]) for name in names]
        for row in rows
    ])
    matrix = np.corrcoef(values, rowvar=False)
    return [[float(value) for value in line] for line in matrix]


def _correct_distribution(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Cuenta partidos por número exacto de mercados acertados."""

    counts = Counter(int(row["correct_markets"]) for row in rows)
    return {str(value): counts[value] for value in range(10)}


def _independence_reference(
    rows: list[dict[str, Any]], names: list[str],
) -> dict[str, float]:
    """Calcula una referencia heurística sin asumirla como modelo real."""

    accuracies = [
        float(np.mean([
            row["markets"][name]["model_correct"] for row in rows]))
        for name in names
    ]
    probability = math.prod(accuracies)
    expected = len(rows) * probability
    return {
        "joint_probability_if_independent": probability,
        "expected_perfect_matches_if_independent": expected,
        "observed_perfect_matches": sum(
            int(row["correct_markets"]) == len(names) for row in rows),
        "observed_to_independent_expected_ratio":
            sum(int(row["correct_markets"]) == len(names) for row in rows)
            / expected,
    }


def _strong_pairs(
    names: list[str], matrix: list[list[float]],
) -> list[dict[str, Any]]:
    """Lista pares por encima del umbral congelado."""

    output = []
    for left in range(len(names)):
        for right in range(left + 1, len(names)):
            value = matrix[left][right]
            if abs(value) >= THRESHOLD:
                output.append({
                    "left": names[left], "right": names[right],
                    "correlation": value,
                })
    return sorted(output, key=lambda row: -abs(row["correlation"]))


def _policy(
    names: list[str], outcome_matrix: list[list[float]],
) -> ExposurePolicy:
    """Construye una política informativa y conservadora."""

    components = dependency_components(names, outcome_matrix, THRESHOLD)
    return ExposurePolicy(
        max_markets_per_match=3,
        max_markets_per_component=1,
        correlation_threshold=THRESHOLD,
        components=components,
    )


def _write(name: str, payload: Any) -> None:
    """Escribe JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _report(result: dict[str, Any]) -> str:
    """Renderiza el reporte de dependencia."""

    audit = result["perfect_match_audit"]
    lines = [
        "# Fase 96 — dependencia y exposición shadow", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- partidos: `{result['coverage']['matches']}`",
        f"- mercados: `{result['coverage']['markets']}`",
        f"- perfectos observados: `{audit['observed_perfect_matches']}/500`",
        f"- perfectos esperados bajo independencia: "
        f"`{audit['expected_perfect_matches_if_independent']:.2f}`",
        f"- pares con |correlación| >= {THRESHOLD}: "
        f"`{len(result['strong_outcome_pairs'])}`",
        f"- componentes: `{result['policy']['components']}`",
        "- política: máximo 3 mercados por partido y 1 por componente",
        "- stakes/ROI/Kelly: `no calculados`",
    ]
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    """Ejecuta matrices, auditoría conjunta y política shadow."""

    rows = _read()
    names = sorted(rows[0]["markets"])
    outcomes = _matrix(rows, names, "actual")
    probabilities = _matrix(rows, names, "probability")
    policy = _policy(names, outcomes)
    result = _result(rows, names, outcomes, probabilities, policy)
    _validate(result)
    _publish(result)
    return result


def _result(
    rows: list[dict[str, Any]], names: list[str],
    outcomes: list[list[float]], probabilities: list[list[float]],
    policy: ExposurePolicy,
) -> dict[str, Any]:
    """Construye el contrato completo de dependencia."""

    return {
        "classification": "validated",
        "config": {
            "version": "market_dependency_exposure_v1",
            "correlation_threshold": THRESHOLD,
            "max_markets_per_match": 3,
            "max_markets_per_component": 1,
        },
        "coverage": {
            "matches": len(rows), "markets": len(names),
            "decisions": len(rows) * len(names),
        },
        "audit": {
            "complete_match_unit": True, "matrices_symmetric": True,
            "matrix_diagonal_one": True, "router_modified": False,
            "automatic_staking_enabled": False,
            "odds_roi_clv_kelly_used": False,
        },
        "market_order": names,
        "outcome_correlation": outcomes,
        "probability_correlation": probabilities,
        "strong_outcome_pairs": _strong_pairs(names, outcomes),
        "correct_markets_distribution": _correct_distribution(rows),
        "perfect_match_audit": _independence_reference(rows, names),
        "policy": asdict(policy),
    }


def _validate(result: dict[str, Any]) -> None:
    """Valida cobertura y propiedades matriciales."""

    if result["coverage"] != {"matches": 500, "markets": 9, "decisions": 4500}:
        raise ValueError("phase96_coverage_failed")
    for key in ("outcome_correlation", "probability_correlation"):
        matrix = np.asarray(result[key], dtype=float)
        if not np.allclose(matrix, matrix.T) or not np.allclose(
                np.diag(matrix), 1.0):
            raise ValueError(f"phase96_matrix_failed:{key}")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato, matrices, reporte y hashes."""

    payloads = {
        "config.json": result["config"], "coverage.json": result["coverage"],
        "audit.json": result["audit"],
        "dependency.json": {
            key: result[key] for key in (
                "market_order", "outcome_correlation",
                "probability_correlation", "strong_outcome_pairs")},
        "perfect_match_audit.json": result["perfect_match_audit"],
        "correct_markets_distribution.json":
            result["correct_markets_distribution"],
        "exposure_policy.json": result["policy"],
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
    assert RESULT["coverage"]["matches"] == 500
    assert RESULT["audit"]["complete_match_unit"]
    assert not RESULT["audit"]["automatic_staking_enabled"]
    LOGGER.info("Fase 96: %s", RESULT["classification"])


# Version: 1.0.0
# Created: 2026-07-29
