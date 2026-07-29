"""Runner de backfill histórico partido por partido.

Modo por defecto:
- `--dry-run`

Requisitos:
- sqlalchemy
- python-dotenv
- requests
- tenacity
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Optional

import requests
from sqlalchemy import MetaData, Table, insert, select, text, update
from sqlalchemy.orm import Session

from src.api.espn_client import ESPNClientError
from src.database.manager import DatabaseConnectionError, DatabaseManager, Match, Team
from src.match_statistics_loader import MatchStatisticsLoaderError, run_loader
from src.run_pipeline_step1 import run_pipeline

logger = logging.getLogger("backfill_runner")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

RUNNING_TIMEOUT_MINUTES = int(os.getenv("BACKFILL_RUNNING_TIMEOUT_MINUTES", "90"))
BACKFILL_CACHE_DIR = Path(os.getenv("BACKFILL_CACHE_DIR", "data/cache/backfill_runner"))
BACKFILL_CACHE_TTL_SECONDS = int(os.getenv("BACKFILL_CACHE_TTL_SECONDS", "86400"))
BACKFILL_HTTP_RETRIES = int(os.getenv("BACKFILL_HTTP_RETRIES", "3"))
BACKFILL_HTTP_BACKOFF_SECONDS = float(os.getenv("BACKFILL_HTTP_BACKOFF_SECONDS", "1.5"))
BACKFILL_CHECKPOINT_DIR = Path(os.getenv("BACKFILL_CHECKPOINT_DIR", "data/checkpoints/backfill_runner"))
BACKFILL_FINAL_ARTIFACT_DIR = Path(os.getenv("BACKFILL_FINAL_ARTIFACT_DIR", "data/artifacts/backfill_runner"))
BACKFILL_SIMULATE_FAILURE_EVENT_ID = os.getenv("BACKFILL_SIMULATE_FAILURE_EVENT_ID")


class BackfillRunnerError(RuntimeError):
    """Error base del runner de backfill."""


class BackfillSystemError(BackfillRunnerError):
    """Error sistémico que debe abortar siempre."""


class BackfillPartyError(BackfillRunnerError):
    """Error de un partido individual."""


@dataclass(slots=True)
class FixtureCandidate:
    """Candidato de fixture obtenido desde ESPN."""

    espn_event_id: int
    competition_id: int
    league: str
    season: str
    match_date: datetime
    home_espn_id: Optional[int]
    away_espn_id: Optional[int]
    home_name: Optional[str]
    away_name: Optional[str]
    status: str
    event_season_year: Optional[int]
    event_season_slug: Optional[str]
    season_match: bool = False
    season_reason: Optional[str] = None
    competition_season_slug: Optional[str] = None


@dataclass(slots=True)
class ResolvedFixture:
    """Fixture resuelto contra catálogo local."""

    decision: str
    match_id: Optional[int]
    home_team_id: int
    away_team_id: int
    home_espn_id: int
    away_espn_id: int
    home_name: str
    away_name: str
    match_date: datetime
    season: str
    status: str
    match_proposal: Optional[dict[str, Any]] = None
    existing_ingestion: bool = False
    existing_ingestion_status: Optional[str] = None
    existing_ingestion_version: Optional[str] = None
    existing_ingestion_run_id: Optional[int] = None


@dataclass(slots=True)
class FetchResult:
    """Resultado de una carga HTTP o de caché."""

    payload: dict[str, Any]
    cache_hit: bool
    cache_expired: bool
    source_url: str


@dataclass(slots=True)
class CheckpointState:
    """Estado serializable de una corrida autónoma."""

    run_id: str
    timestamp: str
    league: str
    season: str
    reconciliation_version: str
    fixture_index: int
    last_fixture: Optional[int]
    fixture_order_hash: str
    total_fixtures: int
    next_fixture_index: int
    event_ids_success: list[int]
    event_ids_skipped: list[int]
    event_ids_failed: list[int]
    statistics: dict[str, Any]
    postgres_counts: dict[str, int]
    checksum: str


@dataclass(slots=True)
class BackfillRunContext:
    """Contexto acumulado para una corrida de backfill."""

    run_id: str
    league: str
    season: str
    reconciliation_version: str
    processed: list[dict[str, Any]] = field(default_factory=list)
    success_ids: list[int] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)
    failed_ids: list[int] = field(default_factory=list)
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "processed": 0,
            "success": 0,
            "skipped_existing": 0,
            "skipped_checkpoint": 0,
            "existing_data_failed_run": 0,
            "retry_allowed": 0,
            "failed": 0,
            "by_cause": {},
        }
    )
    last_fixture: Optional[int] = None
    last_fixture_index: int = -1
    total_fixtures: int = 0
    fixture_order_hash: str = ""
    checkpoints_written: list[str] = field(default_factory=list)
    coverage_complete: bool = False
    postgres_counts_before: dict[str, int] = field(default_factory=dict)
    postgres_counts_after: dict[str, int] = field(default_factory=dict)


def _now_utc() -> datetime:
    """Devuelve el instante UTC actual."""

    return datetime.now(timezone.utc)


def _build_run_id(prefix: str, league: str, season: str, reconciliation_version: str) -> str:
    """Construye un identificador único de corrida."""

    suffix = uuid.uuid4().hex
    return f"{prefix}-{league}-{season}-{reconciliation_version}-{_now_utc().strftime('%Y%m%dT%H%M%S%fZ')}-{suffix}"


def _parse_iso(value: str) -> datetime:
    """Convierte una fecha ISO de ESPN a `datetime` UTC."""

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_season_years(season: str) -> tuple[Optional[int], Optional[int]]:
    """Extrae años de inicio/fin desde una temporada textual."""

    if season.isdigit():
        year = int(season)
        return year, year
    parts = season.split("-")
    if len(parts) == 2 and len(parts[0]) == 4 and parts[0].isdigit():
        start = int(parts[0])
        end_fragment = parts[1]
        end = int(f"{str(start)[:2]}{end_fragment}") if end_fragment.isdigit() and len(end_fragment) == 2 else None
        return start, end
    return None, None


def _season_bounds(season: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Calcula un intervalo aproximado para la temporada solicitada."""

    start_year, end_year = _parse_season_years(season)
    if start_year is None:
        return None, None
    start = datetime(start_year, 6, 1, tzinfo=timezone.utc)
    end = datetime((end_year or start_year), 6, 1, 6, 59, tzinfo=timezone.utc)
    return start, end


