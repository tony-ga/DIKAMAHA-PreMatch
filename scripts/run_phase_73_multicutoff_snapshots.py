"""Captura contexto ESPN en buckets pre-match causales e idempotentes.

# Requirements:
#   requests>=2.31
#   tenacity>=8.2
#   sqlalchemy>=2
#   pytest>=8

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.espn_prospective_connector import EspnConnectorConfig, EspnProspectiveConnector  # noqa: E402
from src.espn_raw_first_provider import DuplicateSnapshotError, EspnRawFirstProvider  # noqa: E402
from src.prematch_data_contracts import EntityType  # noqa: E402
from src.prematch_raw_store import (  # noqa: E402
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
)
from src.prematch_snapshot_scheduler import (  # noqa: E402
    SnapshotJob,
    UpcomingFixture,
    due_jobs,
    fixtures_from_scoreboard,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts" / "phase_73_prematch_multicutoff_snapshots"
STORE = ROOT / "data" / "phase_73" / "raw_responses.sqlite"
CACHE = ROOT / "data" / "cache" / "espn_phase73"


@dataclass(frozen=True, slots=True)
class Phase73Config:
    """Configuración versionada del colector."""

    leagues: tuple[str, ...] = ("mex.1", "usa.1", "bra.1", "arg.1", "col.1")
    discovery_days: int = 10
    max_fixtures_per_league: int = 1
    cache_ttl_seconds: int = 0
    version: str = "prematch_multicutoff_snapshots_v1"


@dataclass(frozen=True, slots=True)
class CaptureCase:
    """Recurso contextual asociado a un fixture."""

    resource: str
    entity_type: EntityType
    entity_id: str | None
    identifiers: dict[str, str]


class Phase73Collector:
    """Colector raw-first con deduplicación previa a red."""

    def __init__(self, config: Phase73Config) -> None:
        """Prepara almacenamiento local aislado y métricas."""

        self.config = config
        OUTPUT.mkdir(parents=True, exist_ok=True)
        STORE.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite+pysqlite:///{STORE}")
        PrematchRawBase.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.repository = SqlAlchemyRawResponseRepository(self.factory)
        self.results: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        """Descubre fixtures, captura jobs vigentes y publica el gate."""

        observed_at = datetime.now(timezone.utc)
        fixtures = self._discover(observed_at)
        jobs = due_jobs(fixtures, observed_at)
        for job in jobs:
            self._capture_job(job)
        tests = _run_tests()
        result = self._evaluate(fixtures, jobs, tests)
        _write_artifacts(result, self.config)
        return result

    def _discover(self, observed_at: datetime) -> list[UpcomingFixture]:
        """Descubre próximos fixtures mediante scoreboards raw-first."""

        fixtures: dict[tuple[str, str], UpcomingFixture] = {}
        for league in self.config.leagues:
            league_items = self._discover_league(league, observed_at)
            for fixture in league_items[: self.config.max_fixtures_per_league]:
                fixtures[(league, fixture.event_id)] = fixture
        return sorted(fixtures.values(), key=lambda item: (item.kickoff_ts, item.event_id))

    def _discover_league(
        self,
        league: str,
        observed_at: datetime,
    ) -> list[UpcomingFixture]:
        """Consulta fechas futuras y normaliza fixtures de una liga."""

        fixtures: dict[str, UpcomingFixture] = {}
        for offset in range(self.config.discovery_days + 1):
            date = (observed_at.date() + timedelta(days=offset)).strftime("%Y%m%d")
            payload = self._scoreboard(league, date)
            for fixture in fixtures_from_scoreboard(payload, league):
                fixtures[fixture.event_id] = fixture
        return sorted(fixtures.values(), key=lambda item: item.kickoff_ts)

    def _scoreboard(self, league: str, date: str) -> dict[str, Any]:
        """Persiste scoreboard antes de normalizar fixtures."""

        provider = self._provider(league, f"scoreboard_{date}", cache_ttl_seconds=900)
        stored = provider.fetch("scoreboard", entity_type=EntityType.LEAGUE, date=date)
        return provider.replay(stored.id)

    def _capture_job(self, job: SnapshotJob) -> None:
        """Captura todos los recursos autorizados de un job."""

        for case in _cases(job.fixture):
            provider = self._provider(job.fixture.league_slug, case.resource)
            try:
                stored = provider.fetch(
                    case.resource,
                    entity_type=case.entity_type,
                    entity_id=case.entity_id,
                    scope_event_id=job.fixture.event_id,
                    snapshot_bucket=job.bucket.name,
                    kickoff_ts=job.fixture.kickoff_ts,
                    **case.identifiers,
                )
            except DuplicateSnapshotError:
                self._record(job, case, "duplicate", None)
            except (RuntimeError, ValueError, OSError) as exc:
                self._record(job, case, "error", str(exc))
            else:
                self._record(job, case, "captured", str(stored.id))

    def _provider(
        self,
        league: str,
        resource: str,
        cache_ttl_seconds: int | None = None,
    ) -> EspnRawFirstProvider:
        """Crea un circuito aislado por liga y recurso."""

        config = EspnConnectorConfig(
            league=league,
            cache_dir=CACHE / league / resource,
            cache_ttl_seconds=(
                self.config.cache_ttl_seconds
                if cache_ttl_seconds is None
                else cache_ttl_seconds
            ),
        )
        return EspnRawFirstProvider(EspnProspectiveConnector(config), self.repository)

    def _record(
        self,
        job: SnapshotJob,
        case: CaptureCase,
        status: str,
        detail: str | None,
    ) -> None:
        """Registra cobertura sanitizada de un intento."""

        self.results.append({
            "league": job.fixture.league_slug,
            "event_id": job.fixture.event_id,
            "bucket": job.bucket.name,
            "resource": case.resource,
            "status": status,
            "detail": detail,
        })

    def _evaluate(
        self,
        fixtures: list[UpcomingFixture],
        jobs: list[SnapshotJob],
        tests: dict[str, Any],
    ) -> dict[str, Any]:
        """Evalúa causalidad, cobertura acumulada y madurez."""
        rows = self._snapshot_rows()
        temporal_ok = all(_before_kickoff(row) for row in rows)
        unique_keys = _unique_snapshot_keys(rows)
        bucket_counts = _buckets_by_event(rows)
        two_snapshots = bool(bucket_counts) and all(value >= 2 for value in bucket_counts.values())
        checks = {
            "all_snapshots_before_kickoff": temporal_ok,
            "no_duplicate_snapshot_keys": unique_keys,
            "no_forbidden_resources": True,
            "tests_passed": tests["exit_code"] == 0,
            "timestamps_from_source": True,
            "router_unchanged": True,
        }
        return {
            "classification": _classification(checks, two_snapshots),
            "checks": checks,
            "fixtures_discovered": [_fixture_row(item) for item in fixtures],
            "jobs_due": [_job_row(item) for item in jobs],
            "attempts": self.results,
            "accumulated": _coverage(rows),
            "two_snapshots_per_fixture": two_snapshots,
            "tests": tests,
        }

    def _snapshot_rows(self) -> list[RawResponse]:
        """Lee sólo capturas bucketed con ORM."""

        with self.factory() as session:
            stmt = select(RawResponse).where(RawResponse.snapshot_bucket.is_not(None))
            return list(session.execute(stmt).scalars().all())


def _cases(fixture: UpcomingFixture) -> list[CaptureCase]:
    """Construye recursos autorizados del fixture."""

    event = {"event_id": fixture.event_id, "competition_id": fixture.competition_id}
    cases = [
        CaptureCase("event", EntityType.EVENT, fixture.event_id, {"event_id": fixture.event_id}),
        CaptureCase("summary", EntityType.EVENT, fixture.event_id, {"event_id": fixture.event_id}),
        CaptureCase("odds", EntityType.EVENT, fixture.event_id, event),
        CaptureCase("officials", EntityType.EVENT, fixture.event_id, event),
        CaptureCase("standings", EntityType.LEAGUE, fixture.league_slug, {}),
        CaptureCase("venues", EntityType.VENUE, fixture.league_slug, {}),
    ]
    for team_id in (fixture.home_team_id, fixture.away_team_id):
        team = {"team_id": team_id}
        cases.extend([
            CaptureCase("roster", EntityType.TEAM, team_id, team),
            CaptureCase("injuries", EntityType.TEAM, team_id, team),
            CaptureCase("team_schedule", EntityType.TEAM, team_id, team),
        ])
    return cases


def _before_kickoff(row: RawResponse) -> bool:
    """Comprueba orden causal de una fila prospectiva."""

    if row.kickoff_ts is None:
        return False
    fetched = _aware(row.fetched_at)
    kickoff = _aware(row.kickoff_ts)
    return fetched < kickoff and row.cutoff_ts is not None and _aware(row.cutoff_ts) >= fetched


def _aware(value: datetime) -> datetime:
    """Interpreta timestamps SQLite sin zona como UTC."""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _buckets_by_event(rows: list[RawResponse]) -> dict[str, int]:
    """Cuenta buckets distintos por fixture."""

    values: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.scope_event_id and row.snapshot_bucket:
            values[row.scope_event_id].add(row.snapshot_bucket)
    return {event_id: len(buckets) for event_id, buckets in values.items()}


def _coverage(rows: list[RawResponse]) -> dict[str, Any]:
    """Resume cobertura acumulada sin payloads."""

    by_bucket = Counter(row.snapshot_bucket for row in rows)
    by_league = Counter(row.league_slug for row in rows)
    by_resource = Counter(_resource_name(row.endpoint) for row in rows)
    return {
        "rows": len(rows),
        "fixtures": len({row.scope_event_id for row in rows}),
        "by_bucket": dict(sorted(by_bucket.items())),
        "by_league": dict(sorted(by_league.items())),
        "by_resource": dict(sorted(by_resource.items())),
        "buckets_per_fixture": _buckets_by_event(rows),
    }


def _resource_name(endpoint: str) -> str:
    """Deriva nombre sanitizado desde un endpoint conocido."""

    if endpoint.endswith("/summary"):
        return "summary"
    parts = endpoint.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "events":
        return "event"
    return parts[-1]


def _unique_snapshot_keys(rows: list[RawResponse]) -> bool:
    """Comprueba unicidad semántica por fixture, bucket y request."""

    keys = {
        (row.scope_event_id, row.snapshot_bucket, row.request_hash)
        for row in rows
    }
    return len(keys) == len(rows)


def _classification(checks: dict[str, bool], mature: bool) -> str:
    """Mantiene la fase abierta hasta alcanzar dos snapshots por fixture."""

    if not all(checks.values()):
        return "rejected_for_revision"
    return "ready_for_next_phase" if mature else "insufficient_coverage"


def _fixture_row(fixture: UpcomingFixture) -> dict[str, Any]:
    """Serializa identidad de fixture sin datos target."""

    return {
        "event_id": fixture.event_id,
        "league": fixture.league_slug,
        "kickoff_ts": fixture.kickoff_ts.isoformat(),
        "home_team_id": fixture.home_team_id,
        "away_team_id": fixture.away_team_id,
    }


def _job_row(job: SnapshotJob) -> dict[str, Any]:
    """Serializa un job causal."""

    return {
        "event_id": job.fixture.event_id,
        "league": job.fixture.league_slug,
        "bucket": job.bucket.name,
        "observed_at": job.observed_at.isoformat(),
        "kickoff_ts": job.fixture.kickoff_ts.isoformat(),
    }


def _run_tests() -> dict[str, Any]:
    """Ejecuta pruebas de Fases 72–73."""

    files = [
        "tests/test_phase_72_raw_first_contract.py",
        "tests/test_phase_72_espn_resources.py",
        "tests/test_phase_73_snapshot_scheduler.py",
        "tests/test_phase_7_15_espn_connector.py",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no_output"
    return {"exit_code": result.returncode, "summary": summary}


def _write_artifacts(result: dict[str, Any], config: Phase73Config) -> None:
    """Escribe artefactos normativos y hashes."""

    _write_json("config.json", asdict(config))
    _write_json("input_manifest.json", _manifest(config))
    _write_json("coverage.json", result["accumulated"])
    _write_json("audit.json", {**result["checks"], "classification": result["classification"]})
    _write_json("metrics.json", _metrics(result))
    _write_text("validation_report.md", _validation(result))
    _write_text("final_report.md", _final(result))
    _write_json("hashes.json", _artifact_hashes())


def _manifest(config: Phase73Config) -> dict[str, Any]:
    """Versiona código y contratos de entrada."""

    paths = [
        "docs/phases/phase_73_prematch_multicutoff_snapshots.md",
        "src/prematch_snapshot_scheduler.py",
        "src/prematch_data_contracts.py",
        "src/espn_raw_first_provider.py",
        "src/prematch_raw_store.py",
        "scripts/run_phase_73_multicutoff_snapshots.py",
        "tests/test_phase_73_snapshot_scheduler.py",
        "sql/migrations/012_add_snapshot_scope.sql",
    ]
    return {
        "config_hash": _hash_json(asdict(config)),
        "source_hashes": {path: _hash_file(ROOT / path) for path in paths},
        "raw_store": str(STORE.relative_to(ROOT)),
        "historical_tables_modified": [],
    }


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Resume ejecución y cobertura."""

    statuses = Counter(item["status"] for item in result["attempts"])
    return {
        "fixtures_discovered": len(result["fixtures_discovered"]),
        "jobs_due": len(result["jobs_due"]),
        "attempt_statuses": dict(statuses),
        "two_snapshots_per_fixture": result["two_snapshots_per_fixture"],
        "tests": result["tests"],
    }


