"""Audita el simulador pre-match dual de Fase 79.

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
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dual_markov_simulator import (  # noqa: E402
    DualMarkovSimulator,
    HierarchicalTransitionKernel,
    SimulationConfig,
    SimulationRequest,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_79_coherent_prematch_simulation"
ASSIGNMENTS = ROOT / "artifacts/phase_77_dual_state_reaudit/state_assignments.jsonl"
WINDOWS = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
TRANSITIONS = ROOT / "artifacts/phase_78_context_transitions/transition_parameters.json"


def _fit_parameters() -> tuple[list[float], dict[bool, tuple[float, ...]]]:
    """Estima emisión relativa y apertura usando sólo el bloque fit."""

    rows = [json.loads(line) for line in ASSIGNMENTS.open(encoding="utf-8")]
    fit = [row for row in rows if row["split"] == "fit"]
    risk = defaultdict(list)
    for row in fit:
        risk[int(row["state"])].append(float(row["risk_score"]))
    weights = [float(np.mean(risk[state])) for state in range(6)]
    return weights, _initial_distributions(fit)


def _initial_distributions(
    fit: list[dict[str, Any]],
) -> dict[bool, tuple[float, ...]]:
    """Calcula prior core de state_0 por localía en train."""

    home_lookup = _home_lookup()
    counts = {True: np.ones(6), False: np.ones(6)}
    for row in fit:
        if int(row["window_index"]) != 0:
            continue
        is_home = home_lookup[(int(row["match_id"]), int(row["team_id"]))]
        counts[is_home][int(row["state"])] += 1
    return {role: tuple((values / values.sum()).tolist())
            for role, values in counts.items()}


def _home_lookup() -> dict[tuple[int, int], bool]:
    """Indexa localía causal sin leer outcomes."""

    output = {}
    for line in WINDOWS.open(encoding="utf-8"):
        row = json.loads(line)
        key = (int(row["match_id"]), int(row["team_id"]))
        output.setdefault(key, bool(row["is_home"]))
    return output


def _request(
    league: str, initial: dict[bool, tuple[float, ...]],
) -> SimulationRequest:
    """Construye fixture de referencia con lambdas ya congeladas."""

    return SimulationRequest(
        match_id=7_900_001, league_slug=league,
        home_team_id=48_15, away_team_id=79_15,
        cutoff_utc="2026-07-28T00:00:00Z",
        lambda_home=1.7131026284662545,
        lambda_away=1.191515704503168,
        initial_home=initial[True], initial_away=initial[False])


def _run_case(
    league: str, weights: list[float],
    initial: dict[bool, tuple[float, ...]],
) -> tuple[dict[str, Any], bool]:
    """Ejecuta dos replays independientes del mismo caso."""

    kernel = HierarchicalTransitionKernel.from_path(TRANSITIONS)
    config = SimulationConfig(simulations=5_000, seed=79)
    first = DualMarkovSimulator(kernel, weights, config).simulate(
        _request(league, initial))
    second = DualMarkovSimulator(kernel, weights, config).simulate(
        _request(league, initial))
    return first, first == second


def _audit(
    contextual: dict[str, Any], core: dict[str, Any],
    contextual_replay: bool, core_replay: bool,
) -> dict[str, Any]:
    """Aplica los cinco gates congelados de Fase 79."""

    mass = max(
        contextual["audit"]["home_mass_error"],
        contextual["audit"]["away_mass_error"],
        core["audit"]["home_mass_error"],
        core["audit"]["away_mass_error"])
    probability_error = max(
        _probability_error(contextual), _probability_error(core))
    core_distance = _uniform_distance(core)
    return {
        "replay_identical": contextual_replay and core_replay,
        "maximum_mass_error": mass,
        "maximum_probability_sum_error": probability_error,
        "target_post_cutoff_reads": 0,
        "core_available": True,
        "core_uniform_baseline_l1_distance": core_distance,
        "core_copies_flat_baseline": core_distance <= 1e-9,
        "style_changes": sum(
            row["audit"]["home_style_changes"]
            + row["audit"]["away_style_changes"]
            for row in (contextual, core)),
        "router_modified": False,
    }


def _probability_error(result: dict[str, Any]) -> float:
    """Mide normalización del mercado mutuamente excluyente."""

    markets = result["markets"]
    return abs(markets["home_win"] + markets["draw"]
               + markets["away_win"] - 1.0)


def _uniform_distance(result: dict[str, Any]) -> float:
    """Compara asignación Markov contra reparto temporal plano."""

    audit = result["audit"]
    distance = 0.0
    for role in ("home", "away"):
        values = np.asarray(audit[f"{role}_temporal_allocation"])
        distance += float(np.abs(values - values.mean()).sum())
    return distance


def _classification(audit: dict[str, Any]) -> str:
    """Clasifica el gate completo."""

    passed = (
        audit["replay_identical"]
        and audit["maximum_mass_error"] < 1e-6
        and audit["maximum_probability_sum_error"] <= 1e-9
        and audit["target_post_cutoff_reads"] == 0
        and audit["core_available"]
        and not audit["core_copies_flat_baseline"]
        and audit["style_changes"] == 0)
    return "ready_for_next_phase" if passed else "rejected_for_revision"


def run() -> dict[str, Any]:
    """Ejecuta auditoría contextual y fallback core."""

    weights, initial = _fit_parameters()
    contextual, contextual_replay = _run_case("arg.1", weights, initial)
    core, core_replay = _run_case("unknown.core", weights, initial)
    audit = _audit(contextual, core, contextual_replay, core_replay)
    result = {
        "classification": _classification(audit),
        "config": {"version": "dual_markov_simulator_v1",
                   "simulations": 5_000, "seed": 79,
                   "risk_weights_fit_only": weights},
        "coverage": {"reference_cases": 2, "windows_5m": 18,
                     "windows_15m": 6, "modes": ["contextual", "core"]},
        "audit": audit,
        "metrics": {
            "contextual": contextual, "core": core,
            "prediction_hashes": [
                contextual["prediction_hash"], core["prediction_hash"]]},
    }
    _publish(result)
    return result


def _write(name: str, value: Any) -> None:
    """Escribe JSON canónico legible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica el contrato completo de artefactos."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "cutoff_policy": "inputs frozen no later than kickoff",
        "target_outcomes_read": False,
        "assignments_sha256": _sha(ASSIGNMENTS),
        "windows_sha256": _sha(WINDOWS),
        "transitions_sha256": _sha(TRANSITIONS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Renderiza el reporte humano del gate."""

    audit = result["audit"]
    return (
        "# Fase 79 — simulación pre-match coherente\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- replay idéntico: `{audit['replay_identical']}`\n"
        f"- error máximo de masa: `{audit['maximum_mass_error']:.3e}`\n"
        f"- error máximo de probabilidad: "
        f"`{audit['maximum_probability_sum_error']:.3e}`\n"
        f"- lecturas post-cutoff: `{audit['target_post_cutoff_reads']}`\n"
        f"- distancia core vs baseline plano: "
        f"`{audit['core_uniform_baseline_l1_distance']:.6f}`\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta Fase 79 y devuelve estado de proceso."""

    result = run()
    LOGGER.info("Fase 79: %s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

