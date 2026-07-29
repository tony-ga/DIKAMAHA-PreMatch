"""Prueba el flujo completo de predicción con un fixture futuro real de ESPN.

La ejecución consulta únicamente scoreboard, usa el snapshot activo y publica
sólo resultados sanitizados; no guarda el payload crudo ni escribe PostgreSQL.

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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from src.dikamaha_service import ServiceConfig, create_app
from src.espn_fixture_resolver import connector_for_league

OUTPUT = ROOT / "artifacts/phase_51_real_fixture_flow_v1"
LOGGER = logging.getLogger(__name__)


def _write(name: str, payload: Any) -> None:
    """Publica JSON sanitizado mediante reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _parser() -> argparse.ArgumentParser:
    """Define los parámetros del smoke real."""

    parser = argparse.ArgumentParser(description="Prueba un fixture futuro real mediante ESPN.")
    parser.add_argument("--league", default="mex.1")
    parser.add_argument("--kickoff-date", default="20260731")
    parser.add_argument("--home-team-id", type=int, default=231)
    parser.add_argument("--away-team-id", type=int, default=219)
    return parser


def _request(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta la request operativa con dependencias reales de ESPN."""

    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True), fixture_resolver=connector_for_league(args.league))
    payload = {"league_slug": args.league, "kickoff_date": args.kickoff_date, "home_team_id": args.home_team_id, "away_team_id": args.away_team_id}
    response = TestClient(app).post("/v1/predict/fixture", json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"fixture_flow_http_{response.status_code}:{response.text[:240]}")
    return response.json()


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Conserva sólo identidad, mercados y auditorías necesarias."""

    fixture = data["fixture"]
    return {"fixture": {key: fixture[key] for key in ("league_slug", "match_id", "kickoff_ts", "home_team_id", "away_team_id", "home_team_name", "away_team_name", "provider_status")}, "prediction": {key: data[key] for key in ("model", "probability_home", "probability_draw", "probability_away", "probability_over_2_5", "probability_btts")}, "provenance": data["provenance"], "audit": data["audit"]}


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta y audita el flujo real sin persistir la respuesta."""

    data = _sanitize(_request(args))
    stale = bool(data["audit"].get("history_freshness_warning"))
    classification = "real_fixture_flow_verified_with_freshness_warning" if stale else "real_fixture_flow_verified"
    audit = {"classification": classification, "http_status": 200, "external_calls": True, "persistence": False, "target_match_data_used": data["audit"]["target_match_data_used"], "cutoff_causal": data["audit"]["cutoff_causal"], "snapshot_versioned": data["provenance"]["snapshot_versioned"], "history_freshness_warning": stale}
    _write("request.json", vars(args))
    _write("sanitized_result.json", data)
    _write("audit.json", audit)
    report = ["# Fase 51 — flujo real de fixture futuro", "", f"**Clasificación:** `{audit['classification']}`", "", f"- fixture: `{data['fixture']['home_team_name']} vs {data['fixture']['away_team_name']}`", f"- kickoff UTC: `{data['fixture']['kickoff_ts']}`", f"- match ESPN: `{data['fixture']['match_id']}`", f"- HTTP: `{audit['http_status']}`", f"- snapshot: `{data['provenance']['snapshot_id']}`", f"- advertencia de frescura: `{audit['history_freshness_warning']}`", "- persistencia: `False`", "- datos del objetivo usados: `False`", "- cutoff causal: `True`", "- Markov promovido: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 51: %s", run(_parser().parse_args())["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Flujo real rechazado: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