def _ensure_cache_dir() -> None:
    """Crea el directorio de caché local."""

    BACKFILL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_checkpoint_dir(checkpoint_dir: Path | None = None, artifact_dir: Path | None = None) -> None:
    """Crea el directorio local de checkpoints y artefactos."""

    (checkpoint_dir or BACKFILL_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
    (artifact_dir or BACKFILL_FINAL_ARTIFACT_DIR).mkdir(parents=True, exist_ok=True)


def _run_dir(base_dir: Path, run_id: str) -> Path:
    """Devuelve el subdirectorio aislado para una corrida."""

    return base_dir / run_id


def _atomic_json_dump(path: Path, payload: dict[str, Any]) -> None:
    """Escribe JSON de forma atómica usando archivo temporal."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    tmp_path.replace(path)


def _cache_path(url: str) -> Path:
    """Deriva la ruta de caché para una URL."""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return BACKFILL_CACHE_DIR / f"{digest}.json"


def _read_cache(url: str) -> tuple[Optional[dict[str, Any]], bool]:
    """Lee una caché local existente."""

    _ensure_cache_dir()
    path = _cache_path(url)
    if not path.exists():
        return None, False
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    payload = data.get("payload")
    fetched_at = data.get("_fetched_at")
    if not isinstance(fetched_at, str):
        return payload, False
    try:
        fetched_dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return payload, False
    expired = _now_utc() - fetched_dt > timedelta(seconds=BACKFILL_CACHE_TTL_SECONDS)
    if expired:
        logger.warning("Cache expirada para %s", url)
    return payload, expired


def _write_cache(url: str, payload: dict[str, Any]) -> None:
    """Persiste una respuesta exitosa en caché local."""

    _ensure_cache_dir()
    path = _cache_path(url)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"_fetched_at": _now_utc().isoformat(), "payload": payload}, handle, ensure_ascii=False)


def _canonical_json(payload: dict[str, Any]) -> str:
    """Serializa un dict con orden estable."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _stable_checksum(payload: dict[str, Any]) -> str:
    """Calcula checksum SHA-256 de un payload canónico."""

    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _checkpoint_fixtures_hash(fixtures: list[FixtureCandidate]) -> str:
    """Calcula un hash estable para el orden de fixtures."""

    payload = [{"espn_event_id": f.espn_event_id, "match_date": f.match_date.isoformat()} for f in fixtures]
    return _stable_checksum({"fixtures": payload})


def _count_postgres(engine: DatabaseManager | Any) -> dict[str, int]:
    """Cuenta tablas principales de PostgreSQL sin modificar datos."""

    with engine.SessionLocal() as session:
        row = session.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM matches) AS matches,
                    (SELECT COUNT(*) FROM ingestion_runs) AS ingestion_runs,
                    (SELECT COUNT(*) FROM raw_api_responses) AS raw_api_responses,
                    (SELECT COUNT(*) FROM events_ledger) AS events_ledger,
                    (SELECT COUNT(*) FROM events_timeline) AS events_timeline,
                    (SELECT COUNT(*) FROM match_statistics) AS match_statistics
                """
            )
        ).mappings().one()
        return {key: int(value) for key, value in dict(row).items()}


def _load_last_checkpoint(checkpoint_dir: Path | None = None) -> Optional[dict[str, Any]]:
    """Carga el checkpoint local más reciente si existe."""

    ckpt_dir = checkpoint_dir or BACKFILL_CHECKPOINT_DIR
    _ensure_checkpoint_dir(ckpt_dir, None)
    checkpoints = sorted(ckpt_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not checkpoints:
        return None
    with checkpoints[-1].open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise BackfillSystemError(f"Checkpoint inválido: {checkpoints[-1].name}")
    checksum = payload.get("checksum")
    expected = _stable_checksum({k: v for k, v in payload.items() if k != "checksum"})
    if checksum != expected:
        raise BackfillSystemError(f"Checkpoint corrupto: {checkpoints[-1].name}")
    return payload


def _write_checkpoint(context: BackfillRunContext, engine: DatabaseManager | Any, checkpoint_dir: Path | None = None) -> str:
    """Escribe un checkpoint local versionable."""

    ckpt_dir = _run_dir(checkpoint_dir or BACKFILL_CHECKPOINT_DIR, context.run_id)
    _ensure_checkpoint_dir(ckpt_dir, None)
    payload = {
        "run_id": context.run_id,
        "timestamp": _now_utc().isoformat(),
        "league": context.league,
        "season": context.season,
        "reconciliation_version": context.reconciliation_version,
        "fixture_index": context.last_fixture_index,
        "last_fixture": context.last_fixture,
        "fixture_order_hash": context.fixture_order_hash,
        "total_fixtures": context.total_fixtures,
        "next_fixture_index": context.last_fixture_index + 1,
        "event_ids_success": context.success_ids,
        "event_ids_skipped": context.skipped_ids,
        "event_ids_failed": context.failed_ids,
        "statistics": context.stats,
        "postgres_counts": _count_postgres(engine),
    }
    payload["checksum"] = _stable_checksum(payload)
    checkpoint_name = f"{context.run_id}_{len(context.checkpoints_written) + 1:04d}.json"
    path = ckpt_dir / checkpoint_name
    _atomic_json_dump(path, payload)
    context.checkpoints_written.append(str(path))
    return str(path)


def _write_final_artifact(context: BackfillRunContext, engine: DatabaseManager | Any, artifact_dir: Path | None = None) -> str:
    """Escribe el artefacto JSON final de la corrida."""

    art_dir = _run_dir(artifact_dir or BACKFILL_FINAL_ARTIFACT_DIR, context.run_id)
    _ensure_checkpoint_dir(None, art_dir)
    payload = {
        "run_id": context.run_id,
        "timestamp": _now_utc().isoformat(),
        "league": context.league,
        "season": context.season,
        "reconciliation_version": context.reconciliation_version,
        "fixture_index": context.last_fixture_index,
        "summary": context.stats,
        "event_ids_success": context.success_ids,
        "event_ids_skipped": context.skipped_ids,
        "event_ids_failed": context.failed_ids,
        "postgres_counts": _count_postgres(engine),
        "last_fixture": context.last_fixture,
        "fixture_order_hash": context.fixture_order_hash,
        "total_fixtures": context.total_fixtures,
        "next_fixture_index": context.last_fixture_index + 1,
        "checkpoints_written": context.checkpoints_written,
        "coverage_complete": context.coverage_complete,
        "postgres_counts_before": context.postgres_counts_before,
        "postgres_counts_after": context.postgres_counts_after,
    }
    payload["checksum"] = _stable_checksum(payload)
    filename = f"{context.run_id}_final.json"
    path = art_dir / filename
    _atomic_json_dump(path, payload)
    return str(path)


def _fetch_json(url: str, *, dry_run: bool, allow_stale_cache: bool) -> FetchResult:
    """Obtiene JSON con caché, reintentos y distinción de errores permanentes."""

    cached, expired = _read_cache(url)
    if dry_run and cached is not None:
        return FetchResult(payload=cached, cache_hit=True, cache_expired=expired, source_url=url)
    if cached is not None and not expired:
        return FetchResult(payload=cached, cache_hit=True, cache_expired=False, source_url=url)
    last_error: Optional[Exception] = None
    for attempt in range(1, BACKFILL_HTTP_RETRIES + 1):
        try:
            response = requests.get(url, timeout=30)
            if response.status_code in {400, 404}:
                raise BackfillPartyError(f"HTTP {response.status_code} al consultar {url}.")
            response.raise_for_status()
            payload = response.json()
            _write_cache(url, payload)
            return FetchResult(payload=payload, cache_hit=False, cache_expired=False, source_url=url)
        except BackfillPartyError:
            raise
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= BACKFILL_HTTP_RETRIES:
                break
            time.sleep(BACKFILL_HTTP_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        except ValueError as exc:
            last_error = exc
            break
    if cached is not None and expired and allow_stale_cache:
        logger.warning("Usando caché expirada para %s", url)
        return FetchResult(payload=cached, cache_hit=True, cache_expired=True, source_url=url)
    if last_error is None:
        raise BackfillSystemError(f"No se pudo obtener respuesta para {url}.")
    raise BackfillSystemError(f"Fallo al obtener {url}: {last_error}")


def _is_expired(heartbeat_at: Optional[datetime]) -> bool:
    """Indica si un heartbeat está expirado según la política operativa."""

    if heartbeat_at is None:
        return False
    return _now_utc() - heartbeat_at > timedelta(minutes=RUNNING_TIMEOUT_MINUTES)


def _load_season_calendar(league: str, season: str, *, dry_run: bool) -> FetchResult:
    """Carga el calendario histórico de una temporada específica."""

    season_year, _ = _parse_season_years(season)
    if season_year is None:
        raise BackfillSystemError(f"Temporada no reconocida: {season}")
    url = f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/seasons/{season_year}/types/1/calendar/ondays?lang=en&region=us"
    return _fetch_json(url, dry_run=dry_run, allow_stale_cache=dry_run)


def _load_fixtures(
    league: str,
    season: str,
    limit: Optional[int],
    *,
    dry_run: bool,
    continue_on_error: bool,
) -> dict[str, Any]:
    """Carga fixtures históricos consultando el calendario y el scoreboard por fecha."""

    calendar = _load_season_calendar(league, season, dry_run=dry_run)
    start, end = _season_bounds(season)
    if start is None or end is None:
        raise BackfillSystemError(f"No se pudo calcular el rango de temporada para {season}.")
    max_span = (end.date() - start.date()).days
    if max_span > 370:
        raise BackfillSystemError(f"Ventana de temporada inválida: {season}.")
    fixtures: list[FixtureCandidate] = []
    consulted_dates: list[str] = []
    cache_dates: list[str] = []
    ok_dates: list[str] = []
    failed_dates: list[dict[str, Any]] = []
    current = start.date()
    end_date = end.date()
    while current <= end_date:
        date_str = current.strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date_str}"
        consulted_dates.append(date_str)
        try:
            result = _fetch_json(url, dry_run=dry_run, allow_stale_cache=dry_run)
            if result.cache_hit:
                cache_dates.append(date_str)
            ok_dates.append(date_str)
            fixtures.extend(_candidate_from_event(league, season, event) for event in result.payload.get("events", []))
            if limit is not None and limit > 0 and len(fixtures) >= limit:
                break
        except BackfillPartyError as exc:
            failed_dates.append({"date": date_str, "error_code": "date_fetch_failed", "error": str(exc)})
            if not continue_on_error:
                raise
        current += timedelta(days=1)
    fixtures = [_annotate_season_match(candidate, season) for candidate in fixtures]
    fixtures.sort(key=lambda item: (item.match_date, item.espn_event_id))
    if not fixtures and failed_dates and not ok_dates:
        raise BackfillSystemError("Conectividad global fallida al consultar ESPN para la ventana solicitada.")
    return {
        "fixtures": fixtures,
        "consulted_dates": consulted_dates,
        "cache_dates": cache_dates,
        "ok_dates": ok_dates,
        "failed_dates": failed_dates,
        "calendar_cache_hit": calendar.cache_hit,
        "calendar_cache_expired": calendar.cache_expired,
    }


def _load_single_fixture(league: str, season: str, espn_event_id: int) -> list[FixtureCandidate]:
    """Carga un único fixture a partir de su `espn_event_id`."""

    result = _fetch_json(
        f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/events/{espn_event_id}",
        dry_run=False,
        allow_stale_cache=False,
    )
    payload = result.payload
    event_id = int(payload.get("id") or espn_event_id)
    if event_id != espn_event_id:
        raise BackfillSystemError(f"El evento ESPN no coincide con el ID solicitado: solicitado={espn_event_id} detectado={event_id}.")
    competitions = payload.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        raise BackfillSystemError(f"No se pudieron obtener competiciones para espn_event_id={espn_event_id}.")
    event_season = payload.get("season") or {}
    competition = competitions[0]
    competition_id = int(competition.get("id") or espn_event_id)
    candidate = FixtureCandidate(
        espn_event_id=espn_event_id,
        competition_id=competition_id,
        league=league,
        season=season,
        match_date=_parse_iso(str(competition["date"])),
        home_espn_id=_team_id_from_competitor(next((c for c in competition.get("competitors") or [] if c.get("homeAway") == "home"), {})),
        away_espn_id=_team_id_from_competitor(next((c for c in competition.get("competitors") or [] if c.get("homeAway") == "away"), {})),
        home_name=_team_name(next((c for c in competition.get("competitors") or [] if c.get("homeAway") == "home"), {})),
        away_name=_team_name(next((c for c in competition.get("competitors") or [] if c.get("homeAway") == "away"), {})),
        status=str((competition.get("status") or {}).get("type", {}).get("state") or "unknown"),
        event_season_year=int(event_season["year"]) if isinstance(event_season.get("year"), int) else None,
        event_season_slug=str(event_season.get("slug")) if isinstance(event_season.get("slug"), str) else None,
        competition_season_slug=str((competition.get("season") or event_season or {}).get("slug")) if isinstance((competition.get("season") or event_season or {}).get("slug"), str) else None,
    )
    return [_annotate_season_match(candidate, season)]


def _candidate_from_event(league: str, season: str, event: dict[str, Any]) -> FixtureCandidate:
    """Convierte un evento del scoreboard en candidato de backfill."""

    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    event_season = event.get("season") or {}
    competition_season = (competition.get("season") or event_season or {})
    return FixtureCandidate(
        espn_event_id=int(event["id"]),
        competition_id=int(competition["id"]),
        league=league,
        season=season,
        match_date=_parse_iso(str(competition["date"])),
        home_espn_id=_team_id_from_competitor(home),
        away_espn_id=_team_id_from_competitor(away),
        home_name=_team_name(home),
        away_name=_team_name(away),
        status=str((competition.get("status") or {}).get("type", {}).get("state") or "unknown"),
        event_season_year=int(event_season["year"]) if isinstance(event_season.get("year"), int) else None,
        event_season_slug=str(event_season.get("slug")) if isinstance(event_season.get("slug"), str) else None,
        competition_season_slug=str(competition_season.get("slug")) if isinstance(competition_season.get("slug"), str) else None,
    )


def _team_id_from_competitor(competitor: dict[str, Any]) -> Optional[int]:
    """Extrae el ID ESPN del competidor."""

    team = competitor.get("team") or {}
    ref = team.get("$ref")
    if isinstance(ref, str) and "/teams/" in ref:
        return int(ref.split("/teams/")[1].split("?")[0].split("/")[0])
    if isinstance(team.get("id"), str) and team["id"].isdigit():
        return int(team["id"])
    return None


def _team_name(competitor: dict[str, Any]) -> Optional[str]:
    """Extrae el nombre visible del competidor."""

    team = competitor.get("team") or {}
    for key in ("displayName", "name", "location"):
        value = team.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _load_team_map(session: Session) -> dict[int, Team]:
    """Carga el mapa ESPN -> equipo interno."""

    rows = session.execute(select(Team).where(Team.espn_team_id.isnot(None))).scalars().all()
    return {int(team.espn_team_id): team for team in rows if team.espn_team_id is not None}


def _match_by_identity(session: Session, candidate: FixtureCandidate) -> Optional[Match]:
    """Busca un match interno por identidad básica."""

    if candidate.home_espn_id is None or candidate.away_espn_id is None:
        return None
    team_map = _load_team_map(session)
    home = team_map.get(candidate.home_espn_id)
    away = team_map.get(candidate.away_espn_id)
    if home is None or away is None:
        return None
    stmt = (
        select(Match)
        .where(
            Match.home_team_id == home.id,
            Match.away_team_id == away.id,
            Match.match_date == candidate.match_date.replace(tzinfo=None),
        )
        .order_by(Match.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _build_match_proposal(candidate: FixtureCandidate, home: Team, away: Team) -> dict[str, Any]:
    """Construye la propuesta de `matches` para un fixture sin match existente."""

    return {
        "home_team_id": home.id,
        "away_team_id": away.id,
        "match_date": candidate.match_date.isoformat(),
        "season": candidate.season,
        "home_score": None,
        "away_score": None,
        "status": candidate.status,
    }


def _extract_summary_competition(summary: dict[str, Any]) -> dict[str, Any]:
    """Obtiene la competición principal desde el summary ESPN."""

    header = summary.get("header") or {}
    competitions = header.get("competitions") or summary.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        raise BackfillSystemError("No se pudo extraer la competición principal del summary ESPN.")
    return competitions[0]


def _extract_summary_scores(summary: dict[str, Any]) -> tuple[Optional[int], Optional[int], str]:
    """Extrae marcador y estado final desde el summary ESPN."""

    competition = _extract_summary_competition(summary)
    competitors = competition.get("competitors") or []
    home = next((item for item in competitors if item.get("homeAway") == "home"), {})
    away = next((item for item in competitors if item.get("homeAway") == "away"), {})
    home_score = home.get("score")
    away_score = away.get("score")
    status = str((competition.get("status") or {}).get("type", {}).get("state") or "post")
    return (
        int(home_score) if home_score not in (None, "") else None,
        int(away_score) if away_score not in (None, "") else None,
        status,
    )


def _load_resume_target(session: Session, run_id: int) -> dict[str, Any]:
    """Carga una corrida concreta para reanudación segura."""

    row = session.execute(
        text(
            """
            SELECT *
            FROM ingestion_runs
            WHERE id = :run_id
            """
        ),
        {"run_id": run_id},
    ).mappings().one()
    return dict(row)


def _checkpoint_processed_ids(checkpoint: Optional[dict[str, Any]]) -> set[int]:
    """Extrae los event IDs ya procesados desde un checkpoint."""

    processed: set[int] = set()
    if not checkpoint:
        return processed
    for key in ("event_ids_success", "event_ids_skipped", "event_ids_failed"):
        for value in checkpoint.get(key) or []:
            if isinstance(value, int):
                processed.add(value)
    return processed


def _record_cause(stats: dict[str, Any], cause: str) -> None:
    """Incrementa el contador de una causa de resultado."""

    by_cause = stats.setdefault("by_cause", {})
    by_cause[cause] = int(by_cause.get(cause, 0)) + 1


def _record_result(context: BackfillRunContext, decision: str, candidate: FixtureCandidate, error: Optional[str] = None) -> None:
    """Acumula el resultado de un fixture en memoria."""

    context.stats["processed"] = int(context.stats.get("processed", 0)) + 1
    context.last_fixture = candidate.espn_event_id
    if decision == "success":
        context.success_ids.append(candidate.espn_event_id)
        context.stats["success"] = int(context.stats.get("success", 0)) + 1
    elif decision == "existing_ingestion":
        context.skipped_ids.append(candidate.espn_event_id)
        context.stats["skipped_existing"] = int(context.stats.get("skipped_existing", 0)) + 1
    elif decision == "skipped_checkpoint":
        context.skipped_ids.append(candidate.espn_event_id)
        context.stats["skipped_checkpoint"] = int(context.stats.get("skipped_checkpoint", 0)) + 1
    elif decision == "existing_data_failed_run":
        context.skipped_ids.append(candidate.espn_event_id)
        context.stats["existing_data_failed_run"] = int(context.stats.get("existing_data_failed_run", 0)) + 1
    elif decision == "retry_allowed":
        context.stats["retry_allowed"] = int(context.stats.get("retry_allowed", 0)) + 1
    elif decision == "skipped":
        context.skipped_ids.append(candidate.espn_event_id)
        context.stats["skipped"] = int(context.stats.get("skipped", 0)) + 1
    else:
        context.failed_ids.append(candidate.espn_event_id)
        context.stats["failed"] = int(context.stats.get("failed", 0)) + 1
        if error:
            _record_cause(context.stats, error.split(":", 1)[0])


def _maybe_simulated_failure(candidate: FixtureCandidate) -> None:
    """Hook de prueba para simular un fallo aislado en dry-run."""

    if BACKFILL_SIMULATE_FAILURE_EVENT_ID and str(candidate.espn_event_id) == BACKFILL_SIMULATE_FAILURE_EVENT_ID:
        raise BackfillPartyError(f"simulated_party_failure: espn_event_id={candidate.espn_event_id}")


def _load_existing_ingestion(session: Session, espn_event_id: int) -> Optional[dict[str, Any]]:
    """Busca cualquier corrida previa del evento, sin importar la versión."""

    row = session.execute(
        text(
            """
            SELECT id, status, reconciliation_version, started_at, finished_at, heartbeat_at, match_id
            FROM ingestion_runs
            WHERE source = 'espn'
              AND espn_event_id = :espn_event_id
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"espn_event_id": espn_event_id},
    ).mappings().first()
    return dict(row) if row is not None else None


