"""Audita una taxonomía v2 rica en amenaza ofensiva."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.run_phase_63_frozen_markov_candidate import _state_rows
from scripts.run_phase_65_markov_position_audit import _group
from src.state_labeling_v2_candidate import STATES, enrich, fit, label

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_70_state_labeling_v2_candidate_v1"
LOGGER = logging.getLogger(__name__)


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _development_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Selecciona desarrollo por partido completo."""

    grouped = _group(rows)
    ordered = sorted((str(values[0]["match_date"]), match_id) for match_id, values in grouped.items())
    return {match_id for _, match_id in ordered[: int(len(ordered) * 0.60)]}


def _label_rows(rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Etiqueta cada ventana con la taxonomía candidata."""

    output = []
    for row in rows:
        state, values = label(row, thresholds)
        output.append({**row, "state_v2": state, **{f"v2_{key}": value for key, value in values.items()}})
    return output


def _signal(rows: list[dict[str, Any]], state_key: str) -> dict[str, Any]:
    """Mide relación estado actual con goles de la siguiente ventana."""

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["match_id"]), int(row["team_id"]))].append(row)
    stats = defaultdict(lambda: [0.0, 0.0])
    for values in grouped.values():
        ordered = sorted(values, key=lambda row: int(row["window_index"]))
        for current, following in zip(ordered, ordered[1:]):
            key = str(current[state_key])
            stats[key][0] += float(following["goals"])
            stats[key][1] += 1.0
    rates = {state: stats[state][0] / stats[state][1] if stats[state][1] else None for state in STATES}
    observed = [value for value in rates.values() if value is not None]
    return {"rates_next_window": rates, "spread": max(observed) - min(observed) if observed else 0.0, "support": {state: int(stats[state][1]) for state in STATES}}


def run() -> dict[str, Any]:
    """Genera auditoría comparativa v1 contra v2 sin modificar contratos."""

    raw = enrich(_load(WINDOWS))
    development = _development_ids(raw)
    thresholds = fit([row for row in raw if int(row["match_id"]) in development])
    rows = _label_rows(raw, thresholds)
    v1_rows = _state_rows(raw)
    v1 = [{**row, "state_v1": label} for row, label in zip(raw, [item["state"] for item in v1_rows])]
    v2_counts = Counter(str(row["state_v2"]) for row in rows)
    v1_counts = Counter(str(row["state_v1"]) for row in v1)
    audit = {"classification": "state_labeling_v2_candidate_audited", "rows": len(rows), "matches": len({int(row["match_id"]) for row in rows}), "development_matches": len(development), "thresholds": thresholds, "v1_distribution": dict(v1_counts), "v2_distribution": dict(v2_counts), "v1_signal": _signal(v1, "state_v1"), "v2_signal": _signal(rows, "state_v2"), "router_modified": False, "markov_promoted": False}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    (OUTPUT / "labels.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 70 — candidato state_labeling v2", "", f"**Clasificación:** `{audit['classification']}`", "", f"- partidos: `{audit['matches']}`", f"- desarrollo: `{audit['development_matches']}`", f"- distribución v1: `{audit['v1_distribution']}`", f"- distribución v2: `{audit['v2_distribution']}`", f"- spread siguiente ventana v1: `{audit['v1_signal']['spread']}`", f"- spread siguiente ventana v2: `{audit['v2_signal']['spread']}`", "- router modificado: `False`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Fase 70 state v2: %s", audit["classification"])
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"] == "state_labeling_v2_candidate_audited" else 1)

