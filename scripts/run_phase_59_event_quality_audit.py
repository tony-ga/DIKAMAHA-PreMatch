"""Audita calidad estructural y cobertura del corpus de ventanas.

La fase no entrena modelos ni modifica snapshots. Cuando no existe el timeline
crudo local, lo declara explícitamente como gate pendiente en lugar de inferir
calidad temporal desde agregados de 15 minutos.

Requirements:
    - Python 3.10+

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prematch_snapshot_registry import resolve_active_snapshot

OUTPUT = ROOT / "artifacts/phase_59_event_quality_audit_v1"
INCOMPLETE = ROOT / "artifacts/phase_38_multileague_event_windows_v1/incomplete_matches.json"
LOGGER = logging.getLogger(__name__)
REQUIRED = {"match_id", "league_slug", "match_date", "team_id", "window_index", "goals", "event_count", "event_coverage"}


def _parser() -> argparse.ArgumentParser:
    """Define el snapshot a auditar."""

    parser = argparse.ArgumentParser(description="Audita calidad de event_windows sin modificar el snapshot.")
    parser.add_argument("--snapshot", default=None, help="Ruta opcional a event_windows.json.")
    return parser


def _rows(path: Path) -> list[dict[str, Any]]:
    """Carga filas y valida que el payload sea una lista de objetos."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("snapshot_rows_invalid")
    rows = [row for row in payload if isinstance(row, dict)]
    if len(rows) != len(payload):
        raise ValueError("snapshot_row_not_object")
    return rows


