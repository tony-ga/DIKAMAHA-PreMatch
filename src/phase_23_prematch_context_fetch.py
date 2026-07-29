"""Captura y audita contexto pre-match de ESPN.

La fase conserva en caché los payloads crudos de ``summary`` y publica sólo
variables sanitizadas de titulares, formación y cuotas de apertura.

Requirements:
    - requests
    - tenacity
    - SQLAlchemy==2.0.41
    - python-dotenv

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/phase_22_prematch_first_half_signal/feature_rows.json"
OUTPUT = ROOT / "artifacts/phase_23_prematch_context_fetch"
CACHE = ROOT / "data/cache/espn"
SUMMARY_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/summary"


def _load(path: Path) -> Any:
    """Carga JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula un hash estable de una estructura."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    """Calcula el SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_path(url: str) -> Path:
    """Obtiene la ruta de caché para un endpoint."""

    return CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


@retry(retry=retry_if_exception_type((requests.RequestException, ValueError)), wait=wait_exponential(multiplier=1, min=1, max=20), stop=stop_after_attempt(4), reraise=True)
def _request_summary(url: str) -> dict[str, Any]:
    """Obtiene un summary con retry exponencial y persistencia cruda."""

    cached = _cache_path(url)
    if cached.exists():
        payload = _load(cached)
        return payload["payload"] if isinstance(payload, dict) and "payload" in payload else payload
    response = requests.get(url, timeout=30, headers={"Accept": "application/json", "User-Agent": "futbol-predictor/0.1"})
    response.raise_for_status()
    payload = response.json()
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": payload}, ensure_ascii=False), encoding="utf-8")
    return payload


def _event_map() -> dict[int, str]:
    """Mapea IDs internos canónicos a eventos ESPN mediante SELECT."""

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("missing_database_url")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT match_id, source_event_id FROM raw_api_responses WHERE source_event_id IS NOT NULL ORDER BY id")).mappings().all()
    return {int(row["match_id"]): str(row["source_event_id"]) for row in rows}


def _decimal(block: Any) -> float | None:
    """Extrae una cuota decimal desde una estructura ESPN."""

    if not isinstance(block, dict):
        return None
    value = block.get("decimal")
    return float(value) if value is not None else None


def _parse_rosters(summary: dict[str, Any]) -> dict[str, Any]:
    """Resume titulares y formación sin leer estadísticas post-match."""

    output: dict[str, Any] = {"lineup_available": False}
    for roster in summary.get("rosters") or []:
        side = str(roster.get("homeAway") or "unknown")
        players = roster.get("roster") or []
        starters = [player for player in players if bool(player.get("starter"))]
        positions = Counter(str(player.get("position", {}).get("abbreviation") or "unknown") for player in starters)
        output[f"{side}_starter_count"] = len(starters)
        output[f"{side}_formation"] = roster.get("formation")
        output[f"{side}_starter_position_counts"] = dict(sorted(positions.items()))
        output[f"{side}_starter_athlete_ids"] = sorted(str(player.get("athlete", {}).get("id")) for player in starters if player.get("athlete", {}).get("id") is not None)
    output["lineup_available"] = all(output.get(f"{side}_starter_count", 0) >= 11 for side in ("home", "away"))
    return output


def _parse_open_odds(summary: dict[str, Any]) -> dict[str, Any]:
    """Selecciona sólo cuotas de apertura de proveedores no live."""

    candidates = []
    for item in summary.get("odds") or []:
        provider = str((item.get("provider") or {}).get("name") or "unknown")
        if "live" in provider.casefold() or not isinstance(item.get("open"), dict):
            continue
        home = _decimal(((item.get("homeTeamOdds") or {}).get("open") or {}).get("moneyLine"))
        away = _decimal(((item.get("awayTeamOdds") or {}).get("open") or {}).get("moneyLine"))
        draw = _decimal((item.get("open") or {}).get("draw"))
        if not all(value and value > 1.0 for value in (home, away, draw)):
            continue
        implied = [1.0 / home, 1.0 / draw, 1.0 / away]
        total = sum(implied)
        candidates.append({"provider": provider, "open_home_decimal": home, "open_draw_decimal": draw, "open_away_decimal": away, "open_home_probability": implied[0] / total, "open_draw_probability": implied[1] / total, "open_away_probability": implied[2] / total, "open_total_line": item.get("overUnder")})
    return candidates[0] | {"odds_open_available": True} if candidates else {"odds_open_available": False}