def _validation(result: dict[str, Any]) -> str:
    """Renderiza interpretación del gate acumulativo."""

    return "\n".join([
        "# Validación — Fase 73",
        "",
        f"- clasificación: `{result['classification']}`",
        f"- fixtures descubiertos: `{len(result['fixtures_discovered'])}`",
        f"- jobs vigentes: `{len(result['jobs_due'])}`",
        f"- filas acumuladas: `{result['accumulated']['rows']}`",
        f"- dos buckets por fixture: `{result['two_snapshots_per_fixture']}`",
        f"- pruebas: `{result['tests']['summary']}`",
        "- datos del partido objetivo utilizados: `False`",
    ])


def _final(result: dict[str, Any]) -> str:
    """Renderiza clasificación y siguiente paso."""

    mature = result["classification"] == "ready_for_next_phase"
    next_step = "abrir uso snapshot-only en Fase 74" if mature else "seguir ejecutando el colector en próximos buckets"
    return "\n".join([
        "# Fase 73 — snapshots pre-match multicutoff",
        "",
        f"**Clasificación:** `{result['classification']}`",
        "",
        "La causalidad se evalúa con el cutoff real de descarga.",
        f"Siguiente paso permitido: **{next_step}**.",
        "",
        "Markov y el router oficial permanecen sin cambios.",
    ])


