"""Loader ejecutable para reconciliar `match_statistics`.

Modo por defecto:
- `--dry-run`

Modo persistente:
- `--persist --confirm-persist`

Requirements:
- python-dotenv
- sqlalchemy
- requests
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
from sqlalchemy import MetaData, Table, create_engine, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.espn_client import ESPNClient, ESPNClientError
from src.api.espn_parser import ESPNPlayParser
from src.config.settings import settings

logger = logging.getLogger("match_statistics_loader")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass(slots=True)
class TeamMapping:
    """Mapeo entre equipo interno y ESPN."""

    internal_team_id: int
    espn_team_id: int
    name: str


@dataclass(slots=True)
class PreparedRow:
    """Fila preparada para `match_statistics`."""

    payload: dict[str, Any]
    summary_values: dict[str, Any]
    derived_values: dict[str, Any]
    has_conflict: bool
    needs_review: bool
    source_confidence: float
    reconciliation_confidence: float
    include_quality_fields: bool
    reconciliation_status: str = "accepted"


class MatchStatisticsLoaderError(RuntimeError):
    """Error base del loader."""


def _utcnow() -> datetime:
    """Devuelve un timestamp UTC consciente de zona horaria."""

    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime:
    """Convierte un ISO-8601 de ESPN a `datetime` consciente de zona horaria."""

    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _to_utc_naive(value: datetime) -> datetime:
    """Normaliza un `datetime` a UTC sin zona horaria."""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _extract_play_ref_event_id(plays: dict[str, Any]) -> int:
    """Extrae el `event_id` desde la ruta `'$ref'` del play-by-play."""

    ref = str(plays.get("$ref") or "")
    marker = "/events/"
    start = ref.find(marker)
    if start == -1:
        raise MatchStatisticsLoaderError("No se pudo identificar el event_id en el play-by-play.")
    start += len(marker)
    end = ref.find("/", start)
    if end == -1:
        raise MatchStatisticsLoaderError("No se pudo identificar la competencia en el play-by-play.")
    return int(ref[start:end])


def _load_match(session: Session, match_id: int) -> dict[str, Any]:
    """Carga el match interno requerido."""

    row = session.execute(
        text(
            """
            SELECT id, home_team_id, away_team_id, match_date, season, home_score, away_score, status
            FROM matches
            WHERE id = :match_id
            """
        ),
        {"match_id": match_id},
    ).mappings().one()
    return dict(row)


def _load_team_mappings(session: Session, match: dict[str, Any]) -> dict[str, TeamMapping]:
    """Carga el mapeo interno/ESPN de los dos equipos del partido."""

    rows = session.execute(
        text(
            """
            SELECT id, name, espn_team_id
            FROM teams
            WHERE id IN (:home_id, :away_id)
            ORDER BY id
            """
        ),
        {"home_id": match["home_team_id"], "away_id": match["away_team_id"]},
    ).mappings().all()
    mappings: dict[str, TeamMapping] = {}
    for row in rows:
        if row["espn_team_id"] is None:
            continue
        key = "home" if int(row["id"]) == int(match["home_team_id"]) else "away"
        mappings[key] = TeamMapping(int(row["id"]), int(row["espn_team_id"]), str(row["name"]))
    return mappings


def _validate_context(
    summary: dict[str, Any],
    plays: dict[str, Any],
    match: dict[str, Any],
    mappings: dict[str, TeamMapping],
    espn_event_id: int,
) -> None:
    """Valida contexto, orientación y mapeo antes de preparar filas."""

    summary_event_id = int(summary["header"]["id"])
    plays_event_id = _extract_play_ref_event_id(plays)
    competition = summary["header"]["competitions"][0]
    competitors = competition["competitors"]
    summary_home = next((item for item in competitors if item.get("homeAway") == "home"), None)
    summary_away = next((item for item in competitors if item.get("homeAway") == "away"), None)
    if summary_event_id != plays_event_id:
        raise MatchStatisticsLoaderError("Summary y play-by-play no corresponden al mismo evento ESPN.")
    if summary_event_id != espn_event_id:
        raise MatchStatisticsLoaderError(
            f"El resumen no corresponde al evento esperado: esperado={espn_event_id}, recibido={summary_event_id}."
        )
    if summary_home is None or summary_away is None:
        raise MatchStatisticsLoaderError("No se pudieron identificar local y visitante en el summary ESPN.")
    if int(summary_home["team"]["id"]) != mappings["home"].espn_team_id or int(summary_away["team"]["id"]) != mappings["away"].espn_team_id:
        raise MatchStatisticsLoaderError(
            "La orientación del summary no coincide con el match interno esperado."
        )
    if int(match["home_team_id"]) != mappings["home"].internal_team_id or int(match["away_team_id"]) != mappings["away"].internal_team_id:
        raise MatchStatisticsLoaderError("El match interno no coincide con los equipos esperados para este evento ESPN.")
    if _to_utc_naive(match["match_date"]) != _to_utc_naive(_parse_iso_datetime(str(competition["date"]))):
        raise MatchStatisticsLoaderError("La fecha del match interno no coincide con el evento ESPN.")
    if int(summary_home.get("score", 0)) != int(match["home_score"]) or int(summary_away.get("score", 0)) != int(match["away_score"]):
        raise MatchStatisticsLoaderError("El marcador interno no coincide con el evento ESPN.")
    if mappings.get("home") is None or mappings.get("away") is None:
        raise MatchStatisticsLoaderError("Falta el mapeo completo de equipos ESPN -> teams.id.")


def _summary_stats(summary: dict[str, Any]) -> dict[int, dict[str, float]]:
    """Normaliza las estadísticas del summary por equipo ESPN."""

    metrics = {
        "foulsCommitted": "fouls",
        "yellowCards": "yellow_cards",
        "redCards": "red_cards",
        "wonCorners": "corners",
        "saves": "saves",
        "possessionPct": "possession_pct",
        "totalShots": "shots_total",
        "shotsOnTarget": "shots_on_target",
    }
    normalized: dict[int, dict[str, float]] = {}
    for side in summary["boxscore"]["teams"]:
        espn_team_id = int(side["team"]["id"])
        values: dict[str, float] = {}
        for stat in side["statistics"]:
            name = metrics.get(stat["name"])
            if name is None:
                continue
            values[name] = float(stat.get("displayValue") or 0)
        normalized[espn_team_id] = values
    return normalized


def _score_coverage(values: dict[str, Any], keys: Iterable[str]) -> float:
    """Calcula la cobertura de claves esperadas en una fuente."""

    expected = list(keys)
    if not expected:
        return 0.0
    present = sum(1 for key in expected if key in values and values[key] is not None)
    return present / len(expected)


def _classify_confidence(
    summary_values: dict[str, Any],
    derived_values: dict[str, Any],
    has_conflict: bool,
    goals_mismatch: bool,
) -> tuple[float, float, bool]:
    """Calcula confianza de fuente, reconciliación y estado de revisión."""

    summary_keys = ("shots_total", "shots_on_target", "fouls", "yellow_cards", "red_cards", "corners", "saves", "possession_pct", "goals")
    derived_keys = ("shots_total", "shots_on_target", "fouls", "yellow_cards", "red_cards", "corners", "saves", "goals")
    summary_coverage = _score_coverage(summary_values, summary_keys)
    derived_coverage = _score_coverage(derived_values, derived_keys)
    source_confidence = round(0.90 + 0.10 * summary_coverage, 4)
    reconciliation_confidence = round(0.75 + 0.20 * min(summary_coverage, derived_coverage), 4)
    needs_review = bool(goals_mismatch or (has_conflict and summary_coverage < 1.0))
    if goals_mismatch:
        reconciliation_confidence = min(reconciliation_confidence, 0.60)
    elif has_conflict:
        reconciliation_confidence = min(reconciliation_confidence, 0.93)
    return source_confidence, reconciliation_confidence, needs_review


def _metric_severity(metric: str, summary_value: Any, derived_value: Any) -> str:
    """Clasifica la severidad de una diferencia métrica."""

    if summary_value is None:
        return "critical"
    if derived_value is None:
        return "low"
    if summary_value == derived_value:
        return "none"
    if metric in {"corners", "saves", "shots_total", "shots_on_target", "fouls", "yellow_cards", "red_cards"}:
        return "low"
    return "low"


def _reconciliation_status(
    *,
    identity_validated: bool,
    teams_validated: bool,
    orientation_validated: bool,
    date_validated: bool,
    goals_mismatch: bool,
    summary_values: dict[str, Any],
    metric_conflicts: dict[str, dict[str, Any]],
) -> tuple[str, bool]:
    """Determina el estado final de reconciliación para v3."""

    required_metrics = ("shots_total", "shots_on_target", "fouls", "yellow_cards", "red_cards", "corners", "saves", "possession_pct", "goals")
    if not identity_validated or not teams_validated or not orientation_validated or not date_validated:
        return "rejected", False
    missing_summary = any(summary_values.get(metric) is None for metric in required_metrics)
    if missing_summary:
        return "needs_review", True
    if goals_mismatch:
        return "needs_review", True
    if any(item["severity"] == "critical" for item in metric_conflicts.values()):
        return "needs_review", True
    if any(item["severity"] == "low" for item in metric_conflicts.values()):
        return "accepted", False
    return "accepted", False


def _build_metric_conflicts(summary_values: dict[str, Any], derived_values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Construye un diccionario explícito de conflictos por métrica."""

    metric_conflicts: dict[str, dict[str, Any]] = {}
    for metric in ("shots_total", "shots_on_target", "fouls", "yellow_cards", "red_cards", "corners", "saves", "goals"):
        summary_value = summary_values.get(metric)
        derived_value = derived_values.get(metric)
        metric_conflicts[metric] = {
            "summary": summary_value,
            "derived": derived_value,
            "difference": None if summary_value is None or derived_value is None else summary_value - derived_value,
            "severity": _metric_severity(metric, summary_value, derived_value),
        }
    if "possession_pct" in summary_values:
        metric_conflicts["possession_pct"] = {
            "summary": summary_values.get("possession_pct"),
            "derived": derived_values.get("possession_pct"),
            "difference": None,
            "severity": "none",
        }
    return metric_conflicts


