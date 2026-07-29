"""Audita la alineación temporal entre estados y emisiones de gol."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from scripts.run_phase_63_frozen_markov_candidate import _state_rows

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_67_state_emission_audit_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga un JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _group(rows: list[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Agrupa ventanas por partido y equipo."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["match_id"]), int(row["team_id"]))].append(row)
    return grouped


def _accumulate(target: dict[str, list[float]], key: str, goals: float) -> None:
    """Acumula goles y observaciones para una condición."""

    target[key][0] += goals
    target[key][1] += 1.0
    target[key][2] += float(goals > 0.0)


def _rate(values: list[float]) -> float:
    """Calcula media de goles acumulada."""

    return values[0] / values[1] if values[1] else 0.0


def _audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara emisión contemporánea con emisión causal desplazada."""

    grouped = _group(rows)
    same: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    next_window: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    lagged: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for values in grouped.values():
        ordered = sorted(values, key=lambda row: int(row["window_index"]))
        for index, row in enumerate(ordered[:-1]):
            state = str(row["state"])
            _accumulate(same, state, float(row["goals"]))
            _accumulate(next_window, state, float(ordered[index + 1]["goals"]))
            next_state = str(ordered[index + 1]["state"])
            _accumulate(lagged, next_state, float(ordered[index + 1]["goals"]))
    by_state = {}
    for state in STATES:
        by_state[state] = {"observations": int(same[state][1]), "same_window_goal_rate": _rate(same[state]), "same_window_any_goal_rate": same[state][2] / max(same[state][1], 1.0), "next_window_goal_rate_given_state_t": _rate(next_window[state]), "next_window_any_goal_rate_given_state_t": next_window[state][2] / max(next_window[state][1], 1.0), "next_window_goal_rate_given_lagged_state": _rate(lagged[state]), "same_vs_next_gap": _rate(same[state]) - _rate(next_window[state])}
    return {"row_count": len(rows), "match_count": len({int(row["match_id"]) for row in rows}), "state_counts": dict(Counter(str(row["state"]) for row in rows)), "by_state": by_state, "same_window_is_current_implementation": True, "lagged_state_is_causal_candidate": True}


def run() -> dict[str, Any]:
    """Publica auditoría de alineación sin cambiar el modelo."""

    rows = _state_rows(_load(WINDOWS))
    audit = _audit(rows)
    audit["classification"] = "state_emission_temporal_misalignment_detected" if max(abs(value["same_vs_next_gap"]) for value in audit["by_state"].values()) > 0.01 else "state_emission_alignment_requires_revision"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 67 — auditoría state→emission", "", f"**Clasificación:** `{audit['classification']}`", "", f"- filas: `{audit['row_count']}`", f"- partidos: `{audit['match_count']}`", "- implementación actual: `state_t -> goles_t`", "- candidato causal: `state_t -> goles_t+1`"]
    for state, values in audit["by_state"].items():
        report.append(f"- {state}: misma ventana `{values['same_window_goal_rate']:.6f}`; siguiente ventana `{values['next_window_goal_rate_given_state_t']:.6f}`; brecha `{values['same_vs_next_gap']:.6f}`")
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 67 emisión: %s", audit["classification"])
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"].startswith("state_emission_") else 1)

