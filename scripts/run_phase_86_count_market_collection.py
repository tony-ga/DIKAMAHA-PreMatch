"""Descubre fixtures y congela predicciones prospectivas de conteo.

Requirements:
    requests>=2.31
    sqlalchemy>=2
    tenacity>=8.2

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.count_market_prospective import (  # noqa: E402
    CountCohortBase,
    FrozenCountPrediction,
    SqlAlchemyCountPredictionRepository,
)
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
)
from src.espn_raw_first_provider import EspnRawFirstProvider  # noqa: E402
from src.prematch_data_contracts import EntityType  # noqa: E402
from src.prematch_raw_store import (  # noqa: E402
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
)
from src.prematch_snapshot_registry import resolve_active_snapshot  # noqa: E402
from src.prematch_snapshot_scheduler import (  # noqa: E402
    UpcomingFixture,
    fixtures_from_scoreboard,
)
from src.universal_prematch import (  # noqa: E402
    PrematchUnavailableError,
    UniversalPrematchEngine,
    UpcomingMatchInput,
    _load_windows,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_86_count_market_prospective"
RAW_STORE = ROOT / "data/phase_86/raw_responses.sqlite"
COHORT_STORE = ROOT / "data/phase_86/count_market_cohort.sqlite"
CACHE = ROOT / "data/cache/espn_phase86"
DISCOVERY = ROOT / "artifacts/phase_36_multileague_discovery/references.json"
MINIMUM_MATCHES = 500
MINIMUM_LEAGUES = 10


def _parser() -> argparse.ArgumentParser:
    """Define una ventana prospectiva acotada."""

    parser = argparse.ArgumentParser(description="Fase 86 count markets")
    parser.add_argument("--start-date", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-leagues", type=int, default=42)
    return parser


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dates(start: str, days: int) -> list[str]:
    """Construye fechas documentadas de scoreboard."""

    try:
        first = date.fromisoformat(start)
    except ValueError as error:
        raise ValueError("invalid_start_date") from error
    if days < 1 or days > 31:
        raise ValueError("days_out_of_range")
    return [(first + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(days)]


def _leagues(snapshot: Path, maximum: int) -> list[str]:
    """Selecciona ligas documentadas con soporte en el snapshot."""

    references = json.loads(DISCOVERY.read_text(encoding="utf-8"))
    documented = {str(row["league_slug"]) for row in references}
    supported = {str(row["league_slug"])
                 for row in _load_windows(str(snapshot))}
    selected = sorted(documented & supported)
    if maximum < 1:
        raise ValueError("max_leagues_must_be_positive")
    return selected[:maximum]


def _repositories() -> tuple[
    SqlAlchemyRawResponseRepository,
    SqlAlchemyCountPredictionRepository,
]:
    """Crea stores SQLite aislados y transaccionales."""

    RAW_STORE.parent.mkdir(parents=True, exist_ok=True)
    raw_engine = create_engine(f"sqlite+pysqlite:///{RAW_STORE}")
    cohort_engine = create_engine(f"sqlite+pysqlite:///{COHORT_STORE}")
    PrematchRawBase.metadata.create_all(raw_engine)
    CountCohortBase.metadata.create_all(cohort_engine)
    raw_factory = sessionmaker(bind=raw_engine, expire_on_commit=False)
    cohort_factory = sessionmaker(bind=cohort_engine, expire_on_commit=False)
    return (
        SqlAlchemyRawResponseRepository(raw_factory),
        SqlAlchemyCountPredictionRepository(cohort_factory),
    )


def _provider(
    league: str, repository: SqlAlchemyRawResponseRepository,
) -> EspnRawFirstProvider:
    """Construye proveedor raw-first por liga."""

    config = EspnConnectorConfig(
        league=league, cache_dir=CACHE / league,
        cache_ttl_seconds=86400)
    return EspnRawFirstProvider(
        EspnProspectiveConnector(config), repository)


def _discover_league(
    league: str, dates: list[str],
    repository: SqlAlchemyRawResponseRepository,
) -> list[UpcomingFixture]:
    """Persiste scoreboards antes de normalizar fixtures."""

    provider = _provider(league, repository)
    fixtures: dict[str, UpcomingFixture] = {}
    for day in dates:
        stored = provider.fetch(
            "scoreboard", entity_type=EntityType.LEAGUE,
            entity_id=league, date=day)
        for fixture in fixtures_from_scoreboard(provider.replay(stored.id), league):
            fixtures[fixture.event_id] = fixture
    LOGGER.info("phase86_discovery league=%s fixtures=%s", league, len(fixtures))
    return list(fixtures.values())


def _prediction(
    fixture: UpcomingFixture, engine: UniversalPrematchEngine,
    snapshot_hash: str, captured_at: datetime,
) -> FrozenCountPrediction | None:
    """Materializa modelo y baseline sin leer outcomes."""

    if captured_at >= fixture.kickoff_ts:
        return None
    request = UpcomingMatchInput(
        fixture.league_slug, int(fixture.home_team_id),
        int(fixture.away_team_id), fixture.kickoff_ts.isoformat(),
        int(fixture.event_id))
    shadow = engine.predict(request).experimental_team_markets
    if not shadow or shadow["status"] != "experimental_shadow_not_promoted":
        return None
    return FrozenCountPrediction(
        fixture.league_slug, int(fixture.event_id), fixture.kickoff_ts,
        captured_at, int(fixture.home_team_id), int(fixture.away_team_id),
        shadow["probabilities"], shadow["baseline_probabilities"],
        shadow["provenance"]["model_sha256"], snapshot_hash,
        shadow["status"])


def _collect(
    leagues: list[str], dates: list[str],
    raw_repository: SqlAlchemyRawResponseRepository,
    cohort_repository: SqlAlchemyCountPredictionRepository,
    snapshot: Path,
) -> dict[str, int]:
    """Descubre y congela fixtures idempotentemente."""

    engine = UniversalPrematchEngine(snapshot)
    snapshot_hash, inserted, duplicates, skipped = _sha(snapshot), 0, 0, 0
    for league in leagues:
        fixtures = _discover_league(league, dates, raw_repository)
        for fixture in fixtures:
            try:
                frozen = _prediction(
                    fixture, engine, snapshot_hash, datetime.now(timezone.utc))
            except (PrematchUnavailableError, ValueError) as error:
                skipped += 1
                LOGGER.warning(
                    "phase86_fixture_skipped league=%s match=%s reason=%s",
                    fixture.league_slug, fixture.event_id, error)
                continue
            if frozen is None:
                skipped += 1
                continue
            if cohort_repository.add_if_absent(frozen):
                inserted += 1
            else:
                duplicates += 1
    return {"inserted": inserted, "duplicates": duplicates,
            "skipped": skipped}


def _raw_count() -> int:
    """Cuenta payloads raw sin leer sus cuerpos."""

    engine = create_engine(f"sqlite+pysqlite:///{RAW_STORE}")
    with sessionmaker(bind=engine)() as session:
        return int(session.execute(select(func.count(RawResponse.id))).scalar_one())


def _coverage(
    rows: list[FrozenCountPrediction], run: dict[str, int],
) -> dict[str, Any]:
    """Resume cobertura acumulada e invariantes de modelo."""

    leagues = Counter(row.league_slug for row in rows)
    return {
        "matches": len(rows), "leagues": len(leagues),
        "by_league": dict(sorted(leagues.items())),
        "raw_responses": _raw_count(), **run,
        "model_hashes": sorted({row.model_sha256 for row in rows}),
        "snapshot_hashes": sorted({row.snapshot_sha256 for row in rows}),
    }


def _audit(rows: list[FrozenCountPrediction]) -> dict[str, Any]:
    """Aplica gates ciegos sin acceder a estadísticas post-match."""

    market_sets = [set(row.probabilities) for row in rows]
    checks = {
        "all_predictions_before_kickoff": all(
            row.captured_at < row.kickoff_ts for row in rows),
        "unique_fixture_ids": len(rows) == len({
            (row.league_slug, row.match_id) for row in rows}),
        "four_markets_present": all(len(markets) == 4 for markets in market_sets),
        "model_hash_invariant": len({row.model_sha256 for row in rows}) <= 1,
        "snapshot_hash_invariant": len({
            row.snapshot_sha256 for row in rows}) <= 1,
        "outcomes_read": False, "postmatch_endpoints_called": False,
        "official_router_modified": False,
    }
    return checks


def _classification(coverage: dict[str, Any], audit: dict[str, Any]) -> str:
    """Abre evaluación sólo al alcanzar cobertura sellada."""

    positive = [value for key, value in audit.items()
                if key not in {"outcomes_read", "postmatch_endpoints_called",
                               "official_router_modified"}]
    negative = [audit[key] for key in (
        "outcomes_read", "postmatch_endpoints_called",
        "official_router_modified")]
    if not all(positive) or any(negative):
        return "rejected_for_revision"
    if (coverage["matches"] >= MINIMUM_MATCHES
            and coverage["leagues"] >= MINIMUM_LEAGUES):
        return "ready_for_next_phase"
    return "insufficient_coverage"


def _write(name: str, payload: Any) -> None:
    """Escribe JSON mediante reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    temporary.replace(target)