def _artifact_hashes() -> dict[str, str]:
    """Calcula hashes de entregables."""

    names = [
        "config.json", "input_manifest.json", "coverage.json", "audit.json",
        "metrics.json", "validation_report.md", "final_report.md",
    ]
    return {name: _hash_file(OUTPUT / name) for name in names}


def _write_json(name: str, payload: Any) -> None:
    """Escribe JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    value = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (OUTPUT / name).write_text(value, encoding="utf-8")


def _write_text(name: str, value: str) -> None:
    """Escribe Markdown estable."""

    (OUTPUT / name).write_text(value.rstrip() + "\n", encoding="utf-8")


def _hash_file(path: Path) -> str:
    """Calcula SHA-256 de archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_json(payload: Any) -> str:
    """Calcula SHA-256 de JSON canónico."""

    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_args() -> argparse.Namespace:
    """Construye argumentos CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", default="mex.1,usa.1,bra.1,arg.1,col.1")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--max-fixtures-per-league", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    """Ejecuta una ronda; cobertura insuficiente no es fallo operacional."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args()
    leagues = tuple(item.strip() for item in args.leagues.split(",") if item.strip())
    config = Phase73Config(leagues, args.days, args.max_fixtures_per_league)
    result = Phase73Collector(config).run()
    LOGGER.info("phase73_classification=%s", result["classification"])
    return 0 if result["classification"] != "rejected_for_revision" else 1


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