def _load_existing_data_state(session: Session, espn_event_id: int, match_id: Optional[int] = None) -> dict[str, Any]:
    """Carga el estado persistido asociado a un evento ESPN.

    Args:
        session: Sesión SQLAlchemy activa.
        espn_event_id: Identificador del evento ESPN.
        match_id: Identificador interno opcional del partido.

    Returns:
        Diccionario con la corrida previa y la presencia de capas persistidas.
    """

    ingestion = _load_existing_ingestion(session, espn_event_id)
    resolved_match_id = int(match_id) if match_id is not None else int(ingestion["match_id"]) if ingestion and ingestion.get("match_id") is not None else None
    has_raw = session.execute(
        text(
            """
            SELECT EXISTS(
                SELECT 1
                FROM raw_api_responses
                WHERE source = 'espn'
                  AND source_event_id = :espn_event_id
            )
            """
        ),
        {"espn_event_id": str(espn_event_id)},
    ).scalar_one()
    has_ledger = False
    has_timeline = False
    has_statistics = False
    if resolved_match_id is not None:
        has_ledger = bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM events_ledger
                        WHERE match_id = :match_id
                    )
                    """
                ),
                {"match_id": resolved_match_id},
            ).scalar_one()
        )
        has_timeline = bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM events_timeline
                        WHERE match_id = :match_id
                    )
                    """
                ),
                {"match_id": resolved_match_id},
            ).scalar_one()
        )
        has_statistics = bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM match_statistics
                        WHERE match_id = :match_id
                    )
                    """
                ),
                {"match_id": resolved_match_id},
            ).scalar_one()
        )
    return {
        "ingestion": ingestion,
        "match_id": resolved_match_id,
        "has_raw": bool(has_raw),
        "has_ledger": has_ledger,
        "has_timeline": has_timeline,
        "has_statistics": has_statistics,
        "has_any_data": bool(has_raw or has_ledger or has_timeline or has_statistics),
        "has_complete_data": bool(has_raw and has_ledger and has_timeline and has_statistics),
    }


def _create_match(
    session: Session,
    candidate: FixtureCandidate,
    home: Team,
    away: Team,
    *,
    home_score: Optional[int],
    away_score: Optional[int],
    status: str,
) -> Match:
    """Crea un partido nuevo a partir de un fixture validado."""

    match = Match(
        home_team_id=home.id,
        away_team_id=away.id,
        match_date=candidate.match_date.replace(tzinfo=None),
        season=candidate.season,
        home_score=home_score,
        away_score=away_score,
        status=status,
    )
    session.add(match)
    session.flush()
    return match


def _insert_ingestion_run(
    session: Session,
    *,
    candidate: FixtureCandidate,
    match_id: int,
    reconciliation_version: str,
) -> int:
    """Registra una corrida de ingesta en estado running."""

    ingestion_runs = Table("ingestion_runs", MetaData(), autoload_with=session.get_bind())
    result = session.execute(
        insert(ingestion_runs).values(
            source="espn",
            espn_event_id=candidate.espn_event_id,
            match_id=match_id,
            league=candidate.league,
            competition_id=candidate.competition_id,
            season=candidate.season,
            status="running",
            started_at=_now_utc(),
            heartbeat_at=_now_utc(),
            error_code=None,
            error_message=None,
            reconciliation_version=reconciliation_version,
            raw_items=0,
            ledger_events=0,
            timeline_events=0,
            statistics_rows=0,
        ).returning(ingestion_runs.c.id)
    )
    return int(result.scalar_one())


def _update_ingestion_run(
    session: Session,
    run_id: int,
    *,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_items: int = 0,
    ledger_events: int = 0,
    timeline_events: int = 0,
    statistics_rows: int = 0,
) -> None:
    """Actualiza el estado de una corrida de ingesta."""

    ingestion_runs = Table("ingestion_runs", MetaData(), autoload_with=session.get_bind())
    session.execute(
        update(ingestion_runs)
        .where(ingestion_runs.c.id == run_id)
        .values(
            status=status,
            finished_at=_now_utc() if status in {"success", "failed", "skipped"} else None,
            heartbeat_at=_now_utc(),
            error_code=error_code,
            error_message=error_message,
            raw_items=raw_items,
            ledger_events=ledger_events,
            timeline_events=timeline_events,
            statistics_rows=statistics_rows,
        )
    )


def _close_ingestion_run(
    session: Session,
    run_id: int,
    *,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    raw_items: int = 0,
    ledger_events: int = 0,
    timeline_events: int = 0,
    statistics_rows: int = 0,
) -> None:
    """Cierra una corrida de ingesta de forma explícita.

    Args:
        session: Sesión SQLAlchemy activa.
        run_id: Identificador de la corrida.
        status: Estado final a persistir.
        error_code: Código de error opcional.
        error_message: Mensaje de error opcional.
        raw_items: Número de respuestas crudas procesadas.
        ledger_events: Número de eventos del ledger procesados.
        timeline_events: Número de eventos de timeline procesados.
        statistics_rows: Número de filas de estadísticas persistidas.
    """

    _update_ingestion_run(
        session,
        run_id,
        status=status,
        error_code=error_code,
        error_message=error_message,
        raw_items=raw_items,
        ledger_events=ledger_events,
        timeline_events=timeline_events,
        statistics_rows=statistics_rows,
    )


def _resolve_candidate(session: Session, candidate: FixtureCandidate) -> ResolvedFixture:
    """Resuelve el fixture contra el catálogo local sin escribir en base de datos."""

    if candidate.home_espn_id is None or candidate.away_espn_id is None:
        raise BackfillPartyError("teams_mismatch: no se pudieron resolver equipos ESPN del fixture.")
    if not candidate.season_match:
        raise BackfillPartyError(
            f"season_mismatch: fecha={candidate.match_date.isoformat()} solicitada={candidate.season} detectada={candidate.event_season_slug or candidate.event_season_year}."
        )
    team_map = _load_team_map(session)
    home = team_map.get(candidate.home_espn_id)
    away = team_map.get(candidate.away_espn_id)
    if home is None or away is None:
        raise BackfillPartyError(
            f"teams_mismatch: falta mapping ESPN->teams.id para home={candidate.home_espn_id} away={candidate.away_espn_id}."
        )
    match = _match_by_identity(session, candidate)
    if match is None:
        return ResolvedFixture(
            decision="new_match_candidate",
            match_id=None,
            home_team_id=home.id,
            away_team_id=away.id,
            home_espn_id=candidate.home_espn_id,
            away_espn_id=candidate.away_espn_id,
            home_name=home.name,
            away_name=away.name,
            match_date=candidate.match_date,
            season=candidate.season,
            status=candidate.status,
            match_proposal=_build_match_proposal(candidate, home, away),
        )
    if candidate.match_date.replace(tzinfo=None) != match.match_date.replace(tzinfo=None):
        raise BackfillPartyError(
            f"identity_mismatch: fecha no coincide para espn_event_id={candidate.espn_event_id}."
        )
    if match.home_team_id != home.id or match.away_team_id != away.id:
        raise BackfillPartyError(
            f"identity_mismatch: equipos no coinciden para espn_event_id={candidate.espn_event_id}."
        )
    return ResolvedFixture(
        decision="existing_match",
        match_id=match.id,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_espn_id=candidate.home_espn_id,
        away_espn_id=candidate.away_espn_id,
        home_name=home.name,
        away_name=away.name,
        match_date=match.match_date.replace(tzinfo=timezone.utc),
        season=str(match.season),
        status=str(match.status),
        match_proposal=None,
    )


def _attach_existing_ingestion(session: Session, candidate: FixtureCandidate) -> ResolvedFixture:
    """Marca el fixture como ya ingerido cuando existe una corrida previa."""

    ingestion = _load_existing_ingestion(session, candidate.espn_event_id)
    if ingestion is None:
        raise BackfillPartyError("existing_ingestion no encontrado aunque se esperaba una corrida previa.")
    team_map = _load_team_map(session)
    home = team_map.get(candidate.home_espn_id or -1)
    away = team_map.get(candidate.away_espn_id or -1)
    if home is None or away is None:
        raise BackfillPartyError(
            f"teams_mismatch: falta mapping ESPN->teams.id para home={candidate.home_espn_id} away={candidate.away_espn_id}."
        )
    return ResolvedFixture(
        decision="existing_ingestion",
        match_id=int(ingestion["match_id"]) if ingestion.get("match_id") is not None else None,
        home_team_id=home.id,
        away_team_id=away.id,
        home_espn_id=int(candidate.home_espn_id),
        away_espn_id=int(candidate.away_espn_id),
        home_name=home.name,
        away_name=away.name,
        match_date=candidate.match_date,
        season=candidate.season,
        status=candidate.status,
        existing_ingestion=True,
        existing_ingestion_status=str(ingestion["status"]),
        existing_ingestion_version=str(ingestion["reconciliation_version"]) if ingestion.get("reconciliation_version") is not None else None,
        existing_ingestion_run_id=int(ingestion["id"]),
    )


def _classify_existing_event_state(
    session: Session,
    candidate: FixtureCandidate,
    *,
    retry_failed: bool,
) -> tuple[str, Optional[dict[str, Any]]]:
    """Clasifica el estado previo del evento antes de reingerirlo.

    Args:
        session: Sesión SQLAlchemy activa.
        candidate: Candidato actual.
        retry_failed: Indica si se permite reintentar estados fallidos.

    Returns:
        Tupla con la decisión y el estado cargado.
    """

    state = _load_existing_data_state(session, candidate.espn_event_id)
    ingestion = state["ingestion"]
    if ingestion is None and not state["has_any_data"]:
        return "new_match_candidate", state
    if ingestion is not None and str(ingestion["status"]) == "success":
        return "existing_ingestion", state
    if ingestion is not None and str(ingestion["status"]) == "failed":
        if retry_failed:
            return "retry_allowed", state
        return "existing_data_failed_run", state
    if state["has_any_data"] and not retry_failed:
        return "existing_data_failed_run", state
    if state["has_any_data"] and retry_failed:
        return "retry_allowed", state
    if ingestion is not None and str(ingestion["status"]) == "running":
        return "existing_ingestion", state
    return "new_match_candidate", state


def _validate_candidate_identity(candidate: FixtureCandidate, league: str, season: str, espn_event_id: Optional[int]) -> None:
    """Valida que el fixture pertenece al filtro solicitado."""

    if espn_event_id is not None and candidate.espn_event_id != espn_event_id:
        raise BackfillPartyError(
            f"identity_mismatch: evento ESPN detectado={candidate.espn_event_id} solicitado={espn_event_id}."
        )
    if candidate.league != league:
        raise BackfillPartyError(f"identity_mismatch: liga detectada={candidate.league} solicitada={league}.")
    if candidate.season != season:
        raise BackfillPartyError(f"identity_mismatch: temporada detectada={candidate.season} solicitada={season}.")
    if not candidate.season_match:
        raise BackfillPartyError(
            f"season_mismatch: fecha={candidate.match_date.isoformat()} solicitada={season} detectada={candidate.event_season_slug or candidate.event_season_year}."
        )
    if candidate.competition_id is None:
        raise BackfillPartyError("identity_mismatch: falta competition_id en el fixture ESPN.")
    if candidate.home_espn_id is None or candidate.away_espn_id is None:
        raise BackfillPartyError("teams_mismatch: no se pudieron resolver equipos ESPN del fixture.")


def _run_dry(
    session: Session,
    fixtures: list[FixtureCandidate],
    limit: int,
    offset: int = 0,
    *,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Construye un reporte de solo lectura."""

    reviewed = []
    for candidate in fixtures[:limit]:
        try:
            decision, state = _classify_existing_event_state(session, candidate, retry_failed=retry_failed)
            if decision == "existing_ingestion":
                validation = _attach_existing_ingestion(session, candidate)
            elif decision == "existing_data_failed_run":
                reviewed.append({**asdict(candidate), "decision": decision, "state": state})
                continue
            elif decision == "retry_allowed":
                reviewed.append({**asdict(candidate), "decision": decision, "state": state})
                continue
            else:
                validation = _resolve_candidate(session, candidate)
            reviewed.append(
                {
                    **asdict(candidate),
                    **asdict(validation),
                }
            )
        except BackfillPartyError as exc:
            reviewed.append({**asdict(candidate), "decision": "rejected", "error": str(exc)})
    return {
        "mode": "dry-run",
        "fixtures_found": len(fixtures),
        "offset_applied": offset,
        "fixtures_reviewed": len(reviewed),
        "fixtures": reviewed,
        "written": False,
    }