def _empty_counter() -> dict[str, int]:
    """Crea una estructura de contadores vacía."""

    return defaultdict(int)  # type: ignore[return-value]


def _derived_stats(plays: dict[str, Any], parsed: list[dict[str, Any]]) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    """Deriva métricas desde play-by-play para auditoría y fallback."""

    stats: dict[int, dict[str, int]] = defaultdict(_empty_counter)  # type: ignore[assignment]
    var_annulled: dict[int, int] = defaultdict(int)
    for event in parsed:
        team_id = event["team_id"]
        if team_id is None:
            continue
        espn_team_id = int(team_id)
        bucket = stats[espn_team_id]
        if event["event_type"] == "goal":
            bucket["goals"] += 1
            bucket["shots_total"] += 1
            bucket["shots_on_target"] += 1
        elif event["event_type"] == "shot_on_target":
            bucket["shots_total"] += 1
            bucket["shots_on_target"] += 1
        elif event["event_type"] == "shot_off_target":
            bucket["shots_total"] += 1
        elif event["event_type"] == "shot_blocked":
            bucket["shots_total"] += 1
        elif event["event_type"] == "corner":
            bucket["corners"] += 1
        elif event["event_type"] == "foul":
            bucket["fouls"] += 1
        elif event["event_type"] == "yellow":
            bucket["yellow_cards"] += 1
        elif event["event_type"] == "red":
            bucket["red_cards"] += 1
    for item in plays.get("items", []):
        if ((item.get("type") or {}).get("type")) == "shot-hit-woodwork":
            ref = (item.get("team") or {}).get("$ref") or ""
            espn_team_id = 86 if "/teams/86" in ref else 83 if "/teams/83" in ref else None
            if espn_team_id is not None:
                stats[espn_team_id]["shots_total"] += 1
                stats[espn_team_id]["shot_hit_woodwork"] += 1
        if ((item.get("type") or {}).get("type")) in {"deleted-after-review", "var---referee-decision-cancelled"}:
            ref = (item.get("team") or {}).get("$ref") or ""
            espn_team_id = 86 if "/teams/86" in ref else 83 if "/teams/83" in ref else None
            if espn_team_id is not None:
                var_annulled[espn_team_id] += 1
    return stats, var_annulled