def _header(summary: dict[str, Any]) -> dict[str, Any]:
    """Extrae identidad del evento desde el header del summary."""

    header = summary.get("header") or {}; competition = (header.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    teams = {str(row.get("homeAway")): str((row.get("team") or {}).get("id")) for row in competitors}
    return {"summary_event_id": str(header.get("id") or ""), "summary_kickoff_ts": str(competition.get("date") or ""), "home_provider_team_id": teams.get("home"), "away_provider_team_id": teams.get("away")}


def _fetch_row(row: dict[str, Any], event_map: dict[int, str]) -> dict[str, Any]:
    """Descarga, parsea y audita el contexto de un partido."""

    match_id = int(row["match_id"]); event_id = event_map.get(match_id, str(match_id)); url = f"{SUMMARY_BASE}?event={event_id}"
    try:
        summary = _request_summary(url); identity = _header(summary); rosters = _parse_rosters(summary); odds = _parse_open_odds(summary)
        identity_pass = identity["summary_event_id"] == event_id and bool(identity["summary_kickoff_ts"])
        return {"match_id": match_id, "provider_event_id": event_id, "cutoff_ts": row["cutoff_ts"], "source_payload_hash": _hash(summary), "source_endpoint": url, "identity_pass": identity_pass, **identity, **rosters, **odds, "target_match_statistics_used": False, "source_publication_timestamp_available": False, "status": "ok"}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        LOGGER.error("Contexto ESPN fallido match_id=%s event_id=%s: %s", match_id, event_id, exc)
        return {"match_id": match_id, "provider_event_id": event_id, "cutoff_ts": row["cutoff_ts"], "identity_pass": False, "target_match_statistics_used": False, "status": "fetch_failed", "error_type": type(exc).__name__}


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos sanitizados y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("config", "input_manifest", "coverage", "audit", "context_rows"):
        (OUTPUT / f"{name}.json").write_text(json.dumps(result[name], indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(result["validation_report"] + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Captura contexto para las 1,140 filas limpias de Fase 22."""

    rows = _load(SOURCE); event_map = _event_map(); results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_fetch_row, row, event_map) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: int(item["match_id"])); total = len(rows); ok = [row for row in results if row["status"] == "ok"]
    identity_ok = sum(bool(row.get("identity_pass")) for row in ok); lineup_ok = sum(bool(row.get("lineup_available")) for row in ok); odds_ok = sum(bool(row.get("odds_open_available")) for row in ok); failures = total - len(ok)
    classification = "ready_for_next_phase" if len(ok) == total and identity_ok == total else "insufficient_coverage"
    audit = {"classification": classification, "all_payloads_cached_before_parse": True, "target_match_statistics_used": False, "forbidden_current_or_close_odds_used": False, "live_odds_excluded": True, "source_publication_timestamp_available": False, "identity_failures": total - identity_ok, "fetch_failures": failures, "markets_promoted": False, "research_only": True}
    config = {"version": "prematch_context_fetch_v1", "summary_endpoint": SUMMARY_BASE, "workers": 6, "lineup_fields": ["starter", "position", "formation"], "odds_fields": ["open"], "excluded_odds": ["current", "close", "live"]}
    manifest = {"source_feature_rows_hash": _hash_file(SOURCE), "database_mapping_rows": len(event_map), "input_match_count": total}
    coverage = {"input_matches": total, "summary_ok": len(ok), "summary_failed": failures, "identity_ok": identity_ok, "lineup_available": lineup_ok, "odds_open_available": odds_ok, "lineup_rate": lineup_ok / total if total else 0.0, "odds_open_rate": odds_ok / total if total else 0.0, "rows_with_candidate_context": sum(bool(row.get("lineup_available") or row.get("odds_open_available")) for row in ok)}
    validation = f"# Validation report — Fase 23\n\n- partidos de entrada: `{total}`\n- summaries recuperados: `{len(ok)}`\n- identidad válida: `{identity_ok}`\n- alineaciones utilizables: `{lineup_ok}`\n- cuotas `open` utilizables: `{odds_ok}`\n- timestamp histórico de publicación: `False`\n- estadísticas del partido objetivo usadas: `False`."
    final = ["# Fase 23 — captura de contexto pre-match", "", f"**Clasificación:** `{classification}`", "", f"- cobertura summary: `{len(ok)}/{total}`", f"- alineaciones: `{lineup_ok}/{total}`", f"- cuotas open: `{odds_ok}/{total}`", "- proveedores live y campos current/close excluidos", "- mercados promovidos: `False`", "", "Limitación: ESPN no entrega timestamp histórico de publicación; el contexto queda en research-only hasta la evaluación OOS y revisión temporal."]
    result = {"config": config, "input_manifest": manifest, "coverage": coverage, "audit": audit, "context_rows": results, "validation_report": validation, "final_report": "\n".join(final)}
    _publish(result); LOGGER.info("Fase 23 captura de contexto: %s", classification); return result


# Version: 1.0.0
# Created: 2026-07-26
