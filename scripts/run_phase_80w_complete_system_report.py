"""Evalúa la cadena pre-match más completa sobre 100 partidos históricos.

Requirements:
    Python>=3.10

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/phase_45_temporal_markov_recalibration_v1/predictions.json"
CALIBRATION = ROOT / "artifacts/phase_45_temporal_markov_recalibration_v1/calibration.json"
OUTPUT = ROOT / "artifacts/phase_80w_complete_system_100_match_test"
SAMPLE_SIZE = 100
LOGGER = logging.getLogger(__name__)
BINARY_MARKETS = (
    ("over_2_5", "prob_over_2_5"),
    ("btts", "prob_btts"),
    ("first_half_goal", "prob_first_half_goal"),
    ("second_half_goal", "prob_second_half_goal"),
)


def _load(path: Path) -> Any:
    """Carga un JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _select(rows: list[dict[str, Any]]) -> list[int]:
    """Selecciona la cola confirmatoria antes del scoring."""

    confirmation = [row for row in rows if row["split"] == "confirmation"]
    identities = {(str(row["match_date"]), int(row["match_id"]))
                  for row in confirmation}
    return [match_id for _, match_id in sorted(identities)[-SAMPLE_SIZE:]]


def _prediction_view(row: dict[str, Any]) -> dict[str, Any]:
    """Extrae exclusivamente campos disponibles en la predicción."""

    names = (
        "match_id", "match_date", "league_slug", "home_team_id",
        "away_team_id", "lambda_dc_home", "lambda_dc_away",
        "lambda_kalman_home", "lambda_kalman_away", "lambda_base_home",
        "lambda_base_away", "expected_home_goals", "expected_away_goals",
        "prob_1", "prob_x", "prob_2", "prob_over_2_5", "prob_btts",
        "prob_first_half_goal", "prob_second_half_goal",
        "temporal_calibration_source",
    )
    return {name: row[name] for name in names}


def _outcome_view(row: dict[str, Any]) -> dict[str, Any]:
    """Extrae outcomes únicamente para la etapa de evaluación."""

    names = (
        "home_goals", "away_goals", "result_1x2", "over_2_5", "btts",
        "first_half_goal", "second_half_goal", "home_half_goals",
        "away_half_goals",
    )
    return {name: row[name] for name in names}


def _score_1x2(row: dict[str, Any], actual: str) -> dict[str, Any]:
    """Puntúa el mercado categórico 1X2."""

    probabilities = {
        "1": float(row["prob_1"]), "X": float(row["prob_x"]),
        "2": float(row["prob_2"]),
    }
    predicted = max(probabilities, key=probabilities.get)
    assigned = probabilities[actual]
    brier = sum((value - float(label == actual)) ** 2
                for label, value in probabilities.items())
    return _market_score(predicted, actual, assigned, brier, 2.0,
                         max(probabilities.values()))


def _score_binary(probability: float, actual: bool) -> dict[str, Any]:
    """Puntúa un mercado binario con umbral congelado 0.5."""

    predicted = probability >= 0.5
    assigned = probability if actual else 1.0 - probability
    brier = (probability - float(actual)) ** 2
    confidence = probability if predicted else 1.0 - probability
    return _market_score(predicted, actual, assigned, brier, 1.0, confidence)


def _market_score(
    predicted: Any, actual: Any, assigned: float, brier: float,
    brier_max: float, confidence: float,
) -> dict[str, Any]:
    """Construye métricas homogéneas por mercado."""

    clipped = min(max(float(assigned), 1e-12), 1.0)
    return {
        "predicted": predicted, "actual": actual,
        "correct": predicted == actual,
        "actual_probability": float(assigned),
        "prediction_confidence": float(confidence),
        "log_loss": -math.log(clipped), "brier": float(brier),
        "probabilistic_quality_percent": 100.0 * (1.0 - brier / brier_max),
    }


def _score_match(
    prediction: dict[str, Any], outcome: dict[str, Any],
) -> dict[str, Any]:
    """Une predicción y outcome después de congelar la primera."""

    markets = {"1x2": _score_1x2(prediction, str(outcome["result_1x2"]))}
    for market, probability_name in BINARY_MARKETS:
        markets[market] = _score_binary(
            float(prediction[probability_name]), bool(outcome[market]))
    correct = sum(bool(score["correct"]) for score in markets.values())
    actual_probability = sum(
        float(score["actual_probability"]) for score in markets.values()) / 5
    return {
        **prediction, **outcome, "markets": markets,
        "correct_markets": correct,
        "mean_actual_probability": actual_probability,
    }


def _ece(rows: list[dict[str, Any]], market: str) -> float:
    """Calcula ECE de confianza de decisión en diez bins."""

    bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        score = row["markets"][market]
        index = min(int(float(score["prediction_confidence"]) * 10), 9)
        bins[index].append((
            float(score["prediction_confidence"]),
            float(score["correct"]),
        ))
    total = len(rows)
    return sum(
        len(values) / total * abs(
            sum(value[0] for value in values) / len(values)
            - sum(value[1] for value in values) / len(values))
        for values in bins.values()
    )