def _run_dry_autonomous(
    session: Session,
    fixtures: list[FixtureCandidate],
    limit: int,
    offset: int,
    *,
    context: BackfillRunContext,
    checkpoint_every: int,
    manager: DatabaseManager,
    checkpoint_dir: Path | None = None,
    artifact_dir: Path | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Construye un reporte de solo lectura con checkpoints automáticos."""

    reviewed: list[dict[str, Any]] = []
    context.total_fixtures = len(fixtures)
    context.fixture_order_hash = _checkpoint_fixtures_hash(fixtures)
    for index, candidate in enumerate(fixtures[:limit]):
        try:
            decision, state = _classify_existing_event_state(session, candidate, retry_failed=retry_failed)
            if decision == "existing_ingestion":
                validation = _attach_existing_ingestion(session, candidate)
                item = {**asdict(candidate), **asdict(validation), "decision": "existing_ingestion"}
                reviewed.append(item)
                _record_result(context, "existing_ingestion", candidate)
            elif decision == "existing_data_failed_run":
                item = {**asdict(candidate), "decision": decision, "state": state}
                reviewed.append(item)
                _record_result(context, decision, candidate)
            elif decision == "retry_allowed":
                item = {**asdict(candidate), "decision": decision, "state": state}
                reviewed.append(item)
                _record_result(context, decision, candidate)
            else:
                _maybe_simulated_failure(candidate)
                validation = _resolve_candidate(session, candidate)
                item = {**asdict(candidate), **asdict(validation)}
                reviewed.append(item)
                _record_result(context, "success" if validation.decision != "rejected" else "failed", candidate)
        except BackfillPartyError as exc:
            item = {**asdict(candidate), "decision": "rejected", "error": str(exc)}
            reviewed.append(item)
            _record_result(context, "failed", candidate, error=type(exc).__name__)
        context.last_fixture_index = index
        if checkpoint_every > 0 and context.stats["processed"] % checkpoint_every == 0:
            _write_checkpoint(context, manager, checkpoint_dir)
    if context.total_fixtures > 0 and not context.checkpoints_written:
        _write_checkpoint(context, manager, checkpoint_dir)
    context.coverage_complete = context.stats["processed"] >= context.total_fixtures and len(reviewed) == context.total_fixtures
    context.postgres_counts_before = _count_postgres(manager)
    context.postgres_counts_after = context.postgres_counts_before.copy()
    artifact_path = _write_final_artifact(context, manager, artifact_dir)
    return {
        "mode": "dry-run",
        "autonomous": True,
        "fixtures_found": len(fixtures),
        "offset_applied": offset,
        "fixtures_reviewed": len(reviewed),
        "fixtures": reviewed,
        "written": False,
        "checkpoints_written": context.checkpoints_written,
        "final_artifact_path": artifact_path,
        "summary": context.stats,
        "coverage_complete": context.coverage_complete,
        "postgres_counts_before": context.postgres_counts_before,
        "postgres_counts_after": context.postgres_counts_after,
    }


def _annotate_season_match(candidate: FixtureCandidate, season: str) -> FixtureCandidate:
    """Marca si el fixture pertenece a la temporada solicitada."""

    start, end = _season_bounds(season)
    within_bounds = start is not None and end is not None and start <= candidate.match_date <= end
    if candidate.event_season_year is not None and candidate.event_season_slug is not None:
        season_text = candidate.event_season_slug
        if season not in season_text and str(candidate.event_season_year) != season:
            within_bounds = False
    reason = None if within_bounds else f"date={candidate.match_date.isoformat()} requested={season} detected={candidate.event_season_slug or candidate.event_season_year}"
    data = asdict(candidate)
    data["season_match"] = within_bounds
    data["season_reason"] = reason
    return FixtureCandidate(**data)


def run_backfill(
    *,
    league: str,
    season: str,
    limit: int,
    offset: int,
    dry_run: bool,
    persist: bool,
    confirm_persist: bool,
    continue_on_error: bool,
    reconciliation_version: str,
    resume_from_run_id: Optional[int],
    espn_event_id: Optional[int],
    retry_failed: bool,
    upgrade_reconciliation: bool,
    autonomous: bool,
    checkpoint_every: int,
    checkpoint_dir: Optional[str],
    artifact_dir: Optional[str],
) -> dict[str, Any]:
    """Ejecuta el backfill histórico en modo seco o persistente."""

    manager = DatabaseManager()
    ckpt_dir = Path(checkpoint_dir) if checkpoint_dir else BACKFILL_CHECKPOINT_DIR
    art_dir = Path(artifact_dir) if artifact_dir else BACKFILL_FINAL_ARTIFACT_DIR
    last_checkpoint = _load_last_checkpoint(ckpt_dir) if autonomous else None
    if espn_event_id is not None:
        fixtures = _load_single_fixture(league, season, espn_event_id)
        fixture_meta = {
            "consulted_dates": [],
            "cache_dates": [],
            "ok_dates": [],
            "failed_dates": [],
            "calendar_cache_hit": False,
            "calendar_cache_expired": False,
        }
    else:
        fetch_limit = None if autonomous else limit + offset
        loaded = _load_fixtures(league, season, fetch_limit, dry_run=dry_run, continue_on_error=continue_on_error)
        fixtures = loaded["fixtures"]
        fixture_meta = {k: loaded[k] for k in loaded.keys() if k != "fixtures"}
    if offset < 0:
        raise BackfillSystemError("--offset no puede ser negativo.")
    candidate_pool = fixtures[offset:] if espn_event_id is None else fixtures
    with manager.SessionLocal() as session:
        counts_before = _count_postgres(manager)
        if resume_from_run_id is not None:
            run = _load_resume_target(session, resume_from_run_id)
            if str(run["status"]) == "success":
                raise BackfillSystemError("resume-from-run-id apunta a una corrida success; no se puede duplicar.")
            if str(run["status"]) == "running" and not _is_expired(run.get("heartbeat_at")):
                raise BackfillSystemError("resume-from-run-id apunta a una corrida running activa; rechazada.")
        if upgrade_reconciliation and persist:
            raise BackfillSystemError("--upgrade-reconciliation no debe combinarse con --persist en este flujo.")
        start_index = 0
        if autonomous and last_checkpoint is not None:
            checkpoint_next_index = int(last_checkpoint.get("next_fixture_index") or 0)
            if checkpoint_next_index < 0 or checkpoint_next_index > len(candidate_pool):
                raise BackfillSystemError("El checkpoint contiene next_fixture_index inválido.")
            processed_ids = _checkpoint_processed_ids(last_checkpoint)
            for idx in range(min(checkpoint_next_index, len(candidate_pool))):
                if candidate_pool[idx].espn_event_id not in processed_ids:
                    raise BackfillSystemError("El checkpoint no coincide con los fixtures procesados.")
            start_index = checkpoint_next_index
        selected = candidate_pool[start_index:] if autonomous else candidate_pool
        effective_offset = offset + start_index if autonomous else offset
        effective_limit = len(selected) if autonomous else limit
        if autonomous and last_checkpoint is not None:
            if int(last_checkpoint.get("total_fixtures") or len(candidate_pool)) != len(candidate_pool):
                raise BackfillSystemError("El número total de fixtures cambió desde el último checkpoint.")
            expected_hash = _checkpoint_fixtures_hash(candidate_pool)
            if str(last_checkpoint.get("fixture_order_hash") or "") != expected_hash:
                raise BackfillSystemError("El orden de fixtures cambió desde el último checkpoint.")
        if dry_run or not persist:
            if autonomous:
                context = BackfillRunContext(
                    run_id=_build_run_id("dryrun", league, season, reconciliation_version),
                    league=league,
                    season=season,
                    reconciliation_version=reconciliation_version,
                )
                if last_checkpoint is not None:
                    checkpoint_ids = _checkpoint_processed_ids(last_checkpoint)
                    context.stats["skipped_checkpoint"] = len(checkpoint_ids)
                    context.skipped_ids.extend(sorted(checkpoint_ids))
                result = _run_dry_autonomous(
                    session,
                    selected,
                    effective_limit,
                    offset=effective_offset,
                    context=context,
                    checkpoint_every=checkpoint_every,
                    manager=manager,
                    checkpoint_dir=ckpt_dir,
                    artifact_dir=art_dir,
                    retry_failed=retry_failed,
                )
            else:
                result = _run_dry(session, selected, limit, offset=effective_offset, retry_failed=retry_failed)
            if autonomous:
                result["postgres_counts_before"] = counts_before
                result["postgres_counts_after"] = _count_postgres(manager)
            else:
                result["postgres_counts_before"] = counts_before
                result["postgres_counts_after"] = counts_before
            result.update(fixture_meta)
            return result
        if not confirm_persist:
            raise BackfillSystemError("--persist requiere --confirm-persist.")
        context = BackfillRunContext(
            run_id=_build_run_id("persist", league, season, reconciliation_version),
            league=league,
            season=season,
            reconciliation_version=reconciliation_version,
        )
        context.total_fixtures = len(fixtures)
        context.fixture_order_hash = _checkpoint_fixtures_hash(fixtures)
        if last_checkpoint is not None:
            checkpoint_ids = _checkpoint_processed_ids(last_checkpoint)
            context.stats["skipped_checkpoint"] = len(checkpoint_ids)
            context.skipped_ids.extend(sorted(checkpoint_ids))
        results: list[dict[str, Any]] = []
        for index, candidate in enumerate(selected[:effective_limit], start=1):
            run_id: Optional[int] = None
            match_id: Optional[int] = None
            run_status: Optional[str] = None
            run_error_code: Optional[str] = None
            run_error_message: Optional[str] = None
            try:
                decision, state = _classify_existing_event_state(session, candidate, retry_failed=retry_failed)
                if decision == "existing_ingestion":
                    validation = _attach_existing_ingestion(session, candidate)
                    item = {**asdict(candidate), **asdict(validation), "decision": "existing_ingestion"}
                    results.append(item)
                    _record_result(context, "existing_ingestion", candidate)
                    continue
                if decision == "existing_data_failed_run":
                    error = f"existing_data_failed_run: espn_event_id={candidate.espn_event_id} run_id={state['ingestion']['id'] if state.get('ingestion') else 'n/a'}."
                    results.append({**asdict(candidate), "decision": decision, "error": error, "state": state})
                    _record_result(context, decision, candidate, error=error)
                    if not continue_on_error:
                        raise BackfillPartyError(error)
                    continue
                if decision == "retry_allowed":
                    results.append({**asdict(candidate), "decision": decision, "state": state})
                    _record_result(context, decision, candidate)
                    if state.get("has_any_data"):
                        raise BackfillPartyError(
                            f"retry_incompatible_existing_data: espn_event_id={candidate.espn_event_id} requiere una estrategia documentada antes de reingerir."
                        )
                _maybe_simulated_failure(candidate)
                _validate_candidate_identity(candidate, league, season, espn_event_id)
                validation = _resolve_candidate(session, candidate)
                summary = requests.get(
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={candidate.espn_event_id}",
                    timeout=30,
                ).json()
                home_score, away_score, summary_status = _extract_summary_scores(summary)
                with manager.SessionLocal() as write_session:
                    with write_session.begin():
                        if validation.match_id is None:
                            match = _create_match(
                                write_session,
                                candidate,
                                write_session.get(Team, validation.home_team_id),
                                write_session.get(Team, validation.away_team_id),
                                home_score=home_score,
                                away_score=away_score,
                                status=summary_status,
                            )
                            match_id = match.id
                            run_id = _insert_ingestion_run(
                                write_session,
                                candidate=candidate,
                                match_id=match.id,
                                reconciliation_version=reconciliation_version,
                            )
                        else:
                            match_id = validation.match_id
                            run_id = _insert_ingestion_run(
                                write_session,
                                candidate=candidate,
                                match_id=validation.match_id,
                                reconciliation_version=reconciliation_version,
                            )
                if run_id is None:
                    raise BackfillSystemError(
                        f"No se pudo crear ingestion_run para espn_event_id={candidate.espn_event_id}."
                    )
                pipeline_summary = run_pipeline(
                    espn_event_id=str(candidate.espn_event_id),
                    match_id=int(match_id),
                    league=league,
                    competition=str(candidate.competition_id),
                    season=season,
                    dry_run=False,
                    force_reingest=False,
                )
                loader_summary = run_loader(
                    espn_event_id=candidate.espn_event_id,
                    match_id=int(match_id),
                    league=league,
                    competition=candidate.competition_id,
                    season=season,
                    reconciliation_version=reconciliation_version,
                    dry_run=False,
                    persist=True,
                    confirm_persist=True,
                )
                with manager.SessionLocal() as write_session:
                    with write_session.begin():
                        _update_ingestion_run(
                            write_session,
                            int(run_id),
                            status="success",
                            raw_items=int(pipeline_summary.raw_items),
                            ledger_events=int(pipeline_summary.ledger_events),
                            timeline_events=int(pipeline_summary.timeline_events),
                            statistics_rows=int(loader_summary["rows_inserted"]),
                        )
                item = (
                    {
                        **asdict(candidate),
                        **asdict(validation),
                        "raw_items": int(pipeline_summary.raw_items),
                        "ledger_events": int(pipeline_summary.ledger_events),
                        "timeline_events": int(pipeline_summary.timeline_events),
                        "statistics_rows": int(loader_summary["rows_inserted"]),
                        "ingestion_run_id": run_id,
                        "decision": "success",
                    }
                )
                results.append(item)
                _record_result(context, "success", candidate)
                run_status = "success"
                if autonomous and checkpoint_every > 0 and context.stats["processed"] % checkpoint_every == 0:
                    _write_checkpoint(context, manager, ckpt_dir)
            except (BackfillPartyError, BackfillSystemError, MatchStatisticsLoaderError, ESPNClientError, requests.RequestException, DatabaseConnectionError, ValueError, Exception) as exc:
                run_error_code = type(exc).__name__
                run_error_message = str(exc)
                error_text = str(exc)
                results.append({**asdict(candidate), "decision": "failed", "error": error_text})
                _record_result(context, "failed", candidate, error=type(exc).__name__)
                if isinstance(exc, BackfillPartyError):
                    run_status = "failed"
                else:
                    run_status = "failed"
                if not continue_on_error:
                    raise
            finally:
                if run_id is not None and run_status != "success":
                    with manager.SessionLocal() as write_session:
                        with write_session.begin():
                            _close_ingestion_run(
                                write_session,
                                int(run_id),
                                status="failed",
                                error_code=run_error_code or "BackfillRunnerError",
                                error_message=run_error_message or "Corrida cerrada por error no recuperado.",
                            )
                    run_status = "failed"
        context.postgres_counts_before = counts_before
        context.postgres_counts_after = _count_postgres(manager)
        artifact_path = _write_final_artifact(context, manager, art_dir) if autonomous else None
        return {
            "mode": "persist",
            "autonomous": autonomous,
            "fixtures_found": len(selected[:effective_limit]),
            "offset_applied": effective_offset,
            "fixtures_reviewed": len(results),
            "fixtures": results,
            "checkpoints_written": context.checkpoints_written,
            "final_artifact_path": artifact_path,
            "summary": context.stats,
            "coverage_complete": context.stats["processed"] >= context.total_fixtures and len(results) == context.total_fixtures,
            **fixture_meta,
            "postgres_counts_before": context.postgres_counts_before,
            "postgres_counts_after": context.postgres_counts_after,
        }


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI."""

    parser = argparse.ArgumentParser(description="Backfill runner histórico")
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--espn-event-id", type=int)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--reconciliation-version", default="v1")
    parser.add_argument("--resume-from-run-id", type=int)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--confirm-persist", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--upgrade-reconciliation", action="store_true")
    parser.add_argument("--autonomous", action="store_true")
    parser.add_argument("--checkpoint-every", type=int, default=0)
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--artifact-dir")
    return parser


