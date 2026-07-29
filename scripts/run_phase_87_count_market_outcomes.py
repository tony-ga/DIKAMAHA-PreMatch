"""Materializa outcomes post-match sin modificar predicciones selladas.

Requirements:
    requests>=2.31
    sqlalchemy>=2
    tenacity>=8.2

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.count_market_outcomes import EspnCountOutcomeParser  # noqa: E402
from src.count_market_prospective import (  # noqa: E402
    CountCohortBase,
    FrozenCountOutcome,
    FrozenCountPrediction,
    SqlAlchemyCountOutcomeRepository,
    SqlAlchemyCountPredictionRepository,
)
from src.espn_prospective_connector import (  # noqa: E402
    EspnConnectorConfig,
    EspnProspectiveConnector,
)
from src.espn_raw_first_provider import EspnRawFirstProvider  # noqa: E402
from src.prematch_data_contracts import CaptureKind, EntityType  # noqa: E402
from src.prematch_raw_store import (  # noqa: E402
    PrematchRawBase,
    RawResponse,
    SqlAlchemyRawResponseRepository,
    canonical_hash,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_87_count_market_outcomes"
RAW_STORE = ROOT / "data/phase_86/raw_responses.sqlite"
COHORT_STORE = ROOT / "data/phase_86/count_market_cohort.sqlite"
CACHE = ROOT / "data/cache/espn_phase87"
SETTLEMENT_DELAY = timedelta(hours=3)


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repositories() -> tuple[Any, Any, Any]:
    """Abre los stores aislados con SQLAlchemy."""

    raw_engine = create_engine(f"sqlite+pysqlite:///{RAW_STORE}")
    cohort_engine = create_engine(f"sqlite+pysqlite:///{COHORT_STORE}")
    PrematchRawBase.metadata.create_all(raw_engine)
    CountCohortBase.metadata.create_all(cohort_engine)
    raw_factory = sessionmaker(bind=raw_engine, expire_on_commit=False)
    cohort_factory = sessionmaker(bind=cohort_engine, expire_on_commit=False)
    return (
        SqlAlchemyRawResponseRepository(raw_factory),
        SqlAlchemyCountPredictionRepository(cohort_factory),
        SqlAlchemyCountOutcomeRepository(cohort_factory),
    )


def _provider(
    league: str, repository: SqlAlchemyRawResponseRepository,
) -> EspnRawFirstProvider:
    """Construye transporte post-match raw-first."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(
        league=league, cache_dir=CACHE / league,
        cache_ttl_seconds=0))
    return EspnRawFirstProvider(connector, repository)


def _eligible(
    predictions: list[FrozenCountPrediction],
    outcomes: list[FrozenCountOutcome], now: datetime,
) -> list[FrozenCountPrediction]:
    """Selecciona fixtures ya asentables y aún sin outcome."""

    completed = {(row.league_slug, row.match_id) for row in outcomes}
    return [
        row for row in predictions
        if (row.league_slug, row.match_id) not in completed
        and now >= row.kickoff_ts + SETTLEMENT_DELAY
    ]


def _competition_id(summary: dict[str, Any]) -> str:
    """Extrae competition ID del mismo summary persistido."""

    competitions = (summary.get("header") or {}).get("competitions")
    competition = competitions[0] if isinstance(competitions, list) and competitions else None
    identifier = competition.get("id") if isinstance(competition, dict) else None
    if not str(identifier).isdigit():
        raise ValueError("outcome_competition_id_missing")
    return str(identifier)


