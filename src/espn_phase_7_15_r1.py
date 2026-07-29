"""Fase 7.15-R1: validación ESPN sin persistencia ni evaluación.

Version: 1.0.0
Created: 2026-07-16
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, extract_event_id, payload_hash, scoreboard_references
from src.prospective_ingestion_v2 import SourceReference, build_batch, utc_now

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_15_espn_connector_r1"
KNOWN_DATE = os.getenv("DIKAMAHA_ESPN_R1_KNOWN_DATE", "20251026")
LOGGER = logging.getLogger(__name__)


def _hash(value: Any) -> str:
    """Calcula hash estable de metadatos, nunca de secretos."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _status_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Cuenta estados ESPN sin conservar respuestas completas."""

    counts = Counter()
    for event in events:
        competitions = event.get("competitions") if isinstance(event, dict) else []
        status_obj = competitions[0].get("status") if competitions and isinstance(competitions[0], dict) else {}
        status_type = status_obj.get("type") if isinstance(status_obj, dict) else {}
        normalized = str((status_type or {}).get("state") or (status_type or {}).get("name") or "unknown").lower()
        bucket = "completed" if normalized in {"post", "final", "finished", "completed"} else "in_progress" if normalized in {"in", "in_progress", "live"} else "scheduled" if normalized in {"scheduled", "pre"} else "unknown"
        counts[bucket] += 1
    return {key: int(counts[key]) for key in ("scheduled", "in_progress", "completed", "unknown")}


def _scoreboard_probe(connector: EspnProspectiveConnector, date: str) -> dict[str, Any]:
    """Prueba una fecha y clasifica vacío contra schema inesperado."""

    try:
        payload = connector.scoreboard(date)
        events = payload.get("events")
        if not isinstance(events, list):
            return {"date": date, "status": "source_schema_unexpected", "events_count": None, "references_count": None, "payload_hash": payload_hash(payload)}
        refs = scoreboard_references(payload)
        return {"date": date, "status": "event_ids_found" if refs else "source_empty", "events_count": len(events), "references_count": len(refs), "status_counts": _status_counts(events), "payload_hash": payload_hash(payload), "schema_keys": sorted(str(key) for key in payload)[:20]}
    except (OSError, ValueError, EspnConnectorError) as error:
        return {"date": date, "status": "espn_connector_rejected_for_revision", "error": str(error)[:160]}