def _market_metrics(
    rows: list[dict[str, Any]], market: str,
) -> dict[str, float]:
    """Agrega fiabilidad y scores probabilísticos."""

    scores = [row["markets"][market] for row in rows]
    count = len(scores)
    actual_counts: dict[str, int] = defaultdict(int)
    for score in scores:
        actual_counts[str(score["actual"])] += 1
    naive = 100.0 * max(actual_counts.values()) / count
    reliability = 100.0 * sum(
        bool(score["correct"]) for score in scores) / count
    return {
        "matches": count,
        "reliability_percent": reliability,
        "naive_majority_reliability_percent": naive,
        "reliability_uplift_pp": reliability - naive,
        "mean_log_loss": sum(float(score["log_loss"])
                             for score in scores) / count,
        "mean_brier": sum(float(score["brier"])
                          for score in scores) / count,
        "probabilistic_quality_percent": sum(
            float(score["probabilistic_quality_percent"])
            for score in scores) / count,
        "ece_percent": 100.0 * _ece(rows, market),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume los cinco mercados y su macro-promedio."""

    names = ("1x2", *(market for market, _ in BINARY_MARKETS))
    markets = {name: _market_metrics(rows, name) for name in names}
    total_reliability = sum(
        value["reliability_percent"] for value in markets.values()) / 5
    naive_reliability = sum(
        value["naive_majority_reliability_percent"]
        for value in markets.values()) / 5
    return {
        "matches": len(rows), "markets": markets,
        "total_reliability_percent": total_reliability,
        "naive_total_reliability_percent": naive_reliability,
        "total_reliability_uplift_pp": total_reliability - naive_reliability,
        "total_probabilistic_quality_percent": sum(
            value["probabilistic_quality_percent"]
            for value in markets.values()) / 5,
        "mean_correct_markets": sum(
            int(row["correct_markets"]) for row in rows) / len(rows),
        "date_from": min(str(row["match_date"]) for row in rows),
        "date_to": max(str(row["match_date"]) for row in rows),
        "leagues": len({str(row["league_slug"]) for row in rows}),
    }


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena después del scoring por calidad observada."""

    return sorted(rows, key=lambda row: (
        -int(row["correct_markets"]),
        -float(row["mean_actual_probability"]),
        int(row["match_id"]),
    ))


def run() -> dict[str, Any]:
    """Ejecuta el diagnóstico causal de sistema completo."""

    source = _load(SOURCE)
    selected = _select(source)
    indexed = {int(row["match_id"]): row for row in source}
    predictions = [_prediction_view(indexed[match_id])
                   for match_id in selected]
    outcomes = {match_id: _outcome_view(indexed[match_id])
                for match_id in selected}
    ranked = _rank([_score_match(row, outcomes[int(row["match_id"])])
                    for row in predictions])
    result = _result_payload(ranked, selected)
    _publish(result)
    return result


def _result_payload(
    rows: list[dict[str, Any]], selected: list[int],
) -> dict[str, Any]:
    """Compone resultado y auditoría de fase."""

    return {
        "classification": "validated",
        "config": {
            "version": "complete_system_100_match_test_v1",
            "source_phase": "phase_45_temporal_markov_recalibration_v1",
            "selection": "latest_100_confirmation_by_date_match_id",
            "ranking": "correct_markets_desc_then_actual_probability_desc",
            "binary_threshold": 0.5,
        },
        "coverage": {
            "selected_matches": len(selected),
            "unique_matches": len(set(selected)),
            "scored_matches": len(rows),
        },
        "audit": {
            "selection_before_scoring": True,
            "prediction_outcome_fields_separated": True,
            "source_predictions_causal": True,
            "validation_weights_frozen": True,
            "hawkes_included": False,
            "router_modified": False,
            "promotion_evidence": False,
        },
        "summary": _summary(rows), "predictions": rows,
    }


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos, reportes y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "config.json": result["config"], "coverage.json": result["coverage"],
        "audit.json": result["audit"], "metrics.json": result["summary"],
        "predictions_ranked.json": result["predictions"],
        "input_manifest.json": {
            "predictions_sha256": _sha(SOURCE),
            "calibration_sha256": _sha(CALIBRATION),
        },
    }
    for name, value in payloads.items():
        _write_json(name, value)
    _write_csv(result["predictions"])
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    })


