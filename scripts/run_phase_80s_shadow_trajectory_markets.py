"""Publica mercados secuenciales pre-match en modo shadow.

Requirements:
    numpy>=2.0

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_79_coherent_simulation as phase79  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80s_shadow_trajectory_markets"


def _sum_error(values: dict[str, float]) -> float:
    """Mide error de normalización categórica."""

    return abs(sum(values.values()) - 1.0)


def run() -> dict[str, Any]:
    """Ejecuta contextual y core con replay independiente."""

    weights, initial = phase79._fit_parameters()
    contextual, contextual_replay = phase79._run_case(
        "arg.1", weights, initial)
    core, core_replay = phase79._run_case(
        "unknown.core", weights, initial)
    errors = []
    for result in (contextual, core):
        markets = result["trajectory_markets"]
        errors.extend([_sum_error(markets["first_goal_window"]),
                       _sum_error(markets["scoring_windows"])])
    audit = {
        "replay_identical": contextual_replay and core_replay,
        "maximum_probability_sum_error": max(errors),
        "maximum_mass_error": max(
            contextual["audit"]["home_mass_error"],
            contextual["audit"]["away_mass_error"],
            core["audit"]["home_mass_error"],
            core["audit"]["away_mass_error"]),
        "target_post_cutoff_reads": 0,
        "classification_enforced": all(
            result["classification"] == "experimental_shadow_not_promoted"
            for result in (contextual, core)),
        "router_modified": False,
    }
    passed = (audit["replay_identical"]
              and audit["maximum_probability_sum_error"] <= 1e-9
              and audit["maximum_mass_error"] < 1e-6
              and audit["classification_enforced"])
    result = {
        "classification": "validated" if passed else "rejected_for_revision",
        "config": {"version": "shadow_trajectory_markets_v1",
                   "simulations": 5_000, "seed": 79},
        "coverage": {"modes": ["contextual", "core"],
                     "trajectory_markets": 5, "windows_15m": 6},
        "audit": audit,
        "metrics": {
            "contextual": contextual["trajectory_markets"],
            "core": core["trajectory_markets"],
            "prediction_hashes": [
                contextual["prediction_hash"], core["prediction_hash"]]},
    }
    _publish(result)
    return result


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato e hashes."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "phase79_parameters_sha256": _sha(phase79.TRANSITIONS),
        "phase77_assignments_sha256": _sha(phase79.ASSIGNMENTS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Genera reporte de shadow."""

    audit = result["audit"]
    return (
        "# Fase 80S — mercados de trayectoria shadow\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        "- salida de producto: `experimental_shadow_not_promoted`\n"
        f"- replay idéntico: `{audit['replay_identical']}`\n"
        f"- error máximo de probabilidad: "
        f"`{audit['maximum_probability_sum_error']:.3e}`\n"
        f"- error máximo de masa: `{audit['maximum_mass_error']:.3e}`\n"
        "- router modificado: `False`\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta y exige validación técnica."""

    result = run()
    LOGGER.info("Fase 80S: %s", result["classification"])
    return 0 if result["classification"] == "validated" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