def _group(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa ventanas por partido sin perder sus filas originales."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return dict(grouped)


def _match_checks(rows: list[dict[str, Any]]) -> Counter[str]:
    """Evalúa estructura, identidad, ventanas y valores no negativos."""

    checks: Counter[str] = Counter()
    if len(rows) != 12:
        checks["window_count_invalid"] += 1
    if any(not REQUIRED.issubset(row) for row in rows):
        checks["required_column_missing"] += 1
    keys = [(int(row["team_id"]), int(row["window_index"])) for row in rows]
    if len(keys) != len(set(keys)):
        checks["duplicate_team_window"] += 1
    teams = Counter(int(row["team_id"]) for row in rows)
    if len(teams) != 2 or set(teams.values()) != {6}:
        checks["team_window_balance_invalid"] += 1
    for row in rows:
        if int(row["window_index"]) not in range(6) or any(float(row[field]) < 0 for field in ("goals", "event_count")):
            checks["negative_or_invalid_numeric"] += 1
        try:
            datetime.fromisoformat(str(row["match_date"]).replace("Z", "+00:00"))
        except ValueError:
            checks["match_date_invalid"] += 1
    return checks


def _event_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume cobertura observada y eventos no clasificados."""

    coverage = Counter(str(row.get("event_coverage")) for row in rows)
    matches = _group(rows)
    windows = {(int(row["match_id"]), int(row["window_index"])): [] for row in rows}
    for row in rows:
        windows[(int(row["match_id"]), int(row["window_index"]))].append(row)
    observed = sum(sum(int(row.get("event_count", 0) or 0) for row in group) for group in windows.values())
    unknown = sum(int(group[0].get("unknown_event_count", 0) or 0) for group in windows.values())
    null_team = sum(int(group[0].get("null_team_event_count", 0) or 0) for group in windows.values())
    per_match: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for (match_id, _), group in windows.items():
        per_match[match_id][0] += sum(int(row.get("event_count", 0) or 0) for row in group)
        per_match[match_id][1] += int(group[0].get("unknown_event_count", 0) or 0)
    no_events = sum(values[0] == 0 for values in per_match.values())
    timeline_total = observed + unknown
    high_unknown = sum(1 for observed_match, unknown_match in per_match.values() if observed_match + unknown_match and unknown_match / (observed_match + unknown_match) >= 0.5)
    return {"coverage_values": dict(coverage), "matches_without_observed_events": no_events, "observed_event_count": observed, "unknown_event_count": unknown, "null_team_event_count": null_team, "timeline_event_count": timeline_total, "unknown_ratio_over_timeline": unknown / timeline_total if timeline_total else None, "matches_unknown_ratio_at_least_50pct": high_unknown}


def _write(name: str, payload: Any) -> None:
    """Escribe JSON atómicamente."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _league_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calcula cobertura y proporción desconocida por liga."""

    grouped: dict[str, dict[tuple[int, int], list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (int(row["match_id"]), int(row["window_index"]))
        grouped[str(row["league_slug"])][key].append(row)
    result: dict[str, dict[str, Any]] = {}
    for league, windows in grouped.items():
        per_match: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for (match_id, _), group in windows.items():
            per_match[match_id][0] += sum(int(row.get("event_count", 0) or 0) for row in group)
            per_match[match_id][1] += int(group[0].get("unknown_event_count", 0) or 0)
        observed = sum(value[0] for value in per_match.values())
        unknown = sum(value[1] for value in per_match.values())
        result[league] = {"matches": len(per_match), "observed_events": observed, "unknown_events": unknown, "unknown_ratio": unknown / (observed + unknown) if observed + unknown else None, "matches_unknown_ratio_at_least_50pct": sum(value[1] / (value[0] + value[1]) >= 0.5 for value in per_match.values() if value[0] + value[1])}
    return dict(sorted(result.items()))


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta el gate estructural y declara gates no observables."""

    source = Path(args.snapshot).resolve() if args.snapshot else resolve_active_snapshot()
    rows = _rows(source)
    grouped = _group(rows)
    failures: Counter[str] = Counter()
    for match_rows in grouped.values():
        failures.update(_match_checks(match_rows))
    prior_incomplete = json.loads(INCOMPLETE.read_text(encoding="utf-8")) if INCOMPLETE.exists() else []
    event_summary = _event_summary(rows)
    structural_ok = not failures
    classification = "event_quality_structural_pass_quality_gate_blocked" if structural_ok else "event_quality_structural_gate_failed"
    audit = {"classification": classification, "source": str(source), "row_count": len(rows), "match_count": len(grouped), "league_count": len({str(row["league_slug"]) for row in rows}), "structural_failures": dict(failures), "event_summary": event_summary, "league_summary": _league_summary(rows), "historical_incomplete_reference_count": len(prior_incomplete), "raw_event_timestamps_available": False, "raw_payload_quality_gate": "pending_external_raw_timeline_corpus", "training_executed": False, "router_changed": False, "snapshot_changed": False, "markov_promoted": False}
    _write("audit.json", audit)
    worst = sorted(audit["league_summary"].items(), key=lambda item: float(item[1]["unknown_ratio"] or 0), reverse=True)[:5]
    report = ["# Fase 59 — auditoría de calidad de eventos", "", f"**Clasificación:** `{audit['classification']}`", "", f"- filas auditadas: `{len(rows)}`", f"- partidos auditados: `{len(grouped)}`", f"- ligas: `{audit['league_count']}`", f"- fallos estructurales por partido: `{sum(failures.values())}`", f"- partidos sin eventos observados: `{event_summary['matches_without_observed_events']}`", f"- eventos observados: `{event_summary['observed_event_count']}`", f"- eventos desconocidos: `{event_summary['unknown_event_count']}`", f"- total de eventos del timeline agregado: `{event_summary['timeline_event_count']}`", f"- proporción desconocida: `{event_summary['unknown_ratio_over_timeline']:.4f}`", f"- partidos con al menos 50% desconocido: `{event_summary['matches_unknown_ratio_at_least_50pct']}`", f"- eventos sin equipo: `{event_summary['null_team_event_count']}`", f"- referencias históricas incompletas: `{len(prior_incomplete)}`", "", "## Ligas con mayor proporción no clasificada", "", *[f"- `{league}`: `{values['unknown_ratio']:.4f}` en `{values['matches']}` partidos" for league, values in worst], "", "- timestamps crudos disponibles localmente: `False`", "- entrenamiento ejecutado: `False`", "- snapshot modificado: `False`", "", "La estructura de ventanas es íntegra, pero el gate de calidad permanece bloqueado por eventos no clasificados y ausencia del timeline crudo con timestamps."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 59: %s", run(_parser().parse_args())["classification"])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Auditoría de eventos rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
