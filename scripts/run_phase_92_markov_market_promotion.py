"""Ejecuta el gate prospectivo individual por mercado.

Requirements:
    numpy>=2
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.count_market_prospective import (  # noqa: E402
    CountCohortBase,
    FrozenCountOutcome,
    FrozenCountPrediction,
    SqlAlchemyCountOutcomeRepository,
    SqlAlchemyCountPredictionRepository,
)
from src.market_promotion import evaluate_markets  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_92_markov_market_promotion"
STORE = ROOT / "data/phase_90/markov_market_cohort_v2.sqlite"
OUTCOMES = ROOT / "artifacts/phase_91_markov_market_outcomes/outcomes.json"
BOOTSTRAP = 10_000


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repositories() -> tuple[Any, Any]:
    """Abre predicciones y outcomes en modo ORM."""

    engine = create_engine(f"sqlite+pysqlite:///{STORE}")
    CountCohortBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return (
        SqlAlchemyCountPredictionRepository(factory),
        SqlAlchemyCountOutcomeRepository(factory),
    )


def _join(
    predictions: list[FrozenCountPrediction],
    outcomes: list[FrozenCountOutcome],
) -> list[dict[str, Any]]:
    """Une 1:1 por identidad de fixture."""

    indexed = {
        (row.league_slug, row.match_id): row for row in outcomes}
    return [
        {
            "league_slug": row.league_slug, "match_id": row.match_id,
            "probabilities": row.probabilities,
            "baseline_probabilities": row.baseline_probabilities,
            "outcomes": indexed[(row.league_slug, row.match_id)].outcomes,
        }
        for row in predictions
        if (row.league_slug, row.match_id) in indexed]


def _write(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _report(
    classification: str, predictions: int, outcomes: int,
    approved: list[str],
) -> str:
    """Renderiza la decisión de promoción."""

    return (
        "# Fase 92 — gate de promoción Markov por mercado\n\n"
        f"**Clasificación:** `{classification}`\n\n"
        f"- predicciones: `{predictions}`\n"
        f"- outcomes: `{outcomes}`\n"
        f"- mercados aprobados: `{', '.join(approved) or 'ninguno'}`\n"
        f"- scoring ejecutado: `{predictions == outcomes and predictions > 0}`\n")


def run() -> dict[str, Any]:
    """Evalúa sólo cuando la cohorte está completa."""

    prediction_repository, outcome_repository = _repositories()
    predictions = prediction_repository.all()
    outcomes = outcome_repository.all()
    complete = len(predictions) == len(outcomes) and bool(predictions)
    joined = _join(predictions, outcomes)
    metrics = (
        evaluate_markets(joined, BOOTSTRAP) if complete else {
            "markets": {}, "approved_markets": []})
    classification = _classification(complete, metrics["approved_markets"])
    result = _publish(
        classification, predictions, outcomes, joined, metrics)
    return result


def _classification(complete: bool, approved: list[str]) -> str:
    """Clasifica cobertura y resultado del gate."""

    if not complete:
        return "insufficient_coverage"
    return "ready_for_next_phase" if approved else "rejected_for_revision"


def _publish(
    classification: str, predictions: list[FrozenCountPrediction],
    outcomes: list[FrozenCountOutcome], joined: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Publica evidencia de cobertura o evaluación."""

    _write("config.json", {
        "version": "markov_market_promotion_v1",
        "bootstrap_replicates": BOOTSTRAP,
        "minimum_league_matches": 30,
        "minimum_nonnegative_league_rate": 0.70})
    _write("input_manifest.json", {
        "phase91_outcomes_sha256": _sha(OUTCOMES)})
    _write("coverage.json", {
        "predictions": len(predictions), "outcomes": len(outcomes),
        "joined": len(joined)})
    _write("audit.json", {
        "classification": classification,
        "one_to_one_join": len(joined) == len(outcomes),
        "scoring_executed": len(predictions) == len(outcomes)
        and bool(predictions),
        "unit": "complete_match", "router_modified": False})
    _write("metrics.json", metrics)
    report = _report(
        classification, len(predictions), len(outcomes),
        metrics["approved_markets"])
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})
    return {"classification": classification, "metrics": metrics}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["classification"] in {
        "insufficient_coverage", "ready_for_next_phase",
        "rejected_for_revision"}
    LOGGER.info("Fase 92: %s", result["classification"])


# Version: 1.0.0
# Created: 2026-07-29
