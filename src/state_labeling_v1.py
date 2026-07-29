"""Etiquetado causal y sensible de estados sobre ventanas históricas.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_02_state_labeling_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion", "unknown")


@dataclass(frozen=True, slots=True)
class StateLabelingConfig:
    """Umbrales explícitos de etiquetado causal de ventanas."""

    version: str = "state_labeling_v1"
    pressure_threshold: int = 3
    pressure_margin_threshold: int = 2
    retreat_pressure_max: int = 1
    retreat_conceded_min: int = 2
    disorganization_load_min: int = 3


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable de un valor JSON serializable."""
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON por reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _derived(row: dict[str, Any]) -> dict[str, int]:
    """Calcula variables explicativas sólo desde la ventana actual."""
    pressure = int(row["pressure"])
    conceded = int(row["pressure_conceded"])
    aggression = int(row["fouls"]) + int(row["yellow_cards"]) + 2 * int(row["red_cards"])
    return {"aggression": aggression, "defensive_load": conceded - pressure, "pressure_margin": pressure - conceded}


def label(row: dict[str, Any], config: StateLabelingConfig) -> tuple[str, dict[str, int]]:
    """Asigna un estado con prioridad fija y evidencia de la ventana actual."""
    values = _derived(row)
    if row.get("event_coverage") != "observed_timeline": return "unknown", values
    if int(row["red_cards"]) >= 1 or values["defensive_load"] >= config.disorganization_load_min: return "desorganizacion", values
    if int(row["goal_difference_start"]) >= 1 and int(row["pressure"]) <= config.retreat_pressure_max and int(row["pressure_conceded"]) >= config.retreat_conceded_min: return "repliegue", values
    if int(row["pressure"]) >= config.pressure_threshold and values["pressure_margin"] >= config.pressure_margin_threshold: return "presion", values
    return "equilibrio", values


def label_rows(rows: list[dict[str, Any]], config: StateLabelingConfig) -> list[dict[str, Any]]:
    """Etiqueta filas y conserva las variables usadas para explicación."""
    output = []
    for row in rows:
        state, values = label(row, config)
        output.append({"match_id": row["match_id"], "team_id": row["team_id"], "window_index": row["window_index"], "state": state, **values, "source_hash": row["source_hash"], "label_version": config.version})
    return output


def _distribution(labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume distribución global y por partido sin tratar ventanas como IID."""
    counts = Counter(str(row["state"]) for row in labels)
    by_match: dict[int, set[str]] = {}
    for row in labels: by_match.setdefault(int(row["match_id"]), set()).add(str(row["state"]))
    return {"counts": {state: counts[state] for state in STATES}, "rates": {state: counts[state] / len(labels) if labels else 0.0 for state in STATES}, "match_count": len(by_match), "states_per_match": {"min": min(map(len, by_match.values()), default=0), "max": max(map(len, by_match.values()), default=0)}}


def _candidate_config(base: StateLabelingConfig, offset: int) -> StateLabelingConfig:
    """Crea un candidato de sensibilidad sin alterar otros parámetros."""
    return StateLabelingConfig(pressure_threshold=max(1, base.pressure_threshold + offset), pressure_margin_threshold=max(0, base.pressure_margin_threshold + offset), retreat_pressure_max=base.retreat_pressure_max, retreat_conceded_min=base.retreat_conceded_min, disorganization_load_min=base.disorganization_load_min)


def sensitivity(rows: list[dict[str, Any]], base: StateLabelingConfig) -> dict[str, Any]:
    """Compara distribución de estados ante variaciones unitarias de umbral."""
    output: dict[str, Any] = {}
    for offset in (-1, 0, 1):
        candidate = _candidate_config(base, offset)
        labels = label_rows(rows, candidate)
        output[str(offset)] = {"config": asdict(candidate), "distribution": _distribution(labels)}
    return output


def _audit(rows: list[dict[str, Any]], labels: list[dict[str, Any]], results: dict[str, Any]) -> dict[str, Any]:
    """Verifica causalidad, cobertura y estabilidad mínima del etiquetado."""
    states = set(item["state"] for item in labels)
    base_rates = results["0"]["distribution"]["rates"]
    max_shift = max(abs(base_rates[state] - results[key]["distribution"]["rates"][state]) for key in ("-1", "1") for state in STATES)
    rare_states = [state for state, rate in base_rates.items() if state != "unknown" and rate < 0.02]
    return {"row_count_matches": len(rows) == len(labels), "unknown_count": sum(item["state"] == "unknown" for item in labels), "all_operational_states_present": {"equilibrio", "presion", "repliegue", "desorganizacion"}.issubset(states), "max_sensitivity_rate_shift": max_shift, "max_sensitivity_allowed": 0.20, "rare_states": rare_states, "causal_fields": ["pressure", "pressure_conceded", "fouls", "yellow_cards", "red_cards", "goal_difference_start", "event_coverage"], "forbidden_fields_used": []}


def _classification(audit: dict[str, Any]) -> str:
    """Decide el gate de Fase 02 sin calibrar ni promover Markov."""
    valid = audit["row_count_matches"] and not audit["unknown_count"] and audit["all_operational_states_present"] and audit["max_sensitivity_rate_shift"] <= audit["max_sensitivity_allowed"]
    return "ready_for_next_phase" if valid else "rejected_for_revision"


def run(config: StateLabelingConfig | None = None) -> dict[str, Any]:
    """Etiqueta ventanas, ejecuta sensibilidad y publica artefactos Fase 02."""
    active = config or StateLabelingConfig()
    rows = json.loads(INPUT.read_text(encoding="utf-8"))
    labels, results = label_rows(rows, active), sensitivity(rows, active)
    audit = _audit(rows, labels, results)
    result = {"config": asdict(active), "labels": labels, "distribution": _distribution(labels), "sensitivity": results, "audit": audit, "classification": _classification(audit)}
    _publish(result, _hash(rows))
    LOGGER.info("Fase 02 state_labeling: %s", result["classification"])
    return result


def _publish(result: dict[str, Any], input_hash: str) -> None:
    """Publica artefactos, reporte y hashes de la Fase 02."""
    payloads = {"config.json": result["config"], "input_manifest.json": {"source": "phase_01_event_windows_v1/event_windows.json", "input_hash": input_hash, "row_count": len(result["labels"])}, "state_labels.json": result["labels"], "coverage.json": result["distribution"], "sensitivity.json": result["sensitivity"], "audit.json": {**result["audit"], "classification": result["classification"]}}
    for name, value in payloads.items(): _write(name, value)
    report = "\n".join(["# Fase 02 — state_labeling v1", "", f"**Clasificación:** `{result['classification']}`", "", f"- filas etiquetadas: `{len(result['labels'])}`", f"- estados: `{result['distribution']['counts']}`", f"- máximo cambio de sensibilidad: `{result['audit']['max_sensitivity_rate_shift']:.4f}`", "- siguiente paso: `markov_pre_match v1`" if result["classification"] == "ready_for_next_phase" else "- siguiente paso: revisar reglas y umbrales"])
    (OUTPUT / "final_report.md").write_text(report + "\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
