"""Entrena y audita mercados pre-match agregados de equipo.

Requirements:
    joblib>=1.4
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.team_count_markets import (  # noqa: E402
    CountMetricSpec,
    SklearnPoissonSolver,
    negative_binomial_over_probability,
    poisson_deviance,
)
from src.temporal_integrity import (  # noqa: E402
    kickoff_buckets,
    normalize_kickoff_splits,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
OUTPUT = ROOT / "artifacts/phase_84a_team_count_markets"
ALPHAS = (0.1, 1.0, 10.0)
METRICS = (
    CountMetricSpec("corners", "corners", False, 4.5),
    CountMetricSpec("corners_first_half", "corners", True, 2.2),
    CountMetricSpec("yellow_cards", "yellow_cards", False, 1.8),
    CountMetricSpec("yellow_cards_first_half", "yellow_cards", True, 0.8),
    CountMetricSpec("red_cards", "red_cards", False, 0.05),
    CountMetricSpec("shots", "shots", False, 10.0),
    CountMetricSpec("shots_on_target", "shots_on_target", False, 3.5),
)
MARKET_LINES = {
    "corners_total_over_9_5": ("corners", "total", 9),
    "home_corners_over_4_5": ("corners", "home", 4),
    "away_corners_over_4_5": ("corners", "away", 4),
    "first_half_corners_over_4_5": ("corners_first_half", "total", 4),
    "cards_total_over_2_5": ("yellow_cards", "total", 2),
    "first_half_cards_over_1_5": ("yellow_cards_first_half", "total", 1),
    "red_card_yes": ("red_cards", "total", 0),
    "shots_total_over_22_5": ("shots", "total", 22),
    "home_shots_over_10_5": ("shots", "home", 10),
    "away_shots_over_10_5": ("shots", "away", 10),
    "shots_on_target_total_over_7_5": ("shots_on_target", "total", 7),
}


def _read_rows() -> list[dict[str, Any]]:
    """Lee ventanas de 15 minutos del corpus causal."""

    with SOURCE.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega targets post-match por equipo y conserva identidad."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    matches = [_aggregate_match(values) for values in grouped.values()]
    return normalize_kickoff_splits(matches)


def _aggregate_match(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega las doce filas de un partido."""

    home = [row for row in rows if bool(row["is_home"])]
    away = [row for row in rows if not bool(row["is_home"])]
    first = rows[0]
    return {
        "match_id": int(first["match_id"]), "match_date": str(first["match_date"]),
        "league_slug": str(first["league_slug"]), "split": str(first["split"]),
        "home_team_id": int(home[0]["team_id"]),
        "away_team_id": int(away[0]["team_id"]),
        "home": _team_targets(home), "away": _team_targets(away),
    }


