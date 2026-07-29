"""Auditor de catálogo de equipos ESPN vs `teams`.

Modo:
- solo lectura / dry-run

Requirements:
- python-dotenv
- sqlalchemy
- requests
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.manager import DatabaseConnectionError, DatabaseManager, Team

logger = logging.getLogger("audit_team_catalog")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class TeamCatalogAuditError(RuntimeError):
    """Error de auditoría del catálogo."""


@dataclass(slots=True)
class EspnTeamRecord:
    """Representa un equipo ESPN de una temporada."""

    espn_team_id: int
    name: str
    display_name: str
    season: str
    league: str


def _normalize_name(value: Optional[str]) -> str:
    """Normaliza nombres para comparación exacta asistida."""

    if not value:
        return ""
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _season_year(season: str) -> int:
    """Extrae el año base de una temporada textual."""

    if season.isdigit():
        return int(season)
    match = re.match(r"^(\d{4})-\d{2}$", season)
    if match:
        return int(match.group(1))
    raise TeamCatalogAuditError(f"Temporada no reconocida: {season}")


def _load_espn_team_refs(league: str, season: str) -> list[str]:
    """Carga referencias ESPN a equipos de la temporada."""

    year = _season_year(season)
    url = f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/seasons/{year}/teams?lang=en&region=us"
    payload = requests.get(url, timeout=30).json()
    return [str(item["$ref"]) for item in payload.get("items", []) if isinstance(item, dict) and "$ref" in item]


def _fetch_team(ref: str, league: str, season: str) -> EspnTeamRecord:
    """Descarga el detalle de un equipo ESPN a partir de su referencia."""

    payload = requests.get(ref, timeout=30).json()
    espn_team_id = int(payload["id"])
    name = str(payload.get("name") or payload.get("displayName") or payload.get("location") or "")
    display_name = str(payload.get("displayName") or payload.get("name") or payload.get("location") or "")
    return EspnTeamRecord(
        espn_team_id=espn_team_id,
        name=name,
        display_name=display_name,
        season=season,
        league=league,
    )


def _load_espn_catalog(league: str, season: str) -> list[EspnTeamRecord]:
    """Construye el catálogo ESPN completo de la temporada."""

    refs = _load_espn_team_refs(league, season)
    return [_fetch_team(ref, league, season) for ref in refs]


def _load_local_teams(session: Session) -> list[Team]:
    """Carga el catálogo local de equipos."""

    stmt = select(Team).order_by(Team.id)
    return list(session.execute(stmt).scalars().all())


def _classify_catalog(
    espn_catalog: list[EspnTeamRecord],
    local_teams: list[Team],
) -> dict[str, Any]:
    """Clasifica correspondencias entre ESPN y catálogo local."""

    by_espn_id = {team.espn_team_id: team for team in local_teams if team.espn_team_id is not None}
    by_name: dict[str, list[Team]] = {}
    for team in local_teams:
        by_name.setdefault(_normalize_name(team.name), []).append(team)
    duplicate_local_groups = [
        {
            "name": name,
            "count": len(candidates),
            "candidates": [
                {"id": team.id, "name": team.name, "espn_team_id": team.espn_team_id}
                for team in candidates
            ],
        }
        for name, candidates in by_name.items()
        if len(candidates) > 1
    ]

    rows: list[dict[str, Any]] = []
    missing_local: list[dict[str, Any]] = []
    unmapped_local = [team for team in local_teams if team.espn_team_id is None]

    for espn_team in espn_catalog:
        mapped = by_espn_id.get(espn_team.espn_team_id)
        name_candidates = by_name.get(_normalize_name(espn_team.display_name), [])
        if mapped is not None:
            rows.append(
                {
                    "espn_team_id": espn_team.espn_team_id,
                    "name": espn_team.name,
                    "display_name": espn_team.display_name,
                    "season": espn_team.season,
                    "league": espn_team.league,
                    "status": "mapped",
                    "local_id": mapped.id,
                    "local_name": mapped.name,
                    "local_espn_team_id": mapped.espn_team_id,
                }
            )
            continue
        if len(name_candidates) > 1:
            rows.append(
                {
                    "espn_team_id": espn_team.espn_team_id,
                    "name": espn_team.name,
                    "display_name": espn_team.display_name,
                    "season": espn_team.season,
                    "league": espn_team.league,
                    "status": "duplicate_local",
                    "local_candidates": [
                        {"id": team.id, "name": team.name, "espn_team_id": team.espn_team_id}
                        for team in name_candidates
                    ],
                }
            )
            continue
        if len(name_candidates) == 1 and name_candidates[0].espn_team_id is None:
            rows.append(
                {
                    "espn_team_id": espn_team.espn_team_id,
                    "name": espn_team.name,
                    "display_name": espn_team.display_name,
                    "season": espn_team.season,
                    "league": espn_team.league,
                    "status": "unmapped_local",
                    "local_id": name_candidates[0].id,
                    "local_name": name_candidates[0].name,
                    "local_espn_team_id": None,
                }
            )
            continue
        missing_local.append(
            {
                "espn_team_id": espn_team.espn_team_id,
                "name": espn_team.name,
                "display_name": espn_team.display_name,
                "season": espn_team.season,
                "league": espn_team.league,
            }
        )
        rows.append(
            {
                "espn_team_id": espn_team.espn_team_id,
                "name": espn_team.name,
                "display_name": espn_team.display_name,
                "season": espn_team.season,
                "league": espn_team.league,
                "status": "missing_local",
            }
        )

    return {
        "catalog": [asdict(team) for team in espn_catalog],
        "correspondences": rows,
        "missing_local": missing_local,
        "duplicate_local": duplicate_local_groups,
        "unmapped_local": [
            {"id": team.id, "name": team.name, "espn_team_id": team.espn_team_id}
            for team in unmapped_local
        ],
        "approval_required_ids": sorted(
            {
                row["espn_team_id"]
                for row in rows
                if row["status"] in {"duplicate_local", "missing_local"}
            }
        ),
        "counts": {
            "mapped": sum(1 for row in rows if row["status"] == "mapped"),
            "unmapped_local": sum(1 for row in rows if row["status"] == "unmapped_local"),
            "missing_local": sum(1 for row in rows if row["status"] == "missing_local"),
            "duplicate_local": len(duplicate_local_groups),
        },
    }


def audit_catalog(league: str, season: str) -> dict[str, Any]:
    """Audita el catálogo ESPN y local sin escribir en base de datos."""

    manager = DatabaseManager()
    with manager.SessionLocal() as session:
        local_teams = _load_local_teams(session)
        espn_catalog = _load_espn_catalog(league, season)
        report = _classify_catalog(espn_catalog, local_teams)
        report["local_count_before"] = len(local_teams)
        report["local_count_after"] = len(local_teams)
        report["espn_count"] = len(espn_catalog)
        return report


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI."""

    parser = argparse.ArgumentParser(description="Auditor de catálogo ESPN vs teams")
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    return parser


def main() -> int:
    """Punto de entrada CLI."""

    parser = build_parser()
    args = parser.parse_args()
    try:
        report = audit_catalog(args.league, args.season)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except (TeamCatalogAuditError, DatabaseConnectionError, requests.RequestException, ValueError) as exc:
        logger.error("Fallo del auditor de catálogo: %s", exc, exc_info=True)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
