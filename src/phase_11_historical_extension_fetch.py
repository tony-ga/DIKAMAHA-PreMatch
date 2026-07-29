"""Obtención auditable de una extensión histórica ESPN sin escrituras DB.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.espn_phase_7_15_r3 import _normalize
from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, scoreboard_references

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_11_historical_extension_fetch"
CANONICAL_WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
PHASE09_TARGETS = ROOT / "artifacts/phase_09_historical_target_revision/target_labels.json"


@dataclass(frozen=True, slots=True)
class Phase11Config:
    """Parámetros congelados para la captura de extensión histórica."""

    version: str = "historical_extension_fetch_v1"
    league: str = "esp.1"
    start_date: str = "20251201"
    end_date: str = "20260524"
    sleep_between_requests: float = 0.05
    cache_ttl_seconds: int = 86400
    output_dir: str = "artifacts/phase_11_historical_extension_fetch"
    phase_label: str = "Fase 11"


def _hash(value: Any) -> str:
    """Calcula un hash SHA-256 estable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula el hash de un archivo de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dates(start: str, end: str) -> list[str]:
    """Genera fechas inclusivas validando formato y orden."""

    first, last = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:] }"), date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    if first > last:
        raise ValueError("historical_extension_start_after_end")
    return [(first + timedelta(days=offset)).strftime("%Y%m%d") for offset in range((last - first).days + 1)]


def _excluded_ids() -> set[str]:
    """Carga IDs de cohortes ya utilizadas para impedir solapamiento."""

    canonical = {str(int(row["match_id"])) for row in json.loads(CANONICAL_WINDOWS.read_text(encoding="utf-8"))}
    targets = {str(int(row["match_id"])) for row in json.loads(PHASE09_TARGETS.read_text(encoding="utf-8"))}
    return canonical | targets | {"704766"}


def _public_batch(batch: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Elimina raw_data de identidad y eventos antes de publicar."""

    identity = {key: value for key, value in batch["identity"].items() if key != "raw_data"}
    events = [{key: value for key, value in row.items() if key != "raw_data"} for row in batch["events"]]
    return {"identity": identity, "event_audit": batch["event_audit"], "rejected": [{key: value for key, value in row.items() if key != "raw_data"} for row in batch["rejected"]]}, events


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON ordenado mediante reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _write_artifacts(result: dict[str, Any], output: Path) -> None:
    """Publica artefactos de extensión y hashes reproducibles."""

    payloads = {key: result[key] for key in ("config", "input_manifest", "coverage", "date_results", "candidate_matches", "candidate_events", "rejected_records", "audit")}
    for name, value in payloads.items():
        target = output / f"{name}.json"
        target.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report = "\n".join([f"# {result['config']['phase_label']} — extensión histórica ESPN", "", f"**Clasificación:** `{result['classification']}`", "", f"- fechas consultadas: `{len(result['date_results'])}`", f"- candidatos completos: `{result['coverage']['candidate_match_count']}`", f"- eventos candidatos: `{result['coverage']['candidate_event_count']}`", "- PostgreSQL escrito: `False`", "- siguiente paso: construir ventanas y repetir evaluación temporal v2."])
    (output / "final_report.md").write_text(report + "\n", encoding="utf-8")
    hashes = {path.name: _file_hash(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (output / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Phase11Config | None = None) -> dict[str, Any]:
    """Consulta ESPN, normaliza candidatos y no escribe PostgreSQL."""

    active = config or Phase11Config()
    output = ROOT / active.output_dir
    excluded, date_results, matches, events, rejected, seen = _excluded_ids(), [], [], [], [], set()
    connector = EspnProspectiveConnector(EspnConnectorConfig(league=active.league, cache_dir=output / "cache", cache_ttl_seconds=active.cache_ttl_seconds))
    for value in _dates(active.start_date, active.end_date):
        _collect_date(connector, value, excluded, active, date_results, matches, events, rejected, seen)
    matches.sort(key=lambda row: (str(row["kickoff_ts"]), str(row["provider_match_id"])))
    events.sort(key=lambda row: (str(row["provider_match_id"]), int(row["event_index"])))
    coverage = {"candidate_match_count": len(matches), "candidate_event_count": len(events), "rejected_count": len(rejected), "date_count": len(date_results), "unique_candidate_ids": len({row["provider_match_id"] for row in matches})}
    audit = {"classification": "validated_extension_available" if matches else "blocked_by_data", "no_duplicate_candidate_ids": len(matches) == len({row["provider_match_id"] for row in matches}), "excluded_overlap": sorted({str(row["provider_match_id"]) for row in matches}.intersection(excluded)), "postgresql_modified": False, "staging_write_enabled": False, "raw_payloads_cached_locally": True, "public_artifacts_exclude_raw_data": True, "network_failures": sum(row["classification"] == "fetch_failed" for row in date_results)}
    manifest = {"config_hash": _hash(asdict(active)), "canonical_windows_hash": _file_hash(CANONICAL_WINDOWS), "phase09_targets_hash": _file_hash(PHASE09_TARGETS), "cache_dir": str((output / "cache").relative_to(ROOT)), "excluded_id_count": len(excluded)}
    result = {"config": asdict(active), "input_manifest": manifest, "coverage": coverage, "date_results": date_results, "candidate_matches": matches, "candidate_events": events, "rejected_records": rejected, "audit": audit, "classification": audit["classification"]}
    output.mkdir(parents=True, exist_ok=True)
    _write_artifacts(result, output)
    LOGGER.info("Fase 11 extensión histórica: %s (%d partidos)", result["classification"], len(matches))
    return result


def _collect_date(connector: EspnProspectiveConnector, value: str, excluded: set[str], config: Phase11Config, date_results: list[dict[str, Any]], matches: list[dict[str, Any]], events: list[dict[str, Any]], rejected: list[dict[str, Any]], seen: set[str]) -> None:
    """Consulta una fecha y añade sólo partidos completos nuevos."""

    try:
        board = connector.scoreboard(value)
        refs = scoreboard_references(board)
        date_results.append({"date": value, "scoreboard_events": len(board.get("events", [])), "references": len(refs), "classification": "source_ready" if refs else "source_empty"})
    except (OSError, ValueError, EspnConnectorError) as error:
        date_results.append({"date": value, "scoreboard_events": 0, "references": 0, "classification": "fetch_failed", "reason": str(error)[:160]})
        return
    for reference in refs:
        match_id = str(reference["provider_match_id"])
        if match_id in excluded or match_id in seen:
            continue
        seen.add(match_id)
        try:
            if config.sleep_between_requests:
                time.sleep(config.sleep_between_requests)
            batch, public = _normalize(connector, reference)
            if not bool(public["identity"].get("complete")):
                rejected.append({"match_id": match_id, "reason": "match_not_complete"})
                continue
            public_batch, public_events = _public_batch(batch)
            matches.append(public_batch["identity"])
            events.extend(public_events)
        except (OSError, ValueError, EspnConnectorError, RuntimeError) as error:
            rejected.append({"match_id": match_id, "reason": str(error)[:160]})


# Version: 1.0.0
# Created: 2026-07-26