def _capture(
    prediction: FrozenCountPrediction,
    raw_repository: SqlAlchemyRawResponseRepository,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persiste summary y plays paginados antes de retornarlos."""

    provider = _provider(prediction.league_slug, raw_repository)
    summary_row = provider.fetch(
        "summary", entity_type=EntityType.EVENT,
        entity_id=str(prediction.match_id),
        scope_event_id=str(prediction.match_id),
        capture_kind=CaptureKind.HISTORICAL_RECONSTRUCTION,
        event_id=str(prediction.match_id))
    summary = provider.replay(summary_row.id)
    plays_row = provider.fetch_plays(
        event_id=str(prediction.match_id),
        competition_id=_competition_id(summary),
        entity_type=EntityType.EVENT, entity_id=str(prediction.match_id))
    return summary, provider.replay(plays_row.id)


def _materialize(
    eligible: list[FrozenCountPrediction],
    raw_repository: SqlAlchemyRawResponseRepository,
    outcome_repository: SqlAlchemyCountOutcomeRepository,
) -> dict[str, Any]:
    """Captura y reconcilia outcomes elegibles individualmente."""

    parser, inserted, rejected = EspnCountOutcomeParser(), 0, []
    for prediction in eligible:
        try:
            summary, plays = _capture(prediction, raw_repository)
            outcome = parser.parse(
                prediction, summary, plays, datetime.now(timezone.utc))
            inserted += int(outcome_repository.add_if_absent(outcome))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            rejected.append({
                "league_slug": prediction.league_slug,
                "match_id": prediction.match_id, "reason": str(error)})
            LOGGER.warning(
                "phase87_outcome_rejected league=%s match=%s reason=%s",
                prediction.league_slug, prediction.match_id, error)
    return {"eligible": len(eligible), "inserted": inserted,
            "rejected": rejected}


def _prediction_hash(rows: list[FrozenCountPrediction]) -> str:
    """Hash canónico de las predicciones desde el store."""

    payload = []
    for row in rows:
        values = asdict(row)
        values["kickoff_ts"] = row.kickoff_ts.isoformat()
        values["captured_at"] = row.captured_at.isoformat()
        payload.append(values)
    return canonical_hash(payload)


def _raw_count() -> int:
    """Cuenta raws sin abrir sus payloads."""

    engine = create_engine(f"sqlite+pysqlite:///{RAW_STORE}")
    with sessionmaker(bind=engine)() as session:
        return int(session.execute(select(func.count(RawResponse.id))).scalar_one())


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
    predictions: list[FrozenCountPrediction],
    outcomes: list[FrozenCountOutcome], run: dict[str, Any],
    before_hash: str, after_hash: str,
) -> dict[str, Any]:
    """Publica cobertura y mantiene scoring bloqueado."""

    complete = len(outcomes) == len(predictions) and bool(predictions)
    classification = "ready_for_next_phase" if complete else "insufficient_coverage"
    coverage = {
        "predictions": len(predictions), "outcomes": len(outcomes),
        "pending": len(predictions) - len(outcomes),
        "raw_responses": _raw_count(), **run}
    audit = {
        "classification": classification,
        "prediction_hash_before": before_hash,
        "prediction_hash_after": after_hash,
        "predictions_unchanged": before_hash == after_hash,
        "post_kickoff_only": True, "raw_first": True,
        "scoring_executed": False, "official_router_modified": False}
    _write("config.json", {
        "version": "count_market_outcomes_v1",
        "settlement_delay_hours": 3})
    _write("input_manifest.json", {
        "phase_86_predictions_sha256": _sha(
            ROOT / "artifacts/phase_86_count_market_prospective/predictions.json")})
    _write("coverage.json", coverage)
    _write("audit.json", audit)
    _write("metrics.json", {"scoring_executed": False})
    _write("outcomes.json", [asdict(row) for row in outcomes])
    _write("rejections.json", run["rejected"])
    report = _report(classification, coverage)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"classification": classification, "coverage": coverage}


def _report(classification: str, coverage: dict[str, Any]) -> str:
    """Renderiza el estado de settlement."""

    return (
        "# Fase 87 — outcomes prospectivos\n\n"
        f"**Clasificación:** `{classification}`\n\n"
        f"- predicciones: `{coverage['predictions']}`\n"
        f"- outcomes reconciliados: `{coverage['outcomes']}`\n"
        f"- pendientes: `{coverage['pending']}`\n"
        f"- elegibles en esta corrida: `{coverage['eligible']}`\n"
        "- scoring ejecutado: `False`\n"
        "- predicciones recalculadas: `False`\n")


def run(now: datetime | None = None) -> dict[str, Any]:
    """Ejecuta settlement incremental con reloj inyectable."""

    raw_repository, prediction_repository, outcome_repository = _repositories()
    predictions = prediction_repository.all()
    before_hash = _prediction_hash(predictions)
    eligible = _eligible(
        predictions, outcome_repository.all(),
        now or datetime.now(timezone.utc))
    metrics = _materialize(eligible, raw_repository, outcome_repository)
    outcomes = outcome_repository.all()
    after_hash = _prediction_hash(prediction_repository.all())
    return _publish(
        predictions, outcomes, metrics, before_hash, after_hash)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        result = run()
        assert result["coverage"]["predictions"] == 523
        assert result["coverage"]["outcomes"] <= 523
        assert result["classification"] in {
            "insufficient_coverage", "ready_for_next_phase"}
        LOGGER.info("Fase 87: %s", result["classification"])
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.exception("Fase 87 falló: %s", error)
        raise SystemExit(2) from error


# Version: 1.0.0
# Created: 2026-07-28