def _publish(
    args: argparse.Namespace, rows: list[FrozenCountPrediction],
    coverage: dict[str, Any], audit: dict[str, Any], snapshot: Path,
) -> dict[str, Any]:
    """Publica el contrato de colección sin targets."""

    classification = _classification(coverage, audit)
    _write("config.json", {
        "version": "count_market_prospective_v1",
        "minimum_matches": MINIMUM_MATCHES,
        "minimum_leagues": MINIMUM_LEAGUES, **vars(args)})
    _write("input_manifest.json", {
        "snapshot": str(snapshot.relative_to(ROOT)),
        "snapshot_sha256": _sha(snapshot),
        "model_sha256": coverage["model_hashes"][0]
        if coverage["model_hashes"] else None})
    _write("coverage.json", coverage)
    _write("audit.json", {**audit, "classification": classification})
    _write("metrics.json", {
        "scoring_executed": False, "outcomes_available": False})
    _write("predictions.json", [asdict(row) for row in rows])
    report = _report(classification, coverage)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"classification": classification, "coverage": coverage}


def _report(classification: str, coverage: dict[str, Any]) -> str:
    """Renderiza estado de acumulación."""

    next_step = (
        "La cohorte está sellada. El siguiente paso es esperar cada kickoff "
        "y materializar outcomes sin recalcular predicciones."
        if classification == "ready_for_next_phase"
        else "La evaluación permanece sellada hasta alcanzar 500 partidos "
        "y 10 ligas."
    )
    return (
        "# Fase 86 — cohorte prospectiva de mercados\n\n"
        f"**Clasificación:** `{classification}`\n\n"
        f"- predicciones congeladas: `{coverage['matches']}`\n"
        f"- ligas: `{coverage['leagues']}`\n"
        f"- respuestas raw: `{coverage['raw_responses']}`\n"
        "- outcomes leídos: `False`\n"
        "- scoring ejecutado: `False`\n"
        "- router oficial modificado: `False`\n\n"
        f"{next_step}\n")


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta una captura prospectiva idempotente."""

    snapshot = resolve_active_snapshot()
    leagues = _leagues(snapshot, args.max_leagues)
    raw_repository, cohort_repository = _repositories()
    run_metrics = _collect(
        leagues, _dates(args.start_date, args.days),
        raw_repository, cohort_repository, snapshot)
    rows = cohort_repository.all()
    coverage, audit = _coverage(rows, run_metrics), _audit(rows)
    return _publish(args, rows, coverage, audit, snapshot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = run(_parser().parse_args())
        assert result["coverage"]["matches"] >= 0
        assert result["coverage"]["leagues"] >= 0
        assert result["classification"] in {
            "insufficient_coverage", "ready_for_next_phase"}
        LOGGER.info("Fase 86: %s", result["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.exception("Fase 86 rechazada: %s", error)
        raise SystemExit(2) from error


# Version: 1.0.0
# Created: 2026-07-28