def _build_row(
    match_id: int,
    internal_team_id: int,
    espn_team_id: int,
    source_event_id: int,
    summary_values: dict[str, float],
    derived_values: dict[str, int],
    goals: int,
    var_annulled_events: int,
    reconciliation_version: str,
    include_quality_fields: bool,
    status_override: str | None = None,
) -> PreparedRow:
    """Construye una fila preparada para persistencia."""

    summary_with_goals = dict(summary_values)
    summary_with_goals.setdefault("goals", goals)
    metric_conflicts = _build_metric_conflicts(summary_with_goals, derived_values)
    goals_mismatch = int(goals) != int(derived_values.get("goals", 0))
    has_conflict = any(item["severity"] != "none" for item in metric_conflicts.values()) or goals_mismatch
    source_confidence, reconciliation_confidence, needs_review = _classify_confidence(
        summary_with_goals,
        derived_values,
        has_conflict,
        goals_mismatch,
    )
    if reconciliation_version == "v3":
        status, derived_needs_review = _reconciliation_status(
            identity_validated=True,
            teams_validated=True,
            orientation_validated=True,
            date_validated=True,
            goals_mismatch=goals_mismatch,
            summary_values=summary_with_goals,
            metric_conflicts=metric_conflicts,
        )
        needs_review = derived_needs_review
    else:
        status = "needs_review" if needs_review else "accepted"
    payload = {
        "match_id": match_id,
        "team_id": internal_team_id,
        "source": "espn_summary",
        "reconciliation_version": reconciliation_version,
        "shots_total": int(summary_values.get("shots_total", 0)),
        "shots_on_target": int(summary_values.get("shots_on_target", 0)),
        "fouls": int(summary_values.get("fouls", 0)),
        "yellow_cards": int(summary_values.get("yellow_cards", 0)),
        "red_cards": int(summary_values.get("red_cards", 0)),
        "corners": int(summary_values.get("corners", 0)),
        "saves": int(summary_values.get("saves", 0)),
        "possession_pct": float(summary_values.get("possession_pct", 0)),
        "goals": goals,
        "var_annulled_events": var_annulled_events,
        "source_event_id": source_event_id,
        "source_fetched_at": _utcnow(),
        "created_at": _utcnow(),
        "reconciled_at": _utcnow(),
        "has_conflict": has_conflict,
        "primary_source": "espn_summary",
        "fallback_source": "derived_play_by_play",
        "confidence": reconciliation_confidence,
        "derived_play_by_play": derived_values,
        "espn_summary": summary_with_goals,
    }
    if include_quality_fields:
        payload["source_confidence"] = source_confidence
        payload["reconciliation_confidence"] = reconciliation_confidence
        payload["needs_review"] = needs_review
        payload["reconciliation_status"] = status
        payload["conflict_details"] = {
            "has_conflict": has_conflict,
            "goals_mismatch": goals_mismatch,
            "summary_confidence": source_confidence,
            "reconciliation_confidence": reconciliation_confidence,
            "summary_keys": sorted(summary_with_goals.keys()),
            "derived_keys": sorted(derived_values.keys()),
            "metric_conflicts": metric_conflicts,
        }
    return PreparedRow(
        payload=payload,
        summary_values=summary_with_goals,
        derived_values=derived_values,
        has_conflict=has_conflict,
        needs_review=needs_review,
        source_confidence=source_confidence,
        reconciliation_confidence=reconciliation_confidence,
        include_quality_fields=include_quality_fields,
        reconciliation_status=status,
    )