def _team_targets(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Calcula targets completos y de primera mitad."""

    output: dict[str, int] = {}
    for spec in METRICS:
        selected = [row for row in rows if not spec.first_half_only
                    or int(row["window_index"]) < 3]
        output[spec.name] = int(sum(
            _commercial_count(row, spec.name, spec.source_field)
            for row in selected))
    return output


def _commercial_count(
    row: dict[str, Any], metric: str, source_field: str,
) -> float:
    """Alinea tiros de eventos con el total comercial ESPN."""

    value = float(row.get(source_field, 0) or 0)
    if metric in {"shots", "shots_on_target"}:
        value += float(row.get("goals", 0) or 0)
    return value


def _examples(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construye perfiles rolling antes de actualizar cada partido."""

    team: dict[tuple[str, int, str], list[float]] = {}
    league: dict[tuple[str, bool, str], list[float]] = {}
    output = []
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            output.extend(_match_examples(match, team, league))
        for match in bucket:
            _update_history(match, team, league)
    return output


def _match_examples(
    match: dict[str, Any], team: dict[Any, list[float]],
    league: dict[Any, list[float]],
) -> list[dict[str, Any]]:
    """Congela dos observaciones orientadas antes del kickoff."""

    output = []
    for home in (True, False):
        side, rival = ("home", "away") if home else ("away", "home")
        output.append({
            "match_id": match["match_id"], "match_date": match["match_date"],
            "league_slug": match["league_slug"], "split": match["split"],
            "is_home": home,
            "team_id": match[f"{side}_team_id"],
            "opponent_team_id": match[f"{rival}_team_id"],
            "features": _features(match, home, team, league),
            "targets": match[side],
            "baselines": _baselines(match, home, league),
        })
    return output


def _features(
    match: dict[str, Any], home: bool, team: dict[Any, list[float]],
    league: dict[Any, list[float]],
) -> list[float]:
    """Materializa features causales de equipo, rival y liga."""

    league_name = str(match["league_slug"])
    own = int(match["home_team_id"] if home else match["away_team_id"])
    rival = int(match["away_team_id"] if home else match["home_team_id"])
    values = [float(home)]
    for spec in METRICS:
        own_stats = team.get((league_name, own, spec.name), [0.0, 0.0, 0.0])
        rival_stats = team.get(
            (league_name, rival, spec.name), [0.0, 0.0, 0.0])
        league_stats = league.get(
            (league_name, home, spec.name), [0.0, 0.0])
        values.extend(_profile_values(
            own_stats, rival_stats, league_stats, spec.safe_default))
    return values


def _profile_values(
    own: list[float], rival: list[float], league: list[float],
    default: float,
) -> list[float]:
    """Aplica smoothing causal a un bloque de perfil."""

    own_n, rival_n, league_n = own[2], rival[2], league[1]
    return [
        (own[0] + 5.0 * default) / (own_n + 5.0),
        (own[1] + 5.0 * default) / (own_n + 5.0),
        (rival[0] + 5.0 * default) / (rival_n + 5.0),
        (rival[1] + 5.0 * default) / (rival_n + 5.0),
        (league[0] + 20.0 * default) / (league_n + 20.0),
        math.log1p(own_n), math.log1p(rival_n),
    ]


def _baselines(
    match: dict[str, Any], home: bool,
    league: dict[Any, list[float]],
) -> dict[str, float]:
    """Congela baseline liga/localía antes del partido."""

    league_name = str(match["league_slug"])
    output = {}
    for spec in METRICS:
        total, count = league.get(
            (league_name, home, spec.name), [0.0, 0.0])
        output[spec.name] = (total + 20.0 * spec.safe_default) / (
            count + 20.0)
    return output


def _update_history(
    match: dict[str, Any], team: dict[Any, list[float]],
    league: dict[Any, list[float]],
) -> None:
    """Actualiza perfiles sólo después de emitir ambas observaciones."""

    league_name = str(match["league_slug"])
    for home in (True, False):
        side, rival = ("home", "away") if home else ("away", "home")
        own_id, rival_id = match[f"{side}_team_id"], match[f"{rival}_team_id"]
        for spec in METRICS:
            own, conceded = match[side][spec.name], match[rival][spec.name]
            _add(team, (league_name, own_id, spec.name), own, conceded)
            _add_league(league, (league_name, home, spec.name), own)


def _add(
    history: dict[Any, list[float]], key: Any,
    scored: float, conceded: float,
) -> None:
    """Acumula un perfil causal de equipo."""

    values = history.setdefault(key, [0.0, 0.0, 0.0])
    values[0] += scored
    values[1] += conceded
    values[2] += 1.0


def _add_league(
    history: dict[Any, list[float]], key: Any, value: float,
) -> None:
    """Acumula un prior causal de liga/localía."""

    values = history.setdefault(key, [0.0, 0.0])
    values[0] += value
    values[1] += 1.0


def _matrix(
    rows: list[dict[str, Any]], metric: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Convierte ejemplos a matriz y target."""

    features = np.asarray([row["features"] for row in rows], dtype=float)
    targets = np.asarray([row["targets"][metric] for row in rows], dtype=float)
    return features, targets


def _fit_models(
    examples: list[dict[str, Any]],
) -> tuple[
    dict[str, Any], dict[str, float], dict[str, Any],
    dict[str, float], dict[str, float],
]:
    """Selecciona alpha sin abrir confirmation y reajusta modelos."""

    fit = [row for row in examples if row["split"] == "fit"]
    selection = [row for row in examples if row["split"] == "selection"]
    train = [row for row in examples if row["split"] in ("fit", "selection")]
    models, alphas, selection_scores, dispersions, weights = {}, {}, {}, {}, {}
    for spec in METRICS:
        alpha, scores, weight = _select_alpha(fit, selection, spec.name)
        x_train, y_train = _matrix(train, spec.name)
        solver = SklearnPoissonSolver(alpha)
        solver.fit(x_train, y_train)
        models[spec.name] = solver
        alphas[spec.name] = alpha
        selection_scores[spec.name] = scores
        dispersions[spec.name] = _dispersion(y_train)
        weights[spec.name] = weight
    return models, alphas, selection_scores, dispersions, weights


def _dispersion(targets: np.ndarray) -> float:
    """Estima sobredispersión sólo con el bloque de entrenamiento."""

    mean = max(float(np.mean(targets)), 1e-9)
    variance = float(np.var(targets))
    return max((variance - mean) / (mean * mean), 1e-4)


def _select_alpha(
    fit: list[dict[str, Any]], selection: list[dict[str, Any]], metric: str,
) -> tuple[float, dict[str, Any], float]:
    """Selecciona regularización por deviance en selection."""

    x_fit, y_fit = _matrix(fit, metric)
    x_selection, y_selection = _matrix(selection, metric)
    scores: dict[str, Any] = {}
    solvers = {}
    for alpha in ALPHAS:
        solver = SklearnPoissonSolver(alpha)
        solver.fit(x_fit, y_fit)
        solvers[alpha] = solver
        predicted = solver.predict(x_selection)
        scores[str(alpha)] = float(np.mean([
            poisson_deviance(actual, expected)
            for actual, expected in zip(y_selection, predicted)]))
    selected = min(ALPHAS, key=lambda value: scores[str(value)])
    weight, blend_scores = _select_count_weight(
        solvers[selected], selection, metric)
    return selected, {"alpha_deviance": scores,
                      "blend_deviance": blend_scores}, weight


def _select_count_weight(
    solver: Any, selection: list[dict[str, Any]], metric: str,
) -> tuple[float, dict[str, float]]:
    """Selecciona mezcla con baseline usando sólo selection."""

    features, targets = _matrix(selection, metric)
    raw = solver.predict(features)
    baseline = np.asarray([
        row["baselines"][metric] for row in selection], dtype=float)
    candidates = [index / 10.0 for index in range(11)]
    scores = {
        str(weight): float(np.mean([
            poisson_deviance(actual, weight * model + (1.0 - weight) * base)
            for actual, model, base in zip(targets, raw, baseline)]))
        for weight in candidates
    }
    selected = min(candidates, key=lambda value: scores[str(value)])
    return selected, scores


def _predict(
    examples: list[dict[str, Any]], models: dict[str, Any],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Emite predicciones confirmation sin consultar targets."""

    confirmation = [row for row in examples if row["split"] == "confirmation"]
    features = np.asarray([row["features"] for row in confirmation], dtype=float)
    predicted = {name: model.predict(features)
                 for name, model in models.items()}
    output = []
    for index, row in enumerate(confirmation):
        output.append({
            **{key: row[key] for key in (
                "match_id", "match_date", "league_slug", "is_home",
                "team_id", "opponent_team_id")},
            "expected": {
                name: float(weights[name] * values[index] + (
                    1.0 - weights[name]) * row["baselines"][name])
                for name, values in predicted.items()},
            "baseline": dict(row["baselines"]),
        })
    return output


def _outcomes(
    examples: list[dict[str, Any]],
) -> dict[tuple[int, bool], dict[str, float]]:
    """Revela targets confirmation después de predecir."""

    return {
        (int(row["match_id"]), bool(row["is_home"])): {
            name: float(value) for name, value in row["targets"].items()}
        for row in examples if row["split"] == "confirmation"
    }


def _score(
    predictions: list[dict[str, Any]],
    outcomes: dict[tuple[int, bool], dict[str, float]],
) -> list[dict[str, Any]]:
    """Une outcomes y calcula errores por observación."""

    output = []
    for row in predictions:
        actual = outcomes[(int(row["match_id"]), bool(row["is_home"]))]
        metrics = {
            spec.name: _count_score(
                actual[spec.name], row["expected"][spec.name],
                row["baseline"][spec.name])
            for spec in METRICS
        }
        output.append({**row, "actual": actual, "metrics": metrics})
    return output


def _count_score(
    actual: float, expected: float, baseline: float,
) -> dict[str, float]:
    """Puntúa una predicción de conteo."""

    return {
        "model_mae": abs(actual - expected),
        "baseline_mae": abs(actual - baseline),
        "model_deviance": poisson_deviance(actual, expected),
        "baseline_deviance": poisson_deviance(actual, baseline),
    }


def _count_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas por conteo y estabilidad por liga."""

    output = {}
    for spec in METRICS:
        scores = [row["metrics"][spec.name] for row in rows]
        output[spec.name] = {
            key: float(np.mean([score[key] for score in scores]))
            for key in scores[0]
        }
        output[spec.name]["league_nonnegative_rate"] = _league_rate(
            rows, spec.name)
    return output


def _league_rate(rows: list[dict[str, Any]], metric: str) -> float:
    """Calcula proporción de ligas sin degradación de deviance."""

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        score = row["metrics"][metric]
        grouped[str(row["league_slug"])].append(
            score["baseline_deviance"] - score["model_deviance"])
    eligible = [values for values in grouped.values() if len(values) >= 30]
    return sum(float(np.mean(values) >= 0.0) for values in eligible) / max(
        len(eligible), 1)


def _market_rows(
    rows: list[dict[str, Any]], dispersions: dict[str, float],
) -> list[dict[str, Any]]:
    """Construye probabilidades y targets de líneas comerciales."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return [_match_markets(values, dispersions)
            for values in grouped.values()]


def _match_markets(
    rows: list[dict[str, Any]], dispersions: dict[str, float],
) -> dict[str, Any]:
    """Agrega las dos orientaciones de un partido."""

    home = next(row for row in rows if bool(row["is_home"]))
    away = next(row for row in rows if not bool(row["is_home"]))
    markets = {}
    for name, (metric, side, line) in MARKET_LINES.items():
        model_rate = _rate(home, away, metric, side, "expected")
        baseline_rate = _rate(home, away, metric, side, "baseline")
        actual = _rate(home, away, metric, side, "actual")
        model_phi = _combined_dispersion(
            home, away, metric, side, "expected", dispersions[metric])
        baseline_phi = _combined_dispersion(
            home, away, metric, side, "baseline", dispersions[metric])
        markets[name] = _binary_score(
            negative_binomial_over_probability(model_rate, model_phi, line),
            negative_binomial_over_probability(
                baseline_rate, baseline_phi, line),
            actual > line)
    return {
        "match_id": home["match_id"], "match_date": home["match_date"],
        "league_slug": home["league_slug"], "home_team_id": home["team_id"],
        "away_team_id": away["team_id"], "markets": markets,
    }


def _combined_dispersion(
    home: dict[str, Any], away: dict[str, Any], metric: str,
    side: str, block: str, dispersion: float,
) -> float:
    """Conserva media y varianza al combinar las dos orientaciones."""

    if side != "total":
        return dispersion
    home_rate = float(home[block][metric])
    away_rate = float(away[block][metric])
    total = max(home_rate + away_rate, 1e-9)
    extra = dispersion * (home_rate * home_rate + away_rate * away_rate)
    return max(extra / (total * total), 1e-8)


def _rate(
    home: dict[str, Any], away: dict[str, Any], metric: str,
    side: str, block: str,
) -> float:
    """Obtiene tasa individual o total."""

    if side == "home":
        return float(home[block][metric])
    if side == "away":
        return float(away[block][metric])
    return float(home[block][metric]) + float(away[block][metric])


def _binary_score(
    probability: float, baseline: float, actual: bool,
) -> dict[str, Any]:
    """Puntúa una línea binaria para modelo y baseline."""

    return {
        "probability": probability, "baseline_probability": baseline,
        "actual": actual,
        "model_log_loss": _binary_loss(probability, actual),
        "baseline_log_loss": _binary_loss(baseline, actual),
        "model_brier": (probability - float(actual)) ** 2,
        "baseline_brier": (baseline - float(actual)) ** 2,
    }


def _binary_loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario."""

    value = min(max(probability, 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _market_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas de las líneas publicadas."""

    output = {}
    for market in MARKET_LINES:
        values = [row["markets"][market] for row in rows]
        output[market] = {
            key: float(np.mean([value[key] for value in values]))
            for key in (
                "model_log_loss", "baseline_log_loss",
                "model_brier", "baseline_brier")
        }
        output[market]["positive_rate"] = float(np.mean([
            value["actual"] for value in values]))
    return output


def _gate(
    counts: dict[str, Any], markets: dict[str, Any],
) -> dict[str, Any]:
    """Aplica gates independientes por conteo y línea."""

    count_gates = {
        name: value["model_deviance"] <= value["baseline_deviance"]
        and value["model_mae"] <= value["baseline_mae"]
        and value["league_nonnegative_rate"] >= 0.70
        for name, value in counts.items()
    }
    market_gates = {
        name: value["model_log_loss"] <= value["baseline_log_loss"]
        and value["model_brier"] <= value["baseline_brier"]
        and count_gates[MARKET_LINES[name][0]]
        for name, value in markets.items()
    }
    enabled = sorted(name for name, passed in market_gates.items() if passed)
    return {
        "count_gates": count_gates, "market_gates": market_gates,
        "enabled_shadow_markets": enabled,
        "any_market_ready": bool(enabled),
        "all_gates_pass": all(count_gates.values())
        and all(market_gates.values()),
    }


def run() -> dict[str, Any]:
    """Ejecuta Fase 84A y publica sidecar shadow."""

    matches = _matches(_read_rows())
    examples = _examples(matches)
    models, alphas, selection_scores, dispersions, weights = _fit_models(examples)
    predictions = _predict(examples, models, weights)
    scored = _score(predictions, _outcomes(examples))
    market_rows = _market_rows(scored, dispersions)
    counts, markets = _count_metrics(scored), _market_metrics(market_rows)
    gate = _gate(counts, markets)
    result = _result(
        matches, examples, models, alphas, dispersions, weights,
        selection_scores,
        scored, market_rows, counts, markets, gate)
    _publish(result, models)
    return result


def _result(
    matches: list[dict[str, Any]], examples: list[dict[str, Any]],
    models: dict[str, Any], alphas: dict[str, float],
    dispersions: dict[str, float],
    weights: dict[str, float],
    selection_scores: dict[str, Any], scored: list[dict[str, Any]],
    market_rows: list[dict[str, Any]], counts: dict[str, Any],
    markets: dict[str, Any], gate: dict[str, Any],
) -> dict[str, Any]:
    """Compone contrato y auditoría de fase."""

    classification = (
        "ready_for_next_phase" if gate["any_market_ready"]
        else "rejected_for_revision")
    return {
        "classification": classification,
        "config": {
            "version": "team_count_markets_v3_kickoff_integrity",
            "alphas": alphas, "dispersions": dispersions,
            "model_weights": weights,
            "market_lines": MARKET_LINES,
            "metrics": [asdict(spec) for spec in METRICS],
            "model_status": "experimental_shadow_not_promoted",
        },
        "selection": selection_scores,
        "coverage": {
            "source_matches": len(matches), "examples": len(examples),
            "confirmation_team_rows": len(scored),
            "confirmation_matches": len(market_rows),
            "leagues": len({row["league_slug"] for row in market_rows}),
        },
        "audit": {
            "features_strictly_prior": True,
            "same_kickoff_batch_safe": True,
            "split_kickoff_atomic": True,
            "target_match_events_in_features": False,
            "selection_uses_confirmation": False,
            "goal_model_modified": False, "router_modified": False,
            "player_markets_enabled": False, **gate,
        },
        "player_market_readiness": _player_readiness(),
        "count_metrics": counts, "market_metrics": markets,
        "predictions": market_rows, "team_predictions": scored,
        "model_names": sorted(models),
    }


def _player_readiness() -> dict[str, Any]:
    """Declara campos ausentes que bloquean props de jugador."""

    required = (
        "player_id", "starter_status", "minutes_played", "position",
        "lineup_snapshot_ts", "shot_player_id", "card_player_id",
    )
    return {
        "classification": "blocked_by_data",
        "required_fields": list(required), "available_fields": [],
        "missing_fields": list(required),
        "historical_lineup_availability_proven": False,
        "player_event_attribution_reconciled": False,
    }


def _publish(result: dict[str, Any], models: dict[str, Any]) -> None:
    """Publica contrato completo y modelos serializados."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "config.json": result["config"], "selection.json": result["selection"],
        "coverage.json": result["coverage"], "audit.json": result["audit"],
        "metrics.json": {
            "counts": result["count_metrics"],
            "markets": result["market_metrics"]},
        "predictions.json": result["predictions"],
        "team_predictions.json": result["team_predictions"],
        "player_market_readiness.json": result["player_market_readiness"],
        "input_manifest.json": {"source_sha256": _sha(SOURCE)},
    }
    for name, value in payloads.items():
        _write_json(name, value)
    joblib.dump(
        {name: model.native_model() for name, model in models.items()},
        OUTPUT / "models.joblib")
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Renderiza resultados y decisión."""

    lines = [
        "# Fase 84A — mercados agregados de equipo", "",
        f"**Clasificación:** `{result['classification']}`", "",
        f"- partidos fuente: `{result['coverage']['source_matches']}`",
        f"- confirmation: `{result['coverage']['confirmation_matches']}`",
        f"- ligas confirmation: `{result['coverage']['leagues']}`",
        "- modelo de goles modificado: `False`",
        "- estado: `experimental_shadow_not_promoted`", "",
        f"- líneas habilitadas en shadow: "
        f"`{', '.join(result['audit']['enabled_shadow_markets'])}`", "",
        "## Conteos", "",
        "| Target | Deviance modelo | Baseline | MAE modelo | Baseline | Ligas + |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, value in result["count_metrics"].items():
        lines.append(
            f"| {name} | {value['model_deviance']:.4f} | "
            f"{value['baseline_deviance']:.4f} | {value['model_mae']:.4f} | "
            f"{value['baseline_mae']:.4f} | "
            f"{value['league_nonnegative_rate']:.2%} |")
    lines.extend(["", "## Líneas", "",
                  "| Mercado | Log-loss modelo | Baseline | Brier modelo | Baseline |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for name, value in result["market_metrics"].items():
        lines.append(
            f"| {name} | {value['model_log_loss']:.4f} | "
            f"{value['baseline_log_loss']:.4f} | "
            f"{value['model_brier']:.4f} | "
            f"{value['baseline_brier']:.4f} |")
    return "\n".join(lines) + "\n"


def _write_json(name: str, value: Any) -> None:
    """Escribe JSON reproducible."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _sha(path: Path) -> str:
    """Calcula hash SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta la fase y acepta ambos resultados controlados."""

    result = run()
    LOGGER.info("Fase 84A: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
