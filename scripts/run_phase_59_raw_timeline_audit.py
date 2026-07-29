"""Audita timelines crudos ESPN contra ventanas agregadas.

La cohorte es representativa y acotada. Los payloads crudos se usan sólo en
memoria/caché controlada; los artefactos publicados contienen hashes, conteos y
tipos sanitizados, nunca respuestas completas.

Requirements:
    - requests
    - tenacity

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_phase_7_15_r3 import _normalize
from src.espn_event_taxonomy import AUXILIARY_EVENT
from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector
from src.prematch_snapshot_registry import resolve_active_snapshot

OUTPUT = ROOT / "artifacts/phase_59_raw_timeline_audit_v1"
LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Define el tamaño de la cohorte raw."""

    parser = argparse.ArgumentParser(description="Audita timelines ESPN raw contra event_windows.")
    parser.add_argument("--max-matches", type=int, default=30)
    parser.add_argument("--snapshot", default=None)
    return parser


def _snapshot_rows(path: Path) -> list[dict[str, Any]]:
    """Carga filas agregadas del snapshot activo."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("snapshot_rows_invalid")
    return [row for row in payload if isinstance(row, dict)]


def _match_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construye un registro por partido con su ratio desconocido agregado."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    records = []
    for match_id, match_rows in grouped.items():
        windows = {(int(row["window_index"])): [] for row in match_rows}
        for row in match_rows:
            windows[int(row["window_index"])].append(row)
        observed = sum(sum(int(row.get("event_count", 0) or 0) for row in group) for group in windows.values())
        unknown = sum(int(group[0].get("unknown_event_count", 0) or 0) for group in windows.values())
        first = match_rows[0]
        records.append({"match_id": match_id, "league_slug": str(first["league_slug"]), "competition_id": str(first["competition_id"]), "match_date": str(first["match_date"]), "unknown_ratio": unknown / (observed + unknown) if observed + unknown else 0.0, "rows": match_rows})
    return records


def _select(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Selecciona una cohorte estratificada por riesgo de desconocidos."""

    if limit < 1:
        raise ValueError("max_matches_must_be_positive")
    records = _match_records(rows)
    records.sort(key=lambda item: (-float(item["unknown_ratio"]), item["league_slug"], item["match_id"]))
    high = records[: max(1, limit // 2)]
    stride = max(1, len(records) // max(1, limit - len(high)))
    spread = records[::stride]
    selected = {int(item["match_id"]): item for item in high}
    for item in spread:
        if len(selected) >= limit:
            break
        selected[int(item["match_id"])] = item
    return [selected[key] for key in sorted(selected)][:limit]


def _reference(item: dict[str, Any]) -> dict[str, str]:
    """Convierte un registro local en referencia ESPN."""

    return {"provider_match_id": str(item["match_id"]), "competition_id": str(item["competition_id"]), "league_slug": str(item["league_slug"])}


def _event_audit(batch: dict[str, Any]) -> dict[str, Any]:
    """Resume timestamps, tipos, equipos y hashes de eventos normalizados."""

    events = batch.get("events", [])
    raw_types = Counter(str(event.get("event_type_raw") or "missing") for event in events)
    normalized = Counter(str(event.get("event_type") or "missing") for event in events)
    timestamps = [event.get("event_ts") for event in events]
    parsed = [datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in timestamps if value]
    hashes = [str(event.get("event_hash")) for event in events if event.get("event_hash")]
    return {"raw_event_count": len(events), "raw_type_distribution": dict(raw_types), "normalized_type_distribution": dict(normalized), "unclassified_count": normalized.get("unclassified", 0), "missing_team_count": sum(event.get("team_provider_id") is None for event in events), "missing_timestamp_count": len(events) - len(parsed), "timestamps_utc": all(value.tzinfo and value.utcoffset() is not None for value in parsed), "timestamps_not_future": all(value <= datetime.now(timezone.utc) for value in parsed), "timestamp_order_ok": parsed == sorted(parsed), "duplicate_event_hashes": len(hashes) - len(set(hashes)), "payload_hash_count": len(batch.get("raw_payloads", [])), "rejected_count": len(batch.get("rejected", []))}


def _window_audit(item: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """Reconcila goles agregados con el marcador normalizado."""

    home_id = int(identity["home_provider_team_id"]); away_id = int(identity["away_provider_team_id"])
    home = sum(int(row.get("goals", 0) or 0) for row in item["rows"] if int(row["team_id"]) == home_id)
    away = sum(int(row.get("goals", 0) or 0) for row in item["rows"] if int(row["team_id"]) == away_id)
    return {"window_home_goals": home, "window_away_goals": away, "raw_home_score": identity.get("home_score"), "raw_away_score": identity.get("away_score"), "score_reconciled": home == identity.get("home_score") and away == identity.get("away_score")}


def _fetch(item: dict[str, Any]) -> dict[str, Any]:
    """Descarga y audita un partido manteniendo el raw fuera del artefacto."""

    league = str(item["league_slug"])
    connector = EspnProspectiveConnector(EspnConnectorConfig(league=league, cache_dir=OUTPUT / "cache" / league, cache_ttl_seconds=86400))
    try:
        batch, _ = _normalize(connector, _reference(item))
        return {"match_id": item["match_id"], "league_slug": league, "match_date": item["match_date"], "unknown_ratio_windows": item["unknown_ratio"], "fetch_ok": True, "identity": {key: batch["identity"].get(key) for key in ("provider_match_id", "home_provider_team_id", "away_provider_team_id", "home_score", "away_score", "provider_status", "complete", "kickoff_ts")}, "event_audit": _event_audit(batch), "window_audit": _window_audit(item, batch["identity"])}
    except (OSError, ValueError, RuntimeError) as error:
        return {"match_id": item["match_id"], "league_slug": league, "match_date": item["match_date"], "unknown_ratio_windows": item["unknown_ratio"], "fetch_ok": False, "error": str(error)[:200]}


def _write(name: str, payload: Any) -> None:
    """Escribe artefactos sanitizados de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta la auditoría raw representativa."""

    source = Path(args.snapshot).resolve() if args.snapshot else resolve_active_snapshot()
    selected = _select(_snapshot_rows(source), args.max_matches)
    results = [_fetch(item) for item in selected]
    valid = [item for item in results if item["fetch_ok"]]
    audit = {"classification": "raw_timeline_audit_complete_with_quality_findings" if valid else "raw_timeline_audit_no_valid_matches", "source_snapshot": str(source), "selected_matches": len(selected), "valid_matches": len(valid), "failed_matches": len(selected) - len(valid), "results": results, "raw_payloads_published": False, "training_executed": False, "snapshot_changed": False, "router_changed": False, "markov_promoted": False}
    raw_events = sum(item.get("event_audit", {}).get("raw_event_count", 0) for item in valid)
    unknown = sum(item.get("event_audit", {}).get("unclassified_count", 0) for item in valid)
    auxiliary = sum(item.get("event_audit", {}).get("normalized_type_distribution", {}).get(AUXILIARY_EVENT, 0) for item in valid)
    reconciled = sum(item.get("window_audit", {}).get("score_reconciled", False) for item in valid)
    if valid and unknown == 0:
        audit["classification"] = "raw_timeline_audit_taxonomy_validated_rebuild_pending"
    audit.update({"taxonomy_version": "espn_event_taxonomy_v1.1", "raw_event_count": raw_events, "unclassified_count": unknown, "auxiliary_event_count": auxiliary, "score_reconciled_matches": reconciled})
    _write("selected_matches.json", [{key: item[key] for key in ("match_id", "league_slug", "competition_id", "match_date", "unknown_ratio")} for item in selected])
    _write("audit.json", audit)
    report = ["# Fase 59 — auditoría raw ESPN", "", f"**Clasificación:** `{audit['classification']}`", "", "- taxonomía: `espn_event_taxonomy_v1.1`", f"- partidos seleccionados: `{len(selected)}`", f"- partidos raw válidos: `{len(valid)}`", f"- eventos raw normalizados: `{raw_events}`", f"- eventos auxiliares: `{auxiliary}`", f"- eventos `unclassified`: `{unknown}`", f"- marcadores reconciliados con ventanas: `{reconciled}/{len(valid)}`", "- payloads raw publicados: `False`", "- entrenamiento ejecutado: `False`", "- snapshot modificado: `False`", "", "La taxonomía queda validada en la cohorte raw; falta rematerializar el snapshot aislado y repetir el gate global antes de entrenar Markov."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Auditoría raw: %s", run(_parser().parse_args())["classification"])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Auditoría raw rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
