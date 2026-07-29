"""Prueba solicitudes universales sobre fixtures futuros de varias ligas.

Descubre sólo mediante scoreboard ESPN y ejecuta la inferencia por nombres de
equipos. No consulta play-by-play, no escribe PostgreSQL y publica únicamente
resultados sanitizados.

Requirements:
    - fastapi
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from src.dikamaha_service import ServiceConfig, create_app
from src.espn_fixture_resolver import connector_for_league, scoreboard_fixtures
from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector

DISCOVERY = ROOT / "artifacts/phase_36_multileague_discovery/references.json"
OUTPUT = ROOT / "artifacts/phase_56_multileague_upcoming_flow_v1"
LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Define el alcance de la comprobación multi-liga."""

    parser = argparse.ArgumentParser(description="Prueba fixtures futuros multi-liga por nombres.")
    parser.add_argument("--start-date", default="20260728")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-leagues", type=int, default=12)
    parser.add_argument("--league", help="Slugs separados por coma; por defecto usa los documentados.")
    return parser


def _leagues(argument: str | None) -> list[str]:
    """Obtiene y valida los slugs disponibles en el discovery local."""

    payload = json.loads(DISCOVERY.read_text(encoding="utf-8"))
    available = sorted({str(row["league_slug"]) for row in payload if isinstance(row, dict) and row.get("league_slug")})
    selected = sorted({item.strip() for item in argument.split(",") if item.strip()}) if argument else available
    if not selected or not set(selected).issubset(set(available)):
        raise ValueError("invalid_documented_league_selection")
    return selected


def _dates(start: str, days: int) -> list[str]:
    """Construye fechas ESPN inclusivas dentro de un límite operativo."""

    try:
        first = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    except ValueError as error:
        raise ValueError("invalid_start_date") from error
    if days < 1 or days > 31:
        raise ValueError("days_out_of_range")
    return [(first + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(days)]


def _candidate(league: str, fixture: Any) -> dict[str, Any] | None:
    """Convierte un fixture futuro en un request universal sanitizado."""

    if fixture.provider_status in {"in", "live", "post", "final", "completed"}:
        return None
    kickoff = datetime.fromisoformat(fixture.kickoff_ts).astimezone(timezone.utc)
    if kickoff <= datetime.now(timezone.utc):
        return None
    return {"league_slug": league, "kickoff_date": kickoff.strftime("%Y%m%d"), "match_id": fixture.match_id, "kickoff_ts": fixture.kickoff_ts, "home_team_name": fixture.home_team_name, "away_team_name": fixture.away_team_name}


def _discover(leagues: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Descubre como máximo un partido futuro por liga."""

    found: list[dict[str, Any]] = []
    for league in leagues:
        provider = EspnProspectiveConnector(EspnConnectorConfig(league=league, cache_dir=OUTPUT / "cache" / league, cache_ttl_seconds=86400))
        options = []
        for day in _dates(args.start_date, args.days):
            options.extend(scoreboard_fixtures(provider.scoreboard(day), league))
        candidates = [_candidate(league, fixture) for fixture in options]
        valid = [item for item in candidates if item]
        if valid:
            found.append(sorted(valid, key=lambda item: item["kickoff_ts"])[0])
        LOGGER.info("Fixtures futuros league=%s encontrados=%s", league, len(valid))
        if len(found) >= args.max_leagues:
            break
    return found


def _predict(item: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta el endpoint universal con nombres de equipos."""

    league = str(item["league_slug"])
    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True), fixture_resolver=connector_for_league(league))
    payload = {key: item[key] for key in ("league_slug", "kickoff_date", "home_team_name", "away_team_name")}
    response = TestClient(app).post("/v1/predict/fixture", json=payload)
    if response.status_code != 200:
        return {"league_slug": league, "request": payload, "status_code": response.status_code, "error": response.text[:240]}
    data = response.json()
    return {"league_slug": league, "status_code": 200, "fixture": data["fixture"], "prediction": {key: data[key] for key in ("model", "probability_home", "probability_draw", "probability_away", "probability_over_2_5", "probability_btts")}, "snapshot_id": data["provenance"]["snapshot_id"], "cutoff_causal": data["audit"]["cutoff_causal"], "target_match_data_used": data["audit"]["target_match_data_used"], "history_freshness_warning": data["audit"]["history_freshness_warning"]}


def _write(name: str, payload: Any) -> None:
    """Escribe artefactos JSON sanitizados de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta descubrimiento y predicción multi-liga."""

    leagues = _leagues(args.league)
    candidates = _discover(leagues, args)
    results = [_predict(item) for item in candidates]
    verified = [item for item in results if item.get("status_code") == 200 and item.get("cutoff_causal") and not item.get("target_match_data_used")]
    audit = {"classification": "multileague_upcoming_flow_verified" if verified else "multileague_upcoming_flow_no_verified_fixture", "leagues_scanned": len(leagues), "future_candidates": len(candidates), "predictions_http_200": sum(item.get("status_code") == 200 for item in results), "verified_causal_predictions": len(verified), "results": results, "persistence": False, "postgresql_written": False, "play_by_play_requested": False}
    _write("request.json", vars(args)); _write("audit.json", audit)
    report = ["# Fase 56 — flujo upcoming multi-liga", "", f"**Clasificación:** `{audit['classification']}`", "", f"- ligas escaneadas: `{audit['leagues_scanned']}`", f"- candidatos futuros: `{audit['future_candidates']}`", f"- respuestas HTTP 200: `{audit['predictions_http_200']}`", f"- predicciones causales verificadas: `{audit['verified_causal_predictions']}`", "- PostgreSQL escrito: `False`", "- play-by-play solicitado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 56: %s", run(_parser().parse_args())["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Flujo upcoming rechazado: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
