"""Verifica la solicitud universal de un fixture futuro por nombres.

La prueba usa sólo scoreboard para resolver identidad y el snapshot activo para
inferir. La respuesta publicada se sanitiza y no se persiste el payload ESPN.

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

OUTPUT = ROOT / "artifacts/phase_55_universal_named_fixture_flow_v1"
LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Define la solicitud universal mínima para un usuario."""

    parser = argparse.ArgumentParser(description="Predice un fixture futuro por nombres y liga.")
    parser.add_argument("--league", default="mex.1")
    parser.add_argument("--kickoff-date", default="20260731")
    parser.add_argument("--home-team", default="Puebla")
    parser.add_argument("--away-team", default="Guadalajara")
    return parser


def _request(args: argparse.Namespace) -> dict[str, Any]:
    """Resuelve el fixture y solicita la inferencia al endpoint universal."""

    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True), fixture_resolver=connector_for_league(args.league))
    payload = {"league_slug": args.league, "kickoff_date": args.kickoff_date, "home_team_name": args.home_team, "away_team_name": args.away_team}
    response = TestClient(app).post("/v1/predict/fixture", json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"universal_fixture_http_{response.status_code}:{response.text[:240]}")
    return response.json()


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Conserva identidad, mercados, provenance y auditoría causal."""

    fixture_keys = ("league_slug", "match_id", "kickoff_ts", "home_team_id", "away_team_id", "home_team_name", "away_team_name", "provider_status")
    prediction_keys = ("model", "probability_home", "probability_draw", "probability_away", "probability_over_2_5", "probability_btts")
    return {"fixture": {key: data["fixture"][key] for key in fixture_keys}, "prediction": {key: data[key] for key in prediction_keys}, "provenance": data["provenance"], "audit": data["audit"]}


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON sanitizado de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta y documenta el flujo universal por nombres."""

    data = _sanitize(_request(args))
    audit = {"classification": "universal_named_fixture_verified", "http_status": 200, "persistence": False, "target_match_data_used": data["audit"]["target_match_data_used"], "cutoff_causal": data["audit"]["cutoff_causal"], "snapshot_id": data["provenance"]["snapshot_id"], "history_freshness_warning": data["audit"]["history_freshness_warning"]}
    _write("request.json", vars(args)); _write("sanitized_result.json", data); _write("audit.json", audit)
    report = ["# Fase 55 — solicitud universal por nombres", "", f"**Clasificación:** `{audit['classification']}`", "", f"- fixture: `{data['fixture']['home_team_name']} vs {data['fixture']['away_team_name']}`", f"- liga: `{data['fixture']['league_slug']}`", f"- match ESPN: `{data['fixture']['match_id']}`", f"- HTTP: `{audit['http_status']}`", f"- snapshot: `{audit['snapshot_id']}`", f"- cutoff causal: `{audit['cutoff_causal']}`", f"- datos del objetivo usados: `{audit['target_match_data_used']}`", f"- persistencia: `{audit['persistence']}`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 55: %s", run(_parser().parse_args())["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Solicitud universal rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