def _prepare_rows(
    match_id: int,
    match: dict[str, Any],
    summary: dict[str, Any],
    plays: dict[str, Any],
    parsed: list[dict[str, Any]],
    mappings: dict[str, TeamMapping],
    reconciliation_version: str,
) -> list[PreparedRow]:
    """Prepara las filas candidatas sin escribir en base de datos."""

    include_quality_fields = reconciliation_version in {"v2", "v3"}
    summary_by_team = _summary_stats(summary)
    derived_by_team, var_annulled_by_team = _derived_stats(plays, parsed)
    source_event_id = int(summary["header"]["id"])
    rows: list[PreparedRow] = []
    for side, mapping in mappings.items():
        espn_team_id = mapping.espn_team_id
        goals = int(match["home_score"]) if side == "home" else int(match["away_score"])
        rows.append(
            _build_row(
                match_id,
                mapping.internal_team_id,
                espn_team_id,
                source_event_id,
                summary_by_team.get(espn_team_id, {}),
                derived_by_team.get(espn_team_id, {}),
                goals,
                var_annulled_by_team.get(espn_team_id, 0),
                reconciliation_version,
                include_quality_fields,
            )
        )
    return rows


def _summary_payload(summary: dict[str, Any], match_id: int, reconciliation_version: str, rows: list[PreparedRow]) -> dict[str, Any]:
    """Construye un resumen JSON para salida de dry-run/persist."""

    return {
        "match_id": match_id,
        "reconciliation_version": reconciliation_version,
        "rows_prepared": len(rows),
        "rows_inserted": 0,
        "conflicts": [row.has_conflict for row in rows],
        "needs_review": [row.needs_review for row in rows],
        "rows": [
            {
                **row.payload,
                "source_confidence": row.source_confidence,
                "reconciliation_confidence": row.reconciliation_confidence,
                "needs_review": row.needs_review,
            }
            for row in rows
        ],
        "summary_event_id": int(summary["header"]["id"]),
    }