def _event_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Audita extracción desde id y referencia ESPN sin payload completo."""

    events = payload.get("events", []) if isinstance(payload, dict) else []
    rows = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        rows.append({"event_id": extract_event_id(event), "has_direct_id": str(event.get("id", "")).isdigit(), "has_reference": isinstance(event.get("$ref") or event.get("ref"), str)})
    return {"input_events": len(events) if isinstance(events, list) else None, "extracted": sum(row["event_id"] is not None for row in rows), "rows": rows[:20]}


def _summary_probe(connector: EspnProspectiveConnector, reference: dict[str, Any]) -> dict[str, Any]:
    """Confirma el endpoint documentado summary?event sin registrar el contenido."""

    try:
        payload = connector.summary(reference["provider_match_id"])
        keys = sorted(str(key) for key in payload) if isinstance(payload, dict) else []
        return {"event_id": reference["provider_match_id"], "status": "summary_functional" if isinstance(payload, dict) and keys else "source_schema_unexpected", "payload_hash": payload_hash(payload), "top_level_keys": keys[:30], "plays_count": len(payload.get("plays", [])) if isinstance(payload, dict) and isinstance(payload.get("plays"), list) else None}
    except (OSError, ValueError, EspnConnectorError) as error:
        return {"event_id": reference["provider_match_id"], "status": "espn_connector_rejected_for_revision", "error": str(error)[:160]}


def _normalization_probe(connector: EspnProspectiveConnector, reference: dict[str, Any]) -> dict[str, Any]:
    """Prueba normalización en memoria, sin llamar al repositorio staging."""

    try:
        event, plays = connector.event(reference["provider_match_id"]), connector.plays(reference["provider_match_id"], reference["competition_id"])
        batch = build_batch(SourceReference(**reference), event, plays, utc_now())
        return {"status": "normalizable", "match_id": reference["provider_match_id"], "competition_id": reference["competition_id"], "valid_events": len(batch["events"]), "rejected_events": len(batch["rejected"]), "rejection_reasons": dict(Counter(row["reason"] for row in batch["rejected"])), "raw_payload_count": len(batch["raw_payloads"]), "event_audit": batch["event_audit"]}
    except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
        return {"status": "source_schema_unexpected", "match_id": reference["provider_match_id"], "competition_id": reference["competition_id"], "error": str(error)[:160]}


def _cache_manifest(config: EspnConnectorConfig) -> dict[str, Any]:
    """Describe caché local por hashes y metadatos, sin payloads completos."""

    files = []
    if config.cache_dir.exists():
        for path in sorted(config.cache_dir.glob("*.json")):
            files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"cache_dir_configured": True, "payloads_exposed": False, "ttl_seconds": config.cache_ttl_seconds, "files": files, "sanitized_metadata_only": True}


def _run() -> dict[str, Any]:
    """Ejecuta probes de fecha, IDs, summary y normalización en memoria."""

    league = os.getenv("DIKAMAHA_ESPN_LEAGUE", "esp.1")
    cache_dir = OUTPUT / "cache"
    config = EspnConnectorConfig(league=league, cache_dir=cache_dir)
    connector = EspnProspectiveConnector(config)
    dates = ["20260714", "20260716", "20260717", KNOWN_DATE]
    probes = [_scoreboard_probe(connector, date) for date in dates]
    known_payload = connector.scoreboard(KNOWN_DATE)
    refs = scoreboard_references(known_payload)
    reference = refs[0] if refs else None
    summary = _summary_probe(connector, reference) if reference else {"status": "source_empty", "event_id": None}
    normalization = _normalization_probe(connector, reference) if reference else {"status": "source_empty", "valid_events": 0, "rejected_events": 0}
    diagnosis = {"configured_league": league, "known_date": KNOWN_DATE, "known_date_references": len(refs), "event_ids_found": bool(refs), "summary_functional": summary["status"] == "summary_functional", "payloads_normalizable": normalization["status"] == "normalizable", "timezone": "scoreboard dates=YYYYMMDD; event timestamps normalized UTC", "pagination": {"scoreboard": "not paginated in documented endpoint", "plays_limit": 300}, "status_semantics": ["scheduled", "in_progress", "completed"], "staging_write_enabled": False, "postgresql_modified": False, "markov_modified": False, "hawkes_official": False}
    classification = "espn_source_ready" if all((diagnosis["event_ids_found"], diagnosis["summary_functional"], diagnosis["payloads_normalizable"])) else "espn_source_ready_with_caveats" if diagnosis["event_ids_found"] else "espn_source_empty"
    return {"config": {"league": league, "competition_selection": "scoreboard event.competitions[0].id", "known_date": KNOWN_DATE, "write_enabled": False}, "diagnosis": diagnosis, "probes": probes, "references": refs[:20], "summary": summary, "normalization": normalization, "cache": _cache_manifest(config), "classification": classification}


def _write_result(result: dict[str, Any]) -> None:
    """Escribe el contrato completo R1 y hashes reproducibles."""

    _write("source_diagnosis.json", result["diagnosis"])
    _write("competition_config_sanitized.json", result["config"])
    _write("endpoint_diagnostics.json", {"scoreboard": "site.api.espn.com/.../sports/soccer/{league}/scoreboard?dates=YYYYMMDD", "calendar": "sports.core.api.espn.com/.../leagues/{league}/calendar", "summary": "site.api.espn.com/.../sports/soccer/{league}/summary?event={id}", "plays": "sports.core.api.espn.com/.../events/{event}/competitions/{competition}/plays?limit=300", "known_date_probes": result["probes"]})
    _write("known_date_probe.json", {"dates": result["probes"], "known_date": result["config"]["known_date"]})
    _write("event_id_extraction.json", {"references": result["references"], "event_ids_found": len(result["references"]) > 0, "location": "scoreboard.events[].id", "fallback_location": "scoreboard.events[].$ref|ref /events/{id}"})
    _write("summary_probe.json", result["summary"])
    _write("cache_manifest.json", result["cache"])
    audit = {"dry_run": True, "postgresql_modified": False, "staging_write_enabled": False, "event_ids_found": result["diagnosis"]["event_ids_found"], "payloads_normalizable": result["diagnosis"]["payloads_normalizable"], "summary_functional": result["diagnosis"]["summary_functional"], "no_markov_change": True, "no_hawkes_activation": True, "no_alpha_beta_calibration": True, "replay_inputs_frozen": True}
    _write("audit.json", audit)
    manifest = {"phase": "7.15-R1", "version": "phase_7_15_r1_v1", "classification": result["classification"], "dry_run": True, "event_ids_found": result["diagnosis"]["event_ids_found"], "payloads_normalizable": result["diagnosis"]["payloads_normalizable"], "summary_functional": result["diagnosis"]["summary_functional"], "postgresql_modified": False, "staging_write_permitted": False}
    _write("manifest.json", manifest)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    report = ["# Fase 7.15-R1 - Validación ESPN", "", f"**Clasificación:** `{result['classification']}`", "", f"- competición: `{result['config']['league']}`", f"- fecha conocida: `{result['config']['known_date']}`", f"- event_ids_found: `{result['diagnosis']['event_ids_found']}`", f"- summary funcional: `{result['diagnosis']['summary_functional']}`", f"- payload normalizable: `{result['diagnosis']['payloads_normalizable']}`", "- ejecución dry-run; no se habilitó escritura staging.", "- Markov, Hawkes y alpha/beta permanecen sin cambios."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> int:
    """Ejecuta R1 y nunca persiste en PostgreSQL."""

    try:
        result = _run()
    except (OSError, ValueError, EspnConnectorError) as error:
        result = {"classification": "espn_connector_rejected_for_revision", "diagnosis": {"error": str(error)[:160], "event_ids_found": False, "summary_functional": False, "payloads_normalizable": False}, "config": {"write_enabled": False}, "probes": [], "references": [], "summary": {}, "normalization": {}, "cache": {}}
    _write_result(result)
    LOGGER.info("Fase 7.15-R1: %s", result["classification"])
    return 1 if result["classification"] == "espn_connector_rejected_for_revision" else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