def main() -> int:
    """Punto de entrada CLI."""

    parser = build_parser()
    args = parser.parse_args()
    dry_run = True if not args.persist else bool(args.dry_run)
    if args.persist:
        dry_run = False
    try:
        result = run_backfill(
            league=args.league,
            season=args.season,
            limit=args.limit,
            offset=args.offset,
            dry_run=dry_run,
            persist=args.persist,
            confirm_persist=args.confirm_persist,
            continue_on_error=args.continue_on_error,
            reconciliation_version=args.reconciliation_version,
            resume_from_run_id=args.resume_from_run_id,
            espn_event_id=args.espn_event_id,
            retry_failed=args.retry_failed,
            upgrade_reconciliation=args.upgrade_reconciliation,
            autonomous=args.autonomous,
            checkpoint_every=args.checkpoint_every,
            checkpoint_dir=args.checkpoint_dir,
            artifact_dir=args.artifact_dir,
        )
        if args.autonomous and not bool(result.get("coverage_complete")):
            raise BackfillSystemError("Cobertura incompleta: la temporada solicitada no quedó totalmente recorrida.")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (BackfillRunnerError, ESPNClientError, requests.RequestException, DatabaseConnectionError, ValueError) as exc:
        logger.error("Fallo del backfill runner: %s", exc, exc_info=True)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