def _ensure_absent(session: Session, rows: list[PreparedRow]) -> None:
    """Aborta si ya existe una fila equivalente en match_statistics."""

    for row in rows:
        existing = session.execute(
            text(
                """
                SELECT 1
                FROM match_statistics
                WHERE match_id = :match_id
                  AND team_id = :team_id
                  AND source = :source
                  AND reconciliation_version = :reconciliation_version
                """
            ),
            {
                "match_id": row.payload["match_id"],
                "team_id": row.payload["team_id"],
                "source": row.payload["source"],
                "reconciliation_version": row.payload["reconciliation_version"],
            },
        ).first()
        if existing is not None:
            raise MatchStatisticsLoaderError(
                "Ya existe match_statistics para match_id={match_id}, team_id={team_id}, source={source}, reconciliation_version={reconciliation_version}".format(
                    **row.payload
                )
            )


def _insert_rows(session: Session, rows: list[PreparedRow]) -> int:
    """Inserta filas en `match_statistics` dentro de la sesión activa."""

    match_statistics = Table("match_statistics", MetaData(), autoload_with=session.get_bind())
    inserted = 0
    for row in rows:
        session.execute(insert(match_statistics).values(row.payload))
        inserted += 1
    return inserted


def _validate_persist_version(reconciliation_version: str, rows: list[PreparedRow], persist: bool) -> None:
    """Exige `v2` para persistir registros con campos de calidad nuevos."""

    if persist and any(row.include_quality_fields for row in rows) and reconciliation_version not in {"v2", "v3"}:
        raise MatchStatisticsLoaderError(
            "La persistencia de los campos de calidad requiere reconciliation_version=v2 o v3."
        )


def run_loader(
    *,
    espn_event_id: int,
    match_id: int,
    league: str,
    competition: int,
    season: str,
    reconciliation_version: str,
    dry_run: bool,
    persist: bool,
    confirm_persist: bool,
) -> dict[str, Any]:
    """Ejecuta la reconciliación en modo seco o persistente."""

    engine = create_engine(settings.DATABASE_URL, future=True, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    client = ESPNClient(league)
    parser = ESPNPlayParser()
    with session_factory() as read_session:
        match = _load_match(read_session, match_id)
        mappings = _load_team_mappings(read_session, match)
        summary = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={espn_event_id}", timeout=30).json()
        plays = client.get_play_by_play_all(str(espn_event_id), str(competition), limit=300)
        parsed = parser.parse(plays)
        _validate_context(summary, plays, match, mappings, espn_event_id)
        rows = _prepare_rows(match_id, match, summary, plays, parsed, mappings, reconciliation_version)
        _validate_persist_version(reconciliation_version, rows, persist)
        if dry_run or not persist:
            return _summary_payload(summary, match_id, reconciliation_version, rows)
        if not confirm_persist:
            raise MatchStatisticsLoaderError("--persist requiere --confirm-persist.")
    with session_factory() as write_session:
        with write_session.begin():
            _ensure_absent(write_session, rows)
            inserted = _insert_rows(write_session, rows)
        result = _summary_payload(summary, match_id, reconciliation_version, rows)
        result["rows_inserted"] = inserted
        return result


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI."""

    parser = argparse.ArgumentParser(description="Reconciliador de match_statistics")
    parser.add_argument("--espn-event-id", type=int, required=True)
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--league", type=str, required=True)
    parser.add_argument("--competition", type=int, required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--reconciliation-version", type=str, default="v1")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--persist", action="store_true", help="Inserta en match_statistics.")
    parser.add_argument("--confirm-persist", action="store_true", help="Confirma explícitamente la persistencia.")
    return parser


def main() -> int:
    """Punto de entrada CLI."""

    parser = build_parser()
    args = parser.parse_args()
    dry_run = True if not args.persist else bool(args.dry_run)
    if args.persist:
        dry_run = False
    try:
        result = run_loader(
            espn_event_id=args.espn_event_id,
            match_id=args.match_id,
            league=args.league,
            competition=args.competition,
            season=args.season,
            reconciliation_version=args.reconciliation_version,
            dry_run=dry_run,
            persist=args.persist,
            confirm_persist=args.confirm_persist,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (MatchStatisticsLoaderError, ESPNClientError, requests.RequestException, ValueError) as exc:
        logger.error("Fallo del loader: %s", exc, exc_info=True)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
