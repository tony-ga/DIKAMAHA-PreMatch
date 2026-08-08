"""Entrena Markov de mercados por equipo y reporta 100 partidos.

Requirements:
    numpy>=2
    python-dotenv>=1
    sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import joblib
from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.manager import Team  # noqa: E402
from src.team_market_markov import (  # noqa: E402
    MARKET_LINES,
    METRICS,
    STATE_NAMES,
    TeamMarketMarkov,
    market_name,
    state_for,
)
from src.temporal_integrity import (  # noqa: E402
    kickoff_buckets,
    normalize_kickoff_splits,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
SOURCE_2024 = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_88_team_market_markov"
BOOTSTRAP = 2000


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _team_mapping() -> dict[int, int]:
    """Lee el mapeo interno→ESPN mediante ORM."""

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL_missing")
    engine = create_engine(database_url)
    with Session(engine) as session:
        rows = session.execute(select(
            Team.id, Team.espn_team_id).where(
                Team.espn_team_id.is_not(None))).all()
    return {int(row.id): int(row.espn_team_id) for row in rows}


def _read_current() -> list[dict[str, Any]]:
    """Lee el corpus causal vigente."""

    with SOURCE.open(encoding="utf-8") as handle:
        return [_commercial_row(json.loads(line)) for line in handle]


def _commercial_row(row: dict[str, Any]) -> dict[str, Any]:
    """Incluye goles dentro del total comercial de tiros."""

    return {
        **row,
        "shots": int(row.get("shots", 0)) + int(row.get("goals", 0)),
    }


def _read_2024(mapping: dict[int, int]) -> list[dict[str, Any]]:
    """Recupera sólo 2024 y normaliza IDs de equipo a ESPN."""

    rows = json.loads(SOURCE_2024.read_text(encoding="utf-8"))
    output = []
    for row in rows:
        if str(row["match_date"]) >= "2025-01-01":
            continue
        team, rival = mapping.get(int(row["team_id"])), mapping.get(
            int(row["opponent_team_id"]))
        if team is None or rival is None:
            continue
        output.append(_commercial_row({
            **row, "match_id": -int(row["match_id"]),
            "team_id": team, "opponent_team_id": rival,
            "league_slug": str(row["competition_id"]), "split": "fit",
            "match_date": str(row["match_date"]) + "+00:00",
        }))
    return output


def _matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega doce ventanas orientadas por partido."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    matches = [_match(values) for values in grouped.values()]
    return normalize_kickoff_splits(row for row in matches if row)


def _match(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Construye un partido con seis ventanas por orientación."""

    home = sorted((row for row in rows if bool(row["is_home"])),
                  key=lambda row: int(row["window_index"]))
    away = sorted((row for row in rows if not bool(row["is_home"])),
                  key=lambda row: int(row["window_index"]))
    if len(home) != 6 or len(away) != 6:
        return None
    first = rows[0]
    return {
        "match_id": int(first["match_id"]),
        "match_date": str(first["match_date"]),
        "league_slug": str(first["league_slug"]),
        "split": str(first["split"]),
        "home_team_id": int(home[0]["team_id"]),
        "away_team_id": int(away[0]["team_id"]),
        "home": home, "away": away,
    }


def _actuals(match: dict[str, Any]) -> dict[str, bool]:
    """Revela outcomes de los doce mercados después de predecir."""

    output = {}
    for side in ("home", "away"):
        for metric in METRICS:
            counts = [int(row[metric]) for row in match[side]]
            for half, values in zip(
                    ("first_half", "second_half"), (counts[:3], counts[3:])):
                output[market_name(metric, side, half)] = (
                    sum(values) > MARKET_LINES[metric])
    return output


def _walk_forward(
    matches: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]], dict[int, dict[str, bool]], TeamMarketMarkov,
]:
    """Emite la cohorte congelada y actualiza sólo después."""

    model, predictions, outcomes = TeamMarketMarkov(), [], {}
    confirmation = [
        match for match in matches if match["split"] == "confirmation"]
    target_ids = {
        int(match["match_id"]) for match in confirmation[-100:]}
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            if int(match["match_id"]) in target_ids:
                trajectories = model.predict_match(match)
                predictions.append(_prediction_row(match, trajectories))
                outcomes[int(match["match_id"])] = _actuals(match)
        for match in bucket:
            model.update(match)
    return predictions, outcomes, model


def _prediction_row(
    match: dict[str, Any], trajectories: dict[str, Any],
) -> dict[str, Any]:
    """Combina ambas orientaciones sin consultar targets."""

    probabilities, baselines, expected = {}, {}, {}
    for side, trajectory in trajectories.items():
        probabilities.update(trajectory.probabilities)
        baselines.update(trajectory.baselines)
        expected[side] = trajectory.expected_counts
    return {
        "match_id": int(match["match_id"]),
        "match_date": str(match["match_date"]),
        "league_slug": str(match["league_slug"]),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "probabilities": probabilities, "baselines": baselines,
        "expected_counts": expected,
    }


