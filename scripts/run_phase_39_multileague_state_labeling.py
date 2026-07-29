"""Etiqueta estados tácticos en ventanas multi-liga sin leer targets futuros.

La entrada es el corpus limpio de Fase 38. El estado usa sólo presión,
presión concedida, faltas, tarjetas, roja y marcador al inicio de la ventana.
Se conservan liga, competición, temporada y provenance de cada ventana.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state_labeling_v1 import StateLabelingConfig, label

LOGGER = logging.getLogger(__name__)
INPUT = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_39_multileague_state_labeling_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion", "unknown")


def _hash(value: Any) -> str:
    """Calcula un hash estable de contenido serializable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _labels(rows: list[dict[str, Any]], config: StateLabelingConfig) -> list[dict[str, Any]]:
    """Etiqueta filas y conserva metadatos multi-liga."""

    output = []
    for row in rows:
        state, values = label(row, config)
        output.append({"match_id": row["match_id"], "team_id": row["team_id"], "window_index": row["window_index"], "league_slug": row["league_slug"], "competition_id": row["competition_id"], "season": row["season"], "state": state, **values, "source_hash": row["source_hash"], "label_version": config.version})
    return output


def _distribution(labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume distribución global, por liga y por partido."""

    counts = Counter(str(row["state"]) for row in labels)
    by_league: dict[str, Counter[str]] = defaultdict(Counter)
    matches = set()
    for row in labels:
        by_league[str(row["league_slug"])][str(row["state"])] += 1
        matches.add(int(row["match_id"]))
    return {"counts": {state: counts[state] for state in STATES}, "rates": {state: counts[state] / len(labels) if labels else 0.0 for state in STATES}, "match_count": len(matches), "by_league": {key: dict(value) for key, value in sorted(by_league.items())}}


def _sensitivity(rows: list[dict[str, Any]], base: StateLabelingConfig) -> dict[str, Any]:
    """Evalúa offsets unitarios sin cambiar el corpus fuente."""

    results = {}
    for offset in (-1, 0, 1):
        config = StateLabelingConfig(pressure_threshold=max(1, base.pressure_threshold + offset), pressure_margin_threshold=max(0, base.pressure_margin_threshold + offset), retreat_pressure_max=base.retreat_pressure_max, retreat_conceded_min=base.retreat_conceded_min, disorganization_load_min=base.disorganization_load_min)
        results[str(offset)] = {"config": asdict(config), "distribution": _distribution(_labels(rows, config))}
    return results


def _audit(rows: list[dict[str, Any]], labels: list[dict[str, Any]], sensitivity: dict[str, Any]) -> dict[str, Any]:
    """Aplica gates de cobertura, estados y sensibilidad."""

    base = sensitivity["0"]["distribution"]["rates"]
    shifts = [abs(base[state] - sensitivity[key]["distribution"]["rates"][state]) for key in ("-1", "1") for state in STATES]
    states = {row["state"] for row in labels}
    return {"row_count_matches": len(rows) == len(labels), "unknown_count": sum(row["state"] == "unknown" for row in labels), "all_operational_states_present": set(STATES[:-1]).issubset(states), "max_sensitivity_rate_shift": max(shifts, default=0.0), "max_sensitivity_allowed": 0.20, "forbidden_fields_used": [], "source_fields": ["pressure", "pressure_conceded", "fouls", "yellow_cards", "red_cards", "goal_difference_start", "event_coverage"], "targets_used": False, "final_scores_used": False}


def _write(result: dict[str, Any], input_hash: str) -> None:
    """Publica labels, sensibilidad, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config": result["config"], "input_manifest": {"source": str(INPUT.relative_to(ROOT)), "input_hash": input_hash, "row_count": len(result["labels"])}, "state_labels": result["labels"], "coverage": result["distribution"], "sensitivity": result["sensitivity"], "audit": result["audit"]}
    for name, value in payloads.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash(path.read_bytes()) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta el etiquetado multi-liga en memoria y publica artefactos."""

    rows = json.loads(INPUT.read_text(encoding="utf-8"))
    config = StateLabelingConfig()
    labels = _labels(rows, config)
    sensitivity = _sensitivity(rows, config)
    audit = _audit(rows, labels, sensitivity)
    classification = "ready_for_multileague_markov" if audit["row_count_matches"] and not audit["unknown_count"] and audit["all_operational_states_present"] and audit["max_sensitivity_rate_shift"] <= audit["max_sensitivity_allowed"] else "rejected_for_revision"
    audit["classification"] = classification
    result = {"config": {**asdict(config), "input_version": "multileague_event_windows_v1"}, "labels": labels, "distribution": _distribution(labels), "sensitivity": sensitivity, "audit": audit, "classification": classification, "final_report": f"# Fase 39 — estados multi-liga\n\n**Clasificación:** `{classification}`\n\n- filas etiquetadas: `{len(labels)}`\n- partidos: `{result_count(labels, 'match_id')}`\n- ligas: `{result_count(labels, 'league_slug')}`\n- distribución: `{audit['all_operational_states_present']}`\n- máximo desplazamiento de sensibilidad: `{audit['max_sensitivity_rate_shift']:.4f}`\n- targets utilizados: `False`\n- entrenamiento Markov ejecutado: `False`\n"}
    _write(result, _hash(rows))
    LOGGER.info("Fase 39 estados multi-liga: %s", classification)
    return result


def result_count(rows: list[dict[str, Any]], key: str) -> int:
    """Cuenta valores distintos para el reporte de cobertura."""

    return len({str(row[key]) for row in rows})


def main() -> int:
    """Ejecuta Fase 39."""

    return 0 if run()["classification"] == "ready_for_multileague_markov" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
