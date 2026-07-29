"""Congela una cohorte prospectiva para mercados Markov.

Requirements:
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.count_market_prospective import (  # noqa: E402
    CountCohortBase,
    FrozenCountPrediction,
    SqlAlchemyCountPredictionRepository,
)
from src.prematch_snapshot_registry import resolve_active_snapshot  # noqa: E402
from src.team_count_market_runtime import MARKOV_APPROVED_MARKETS  # noqa: E402
from src.universal_prematch import (  # noqa: E402
    PrematchUnavailableError,
    UniversalPrematchEngine,
    UpcomingMatchInput,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_90_markov_market_prospective"
SOURCE_STORE = ROOT / "data/phase_86/count_market_cohort.sqlite"
COHORT_STORE = ROOT / "data/phase_90/markov_market_cohort_v2.sqlite"
SOURCE_ARTIFACT = (
    ROOT / "artifacts/phase_86_count_market_prospective/predictions.json")
MINIMUM_MATCHES = 500
MINIMUM_LEAGUES = 10


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository(path: Path) -> SqlAlchemyCountPredictionRepository:
    """Crea un repositorio SQLite append-only."""

    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    CountCohortBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyCountPredictionRepository(factory)


def _freeze(
    source: FrozenCountPrediction, engine: UniversalPrematchEngine,
    snapshot_hash: str, captured_at: datetime,
) -> FrozenCountPrediction | None:
    """Emite las probabilidades aprobadas sin consultar el objetivo."""

    if captured_at >= source.kickoff_ts:
        return None
    request = UpcomingMatchInput(
        source.league_slug, source.home_team_id, source.away_team_id,
        source.kickoff_ts.isoformat(), source.match_id)
    shadow = engine.predict(request).experimental_team_markets
    if not shadow:
        return None
    markov = shadow["provenance"]["team_market_markov"]
    if markov.get("status") != "available":
        return None
    probabilities = _select(shadow["probabilities"])
    baselines = _select(shadow["baseline_probabilities"])
    return FrozenCountPrediction(
        source.league_slug, source.match_id, source.kickoff_ts, captured_at,
        source.home_team_id, source.away_team_id, probabilities, baselines,
        str(markov["model_sha256"]), snapshot_hash,
        "experimental_shadow_not_promoted")


def _select(values: dict[str, float]) -> dict[str, float]:
    """Conserva exactamente los mercados Markov congelados."""

    selected = {
        key: float(value) for key, value in values.items()
        if key in MARKOV_APPROVED_MARKETS}
    if set(selected) != MARKOV_APPROVED_MARKETS:
        raise ValueError("phase90_markov_market_contract_mismatch")
    return selected


def _collect(
    sources: list[FrozenCountPrediction],
    repository: SqlAlchemyCountPredictionRepository,
    snapshot: Path, captured_at: datetime,
) -> dict[str, int]:
    """Congela fixtures futuros de forma idempotente."""

    engine = UniversalPrematchEngine(snapshot)
    snapshot_hash, inserted, duplicates, skipped = _sha(snapshot), 0, 0, 0
    existing = {
        (row.league_slug, row.match_id) for row in repository.all()}
    for source in sources:
        if (source.league_slug, source.match_id) in existing:
            duplicates += 1
            continue
        try:
            prediction = _freeze(
                source, engine, snapshot_hash, captured_at)
        except (KeyError, PrematchUnavailableError, TypeError, ValueError) as error:
            skipped += 1
            LOGGER.warning(
                "phase90_skipped league=%s match=%s reason=%s",
                source.league_slug, source.match_id, error)
            continue
        if prediction is None:
            skipped += 1
        elif repository.add_if_absent(prediction):
            inserted += 1
        else:
            duplicates += 1
    return {"inserted": inserted, "duplicates": duplicates,
            "skipped": skipped}


def _coverage(
    rows: list[FrozenCountPrediction], run: dict[str, int],
) -> dict[str, Any]:
    """Resume soporte e invariantes de cohorte."""

    leagues = Counter(row.league_slug for row in rows)
    return {
        "matches": len(rows), "leagues": len(leagues),
        "by_league": dict(sorted(leagues.items())),
        "model_hashes": sorted({row.model_sha256 for row in rows}),
        "snapshot_hashes": sorted({row.snapshot_sha256 for row in rows}),
        "source_fixtures": len(rows) + run["skipped"],
        "excluded_before_lock": run["skipped"]}


def _audit(rows: list[FrozenCountPrediction]) -> dict[str, bool]:
    """Valida causalidad sin abrir outcomes."""

    return {
        "all_predictions_before_kickoff": all(
            row.captured_at < row.kickoff_ts for row in rows),
        "unique_fixture_ids": len(rows) == len({
            (row.league_slug, row.match_id) for row in rows}),
        "approved_markets_present": all(
            set(row.probabilities) == MARKOV_APPROVED_MARKETS
            and set(row.baseline_probabilities) == MARKOV_APPROVED_MARKETS
            for row in rows),
        "probabilities_valid": all(
            0.0 <= value <= 1.0 for row in rows
            for value in (*row.probabilities.values(),
                          *row.baseline_probabilities.values())),
        "model_hash_invariant": len({row.model_sha256 for row in rows}) <= 1,
        "snapshot_hash_invariant": len({
            row.snapshot_sha256 for row in rows}) <= 1,
        "outcomes_read": False,
        "postmatch_endpoints_called": False,
        "official_router_modified": False,
    }


def _classification(
    coverage: dict[str, Any], audit: dict[str, bool],
) -> str:
    """Clasifica la colección bajo el gate congelado."""

    forbidden = (
        audit["outcomes_read"] or audit["postmatch_endpoints_called"]
        or audit["official_router_modified"])
    positive = [
        value for key, value in audit.items()
        if key not in {
            "outcomes_read", "postmatch_endpoints_called",
            "official_router_modified"}]
    if forbidden or not all(positive):
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


def _report(classification: str, coverage: dict[str, Any]) -> str:
    """Renderiza el cierre de colección."""

    return (
        "# Fase 90 — cohorte prospectiva Markov por mercado\n\n"
        f"**Clasificación:** `{classification}`\n\n"
        f"- predicciones congeladas: `{coverage['matches']}`\n"
        f"- ligas: `{coverage['leagues']}`\n"
        f"- mercados por partido: `{len(MARKOV_APPROVED_MARKETS)}`\n"
        "- outcomes leídos: `False`\n"
        "- router oficial modificado: `False`\n")


def _publish(
    rows: list[FrozenCountPrediction], coverage: dict[str, Any],
    audit: dict[str, bool], snapshot: Path,
) -> dict[str, Any]:
    """Publica el contrato completo de Fase 90."""

    classification = _classification(coverage, audit)
    _write("config.json", {
        "version": "markov_market_prospective_v1",
        "minimum_matches": MINIMUM_MATCHES,
        "minimum_leagues": MINIMUM_LEAGUES,
        "markets": sorted(MARKOV_APPROVED_MARKETS)})
    _write("input_manifest.json", {
        "phase86_predictions_sha256": _sha(SOURCE_ARTIFACT),
        "active_snapshot_sha256": _sha(snapshot)})
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


def run(captured_at: datetime | None = None) -> dict[str, Any]:
    """Congela la cohorte desde el catálogo ciego de Fase 86."""

    source_repository = _repository(SOURCE_STORE)
    cohort_repository = _repository(COHORT_STORE)
    snapshot = resolve_active_snapshot()
    run_metrics = _collect(
        source_repository.all(), cohort_repository, snapshot,
        captured_at or datetime.now(timezone.utc))
    rows = cohort_repository.all()
    return _publish(rows, _coverage(rows, run_metrics), _audit(rows), snapshot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["classification"] in {
        "ready_for_next_phase", "insufficient_coverage"}
    LOGGER.info("Fase 90: %s", result["classification"])


# Version: 1.0.0
# Created: 2026-07-29
