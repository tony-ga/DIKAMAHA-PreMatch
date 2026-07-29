"""Genera lambdas prospectivas OOS desde el historial previo al corte.

Requirements:
    - SQLAlchemy==2.0.41
    - psycopg2-binary==2.9.10
    - pandas
    - scipy

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_16_prospective_evaluation/prospective_lambda_base_input.json"
sys.path.insert(0, str(ROOT))
LOGGER = logging.getLogger(__name__)

from dotenv import load_dotenv
from sqlalchemy import create_engine, text



def _hash(value: Any) -> str:
    """Calcula un hash estable del contenido serializado."""
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_source(database_url: str, cutoff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Lee historial y staging mediante SELECT, sin escribir en PostgreSQL."""
    engine = create_engine(database_url)
    with engine.connect() as connection:
        history = connection.execute(text("SELECT id, home_team_id, away_team_id, match_date, season, home_score, away_score FROM matches WHERE match_date < :cutoff ORDER BY match_date, id"), {"cutoff": cutoff}).mappings().all()
        teams = connection.execute(text("SELECT id, espn_team_id FROM teams WHERE espn_team_id IS NOT NULL")).mappings().all()
        staging = connection.execute(text("SELECT provider_match_id, kickoff_ts, home_provider_team_id, away_provider_team_id FROM prospective_staging_v2.matches WHERE provider='espn' AND complete=true ORDER BY kickoff_ts, provider_match_id")).mappings().all()
    return [dict(row) for row in history], [dict(row) for row in teams], [dict(row) for row in staging]


def _frozen_parameters() -> dict[str, Any]:
    """Carga parámetros Dixon-Coles históricos ya auditados y congelados."""
    path = ROOT / "artifacts/phase_3_4_dixon_coles_v1_dry_run/dixon_coles_v1_fold_parameters.json"
    values = json.loads(path.read_text(encoding="utf-8"))[-1]
    attack_mean = sum(values["attack"].values()) / len(values["attack"])
    defense_mean = sum(values["defense"].values()) / len(values["defense"])
    for team_id in (92, 1538, 3751):
        values["attack"].setdefault(str(team_id), attack_mean)
        values["defense"].setdefault(str(team_id), defense_mean)
    values["source_artifact"] = str(path.relative_to(ROOT))
    return values


def _rows(parameters: dict[str, Any], teams: list[dict[str, Any]], staging: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Predice lambdas para cada partido ESPN completo usando el catálogo mapeado."""
    mapping = {int(row["espn_team_id"]): int(row["id"]) for row in teams}
    known_provider_ids = {int(row["home_provider_team_id"]) for row in staging} | {int(row["away_provider_team_id"]) for row in staging}
    mapping.update({team_id: team_id for team_id in known_provider_ids if team_id not in mapping})
    rows = []
    for match in staging:
        home, away = mapping.get(int(match["home_provider_team_id"])), mapping.get(int(match["away_provider_team_id"]))
        if home is None or away is None:
            raise ValueError(f"missing_team_mapping:{match['provider_match_id']}")
        home_lambda = _lambda(parameters, home, away, home_advantage=True)
        away_lambda = _lambda(parameters, away, home, home_advantage=False)
        rows.append({"match_id": int(match["provider_match_id"]), "kickoff_ts": str(match["kickoff_ts"]), "lambda_base_home": home_lambda, "lambda_base_away": away_lambda})
    return rows


def _lambda(parameters: dict[str, Any], attack_team: int, defense_team: int, *, home_advantage: bool) -> float:
    """Calcula una intensidad Dixon-Coles con parámetros congelados."""
    attack = float(parameters["attack"][str(attack_team)])
    defense = float(parameters["defense"][str(defense_team)])
    offset = float(parameters["home_advantage"]) if home_advantage else 0.0
    value = float(__import__("math").exp(float(parameters["league_intercept"]) + offset + attack - defense))
    return max(1e-9, min(value, 100.0))


def main() -> int:
    """Genera y publica el input congelado de lambdas prospectivas."""
    load_dotenv(ROOT / ".env")
    database_url = os.environ["DATABASE_URL"]
    cutoff = os.getenv("DIKAMAHA_PROSPECTIVE_CUTOFF_TS", "2025-10-26T15:15:00+00:00")
    history, teams, staging = _read_source(database_url, cutoff)
    parameters = _frozen_parameters()
    rows = _rows(parameters, teams, staging)
    payload = {"version": "phase_7_16_lambda_input_v1", "cutoff_ts": cutoff, "source": "frozen_dixon_coles_parameters_before_cutoff", "history_match_count": len(history), "parameter_source": parameters["source_artifact"], "rows": rows}
    payload["input_hash"] = _hash(payload)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    LOGGER.info("Input lambda generado: filas=%d hash=%s", len(rows), payload["input_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-26