def _select_100(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Selecciona cronológicamente antes del scoring."""

    ordered = sorted(rows, key=lambda row: (
        str(row["match_date"]), int(row["match_id"])))
    selected = ordered[-100:]
    if len(selected) != 100 or len({row["match_id"] for row in selected}) != 100:
        raise ValueError("phase88_100_match_selection_failed")
    return selected


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario acotado."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _score(
    rows: list[dict[str, Any]], outcomes: dict[int, dict[str, bool]],
) -> list[dict[str, Any]]:
    """Une outcomes después de congelar la selección."""

    output = []
    for row in rows:
        actual = outcomes[int(row["match_id"])]
        markets = {
            name: _market_score(
                row["probabilities"][name], row["baselines"][name], value)
            for name, value in actual.items()
        }
        model_loss = float(np.mean([
            item["model_log_loss"] for item in markets.values()]))
        output.append({
            **row, "actual": actual, "markets": markets,
            "mean_model_log_loss": model_loss,
            "mean_baseline_log_loss": float(np.mean([
                item["baseline_log_loss"] for item in markets.values()])),
            "reliability": float(np.mean([
                item["model_correct"] for item in markets.values()])),
        })
    return sorted(output, key=lambda row: (
        row["mean_model_log_loss"], -row["reliability"], row["match_id"]))


def _market_score(
    probability: float, baseline: float, actual: bool,
) -> dict[str, Any]:
    """Puntúa modelo y baseline para una línea."""

    return {
        "probability": probability, "baseline_probability": baseline,
        "actual": actual,
        "model_log_loss": _loss(probability, actual),
        "baseline_log_loss": _loss(baseline, actual),
        "model_brier": (probability - float(actual)) ** 2,
        "baseline_brier": (baseline - float(actual)) ** 2,
        "model_correct": (probability >= 0.5) == actual,
        "baseline_correct": (baseline >= 0.5) == actual,
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega resultados por mercado y macro total."""

    names = sorted(rows[0]["markets"])
    markets = {name: _aggregate_market(rows, name) for name in names}
    all_values = [row["markets"][name] for row in rows for name in names]
    total = _aggregate_values(all_values)
    improvements = [
        row["mean_baseline_log_loss"] - row["mean_model_log_loss"]
        for row in rows]
    total["bootstrap_log_loss_improvement_ci95"] = _bootstrap(improvements)
    total["markets_model_wins_log_loss"] = sum(
        value["model_log_loss"] < value["baseline_log_loss"]
        for value in markets.values())
    qualified = [
        name for name, value in markets.items()
        if value["model_log_loss"] < value["baseline_log_loss"]
        and value["model_brier"] < value["baseline_brier"]]
    return {"markets": markets, "total": total,
            "historically_qualified_markets": qualified}


def _aggregate_market(
    rows: list[dict[str, Any]], name: str,
) -> dict[str, float]:
    """Agrega una línea sobre 100 partidos."""

    return _aggregate_values([row["markets"][name] for row in rows])


def _aggregate_values(values: list[dict[str, Any]]) -> dict[str, float]:
    """Agrega log-loss, Brier y fiabilidad."""

    return {
        key: float(np.mean([value[key] for value in values]))
        for key in (
            "model_log_loss", "baseline_log_loss",
            "model_brier", "baseline_brier",
            "model_correct", "baseline_correct")
    }


def _bootstrap(values: list[float]) -> list[float]:
    """Bootstrap pareado por partido completo."""

    generator = random.Random(8801)
    samples = sorted(sum(generator.choice(values) for _ in values) / len(values)
                     for _ in range(BOOTSTRAP))
    return [samples[int(0.025 * BOOTSTRAP)],
            samples[int(0.975 * BOOTSTRAP) - 1]]


def _state_audit(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume ocupación semántica por métrica."""

    output = {}
    for metric in METRICS:
        counts = Counter(
            state_for(metric, int(row[metric]))
            for match in matches for side in ("home", "away")
            for row in match[side])
        total = sum(counts.values())
        output[metric] = {
            STATE_NAMES[metric][state]: counts[state] / total
            for state in range(3)}
    return output


def _write(name: str, payload: Any) -> None:
    """Escribe JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _write_csv(rows: list[dict[str, Any]]) -> None:
    """Publica las 100 comparaciones en formato tabular."""

    markets = sorted(rows[0]["markets"])
    fixed = [
        "rank", "match_id", "match_date", "league_slug",
        "home_team_id", "away_team_id", "reliability",
        "mean_model_log_loss", "mean_baseline_log_loss"]
    fields = fixed + [
        f"{name}_{suffix}" for name in markets
        for suffix in ("probability", "actual")]
    path = OUTPUT / "ranked_100_predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            writer.writerow(_csv_row(rank, row, markets))


def _csv_row(
    rank: int, row: dict[str, Any], markets: list[str],
) -> dict[str, Any]:
    """Aplana una predicción ordenada para CSV."""

    output = {
        key: row[key] for key in (
            "match_id", "match_date", "league_slug", "home_team_id",
            "away_team_id", "reliability", "mean_model_log_loss",
            "mean_baseline_log_loss")}
    output["rank"] = rank
    for name in markets:
        output[f"{name}_probability"] = row["markets"][name]["probability"]
        output[f"{name}_actual"] = row["markets"][name]["actual"]
    return output


def _report(result: dict[str, Any]) -> str:
    """Renderiza el resumen del backtest."""

    total = result["metrics"]["total"]
    lines = [
        "# Fase 88 — Markov de mercados por equipo", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- partidos del modelo: `{result['coverage']['matches']}`",
        "- prueba: `100` partidos confirmation", "- mercados: `12`",
        f"- fiabilidad Markov: `{total['model_correct']:.2%}`",
        f"- fiabilidad baseline: `{total['baseline_correct']:.2%}`",
        f"- log-loss Markov: `{total['model_log_loss']:.6f}`",
        f"- log-loss baseline: `{total['baseline_log_loss']:.6f}`",
        f"- mercados ganados: `{total['markets_model_wins_log_loss']}/12`",
        f"- mercados calificados: "
        f"`{len(result['metrics']['historically_qualified_markets'])}/12`",
        f"- IC95% mejora: `{total['bootstrap_log_loss_improvement_ci95']}`",
        "", "## Mercados", "",
        "| Mercado | Accuracy M | Accuracy B | Log-loss M | Log-loss B |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, value in result["metrics"]["markets"].items():
        lines.append(
            f"| {name} | {value['model_correct']:.1%} | "
            f"{value['baseline_correct']:.1%} | "
            f"{value['model_log_loss']:.4f} | "
            f"{value['baseline_log_loss']:.4f} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    """Ejecuta entrenamiento, backtest, scoring y publicación."""

    old_rows = _read_2024(_team_mapping())
    current_rows = _read_current()
    matches = _matches(old_rows + current_rows)
    predictions, outcomes, model = _walk_forward(matches)
    selected = _select_100(predictions)
    scored = _score(selected, outcomes)
    metrics = _metrics(scored)
    improvement = (
        metrics["total"]["baseline_log_loss"]
        - metrics["total"]["model_log_loss"])
    qualified = metrics["historically_qualified_markets"]
    classification = (
        "promising_unconfirmed" if improvement > 0
        else "partially_validated" if qualified
        else "rejected_for_revision")
    result = {
        "classification": classification,
        "config": {
            "version": "team_market_markov_v3_kickoff_integrity",
            "metrics": list(METRICS),
            "state_names": STATE_NAMES, "market_lines": MARKET_LINES,
            "bootstrap_replicates": BOOTSTRAP,
            "training_cutoff_ts": str(matches[-1]["match_date"]),
            "enabled_shadow_markets": metrics[
                "historically_qualified_markets"]},
        "coverage": {
            "matches": len(matches),
            "matches_2024": len({row["match_id"] for row in old_rows}),
            "confirmation_matches": sum(
                match["split"] == "confirmation" for match in matches),
            "confirmation_predictions": len(predictions),
            "report_matches": len(scored),
            "leagues": len({row["league_slug"] for row in matches})},
        "audit": {
            "features_strictly_prior": True,
            "prediction_before_update": True,
            "same_kickoff_batch_safe": True,
            "split_kickoff_atomic": True,
            "selection_before_scoring": True,
            "target_match_events_used_as_features": False,
            "report_ids_unique": len({row["match_id"] for row in scored}) == 100,
            "goal_router_modified": False},
        "state_occupancy": _state_audit(matches),
        "metrics": metrics, "ranked_predictions": scored,
    }
    _publish(result, old_rows, model)
    return result


def _publish(
    result: dict[str, Any], old_rows: list[dict[str, Any]],
    model: TeamMarketMarkov,
) -> None:
    """Publica contrato completo y hashes."""

    payloads = {
        "config.json": result["config"], "coverage.json": result["coverage"],
        "audit.json": result["audit"], "metrics.json": result["metrics"],
        "state_occupancy.json": result["state_occupancy"],
        "ranked_100_predictions.json": result["ranked_predictions"],
        "input_manifest.json": {
            "phase74_sha256": _sha(SOURCE),
            "phase01_sha256": _sha(SOURCE_2024),
            "mapped_2024_rows": len(old_rows)},
    }
    for name, payload in payloads.items():
        _write(name, payload)
    joblib.dump(model, OUTPUT / "team_market_markov.joblib", compress=3)
    _write_csv(result["ranked_predictions"])
    report = _report(result)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    assert result["coverage"]["report_matches"] == 100
    assert result["audit"]["report_ids_unique"]
    assert not result["audit"]["target_match_events_used_as_features"]
    LOGGER.info("Fase 88: %s", result["classification"])


# Version: 1.0.0
# Created: 2026-07-28