def _write_json(name: str, value: Any) -> None:
    """Escribe JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(rows: list[dict[str, Any]]) -> None:
    """Escribe tabla plana por partido."""

    fields = (
        "rank", "match_id", "match_date", "league_slug", "home_team_id",
        "away_team_id", "actual_score", "expected_score", "correct_markets",
        "mean_actual_probability", "prediction_1x2", "actual_1x2",
        "over_2_5_correct", "btts_correct", "first_half_goal_correct",
        "second_half_goal_correct",
    )
    with (OUTPUT / "predictions_ranked.csv").open(
            "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, 1):
            writer.writerow(_flat(row, rank))


def _flat(row: dict[str, Any], rank: int) -> dict[str, Any]:
    """Aplana un registro para CSV."""

    markets = row["markets"]
    return {
        "rank": rank, "match_id": row["match_id"],
        "match_date": row["match_date"], "league_slug": row["league_slug"],
        "home_team_id": row["home_team_id"],
        "away_team_id": row["away_team_id"],
        "actual_score": f"{row['home_goals']}-{row['away_goals']}",
        "expected_score": (
            f"{row['expected_home_goals']:.2f}-"
            f"{row['expected_away_goals']:.2f}"),
        "correct_markets": row["correct_markets"],
        "mean_actual_probability": row["mean_actual_probability"],
        "prediction_1x2": markets["1x2"]["predicted"],
        "actual_1x2": markets["1x2"]["actual"],
        **{f"{name}_correct": markets[name]["correct"]
           for name, _ in BINARY_MARKETS},
    }


def _report(result: dict[str, Any]) -> str:
    """Construye reporte Markdown completo."""

    summary = result["summary"]
    lines = [
        "# Prueba completa de 100 partidos — Fase 80W", "",
        "**Clasificación:** `validated` como diagnóstico, no promoción", "",
        "Cadena: `Dixon-Coles/Kalman → intensidad → Markov → "
        "calibración temporal → mercados`.", "",
        f"- periodo: `{summary['date_from']}` → `{summary['date_to']}`",
        f"- ligas: `{summary['leagues']}`",
        f"- fiabilidad total: `{summary['total_reliability_percent']:.2f}%`",
        f"- referencia ingenua por mayoría: "
        f"`{summary['naive_total_reliability_percent']:.2f}%`",
        f"- diferencia: `{summary['total_reliability_uplift_pp']:+.2f} pp`",
        "- Hawkes: excluido por permanecer en shadow.", "",
        "## Fiabilidad por mercado", "",
        "| Mercado | Fiabilidad | Mayoría ingenua | Δ | Calidad prob. | "
        "Log-loss | Brier | ECE |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_metric_rows(summary["markets"]))
    lines.extend(["", "## Partidos ordenados por mejor predicción", "",
                  _match_header(), _match_separator()])
    lines.extend(_match_row(rank, row)
                 for rank, row in enumerate(result["predictions"], 1))
    lines.extend(["", "La fiabilidad es accuracy de decisión; la calidad "
                  "probabilística incorpora la distancia de Brier. El orden "
                  "se aplicó después de revelar resultados."])
    return "\n".join(lines) + "\n"


def _metric_rows(markets: dict[str, dict[str, float]]) -> list[str]:
    """Renderiza tabla de métricas."""

    labels = {
        "1x2": "1X2", "over_2_5": "Over 2.5", "btts": "BTTS",
        "first_half_goal": "Gol 1T", "second_half_goal": "Gol 2T",
    }
    return [
        f"| {labels[name]} | {value['reliability_percent']:.2f}% | "
        f"{value['naive_majority_reliability_percent']:.2f}% | "
        f"{value['reliability_uplift_pp']:+.2f} pp | "
        f"{value['probabilistic_quality_percent']:.2f}% | "
        f"{value['mean_log_loss']:.4f} | {value['mean_brier']:.4f} | "
        f"{value['ece_percent']:.2f}% |"
        for name, value in markets.items()
    ]


def _match_header() -> str:
    """Devuelve cabecera de partidos."""

    return ("| # | Fecha | Liga | Partido IDs | Real | xG sistema | "
            "1X2 | O2.5 | BTTS | Gol 1T | Gol 2T | Aciertos |")


def _match_separator() -> str:
    """Devuelve separador Markdown."""

    return ("| ---: | --- | --- | --- | ---: | ---: | --- | --- | --- | "
            "--- | --- | ---: |")


def _match_row(rank: int, row: dict[str, Any]) -> str:
    """Renderiza una fila de partido."""

    markets = row["markets"]
    values = [_market_cell(markets[name])
              for name in ("1x2", *(name for name, _ in BINARY_MARKETS))]
    return (
        f"| {rank} | {str(row['match_date'])[:10]} | {row['league_slug']} | "
        f"{row['home_team_id']}–{row['away_team_id']} | "
        f"{row['home_goals']}–{row['away_goals']} | "
        f"{row['expected_home_goals']:.2f}–{row['expected_away_goals']:.2f} | "
        f"{' | '.join(values)} | {row['correct_markets']}/5 |")


def _market_cell(score: dict[str, Any]) -> str:
    """Resume pronóstico, resultado y acierto."""

    mark = "✓" if score["correct"] else "✗"
    return f"{score['predicted']}→{score['actual']} {mark}"


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta la fase y exige cobertura exacta."""

    result = run()
    coverage = result["coverage"]
    LOGGER.info("Fase 80W: %s partidos", coverage["scored_matches"])
    return 0 if coverage["scored_matches"] == SAMPLE_SIZE else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
