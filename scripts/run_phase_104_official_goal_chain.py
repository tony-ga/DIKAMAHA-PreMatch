"""Evalúa walk-forward la cadena Dixon-Coles/Kalman contra el router vigente.

# Requirements:
#   numpy>=1.24
#   pandas>=2.0
#   scipy>=1.10

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dixon_coles_v1 import DixonColesEstimatorV1
from src.kalman_v2 import KalmanV2Config, KalmanV2Filter, poisson_matrix
from src.official_goal_chain import (
    _blend_lambda,
    _dc_config,
    _initial_state,
    _markets,
    _predict_update,
    _team_ids,
)
from src.prematch_snapshot_registry import resolve_active_snapshot
from src.temporal_integrity import (  # noqa: E402
    aligned_fraction_boundaries,
    split_boundary_is_causal,
)
from src.universal_prematch import (
    UpcomingMatchInput,
    _lambdas,
    _load_matches,
    _markets as baseline_markets,
)

OUTPUT = ROOT / "artifacts/phase_104_official_goal_chain"
LOGGER = logging.getLogger(__name__)
BOOTSTRAP_SAMPLES = 10_000
SEED = 20260729


def _frame() -> pd.DataFrame:
    """Carga el snapshot activo y normaliza sus partidos."""

    rows = list(_load_matches(str(resolve_active_snapshot())))
    frame = pd.DataFrame(rows)
    frame["match_date"] = pd.to_datetime(
        frame["match_date"], utc=True, format="mixed")
    return frame.sort_values(["league_slug", "match_date", "match_id"])


def _parameters(model: Any) -> dict[str, Any]:
    """Adapta parámetros Dixon-Coles al inicializador Kalman."""

    return {
        "attack": model.attack, "defense": model.defense,
        "home_advantage": model.home_advantage,
        "league_intercept": model.league_intercept,
    }


def _dc_pair(model: Any, home: int, away: int) -> tuple[float, float]:
    """Calcula lambdas estructurales desde el modelo congelado."""

    home_rate = math.exp(
        model.league_intercept + model.home_advantage
        + model.attack[home] - model.defense[away])
    away_rate = math.exp(
        model.league_intercept + model.attack[away] - model.defense[home])
    return home_rate, away_rate


def _candidate(
    model: Any, state: Any, row: pd.Series,
) -> tuple[dict[str, float], tuple[int, int, int, int, float, float]]:
    """Predice un partido antes de construir su actualización Kalman."""

    filter_ = KalmanV2Filter(KalmanV2Config())
    home, away = int(row.home_team_id), int(row.away_team_id)
    prediction, _ = filter_._predict_one(
        state, home, away, 0, int(row.match_id),
        row.match_date.isoformat())
    dc_home, dc_away = _dc_pair(model, home, away)
    home_rate = _blend_lambda(dc_home, prediction.lambda_home)
    away_rate = _blend_lambda(dc_away, prediction.lambda_away)
    matrix = poisson_matrix(home_rate, away_rate, 12, model.tau_dc, True)
    update = _predict_update(filter_, state, row)
    return _markets(matrix), update


def _baseline(history: pd.DataFrame, row: pd.Series) -> dict[str, float]:
    """Calcula el baseline universal usando sólo el prefijo previo."""

    request = UpcomingMatchInput(
        str(row.league_slug), int(row.home_team_id), int(row.away_team_id),
        row.match_date.isoformat(), int(row.match_id))
    home, away, _ = _lambdas(history.to_dict("records"), request)
    return baseline_markets(home, away)


def _outcomes(row: pd.Series) -> dict[str, Any]:
    """Deriva outcomes de goles del partido ya separado del predictor."""

    home, away = int(row.home_goals), int(row.away_goals)
    result = "home" if home > away else "away" if away > home else "draw"
    return {
        "1x2": result, "over_2_5": home + away > 2,
        "btts": home > 0 and away > 0}


def _league_predictions(league: pd.DataFrame) -> list[dict[str, Any]]:
    """Ejecuta un fold temporal 60/20/20 para una liga."""

    league = league.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    if len(league) < 40:
        return []
    dc_end, evaluation_start = aligned_fraction_boundaries(
        league, (0.60, 0.80))
    split_causal = (
        split_boundary_is_causal(league, dc_end)
        and split_boundary_is_causal(league, evaluation_start))
    if not split_causal:
        raise RuntimeError("phase104_kickoff_split_violation")
    model = DixonColesEstimatorV1(_dc_config()).fit(
        league.iloc[:dc_end],
        team_universe=_team_ids(league.iloc[:evaluation_start]))
    state = _initial_state(model, league.iloc[:dc_end], league.iloc[dc_end].match_date.isoformat())
    state = _replay_prefix(state, league.iloc[dc_end:evaluation_start])
    rows = _evaluate_tail(model, state, league, evaluation_start)
    for row in rows:
        row["audit"]["split_boundaries_causal"] = split_causal
        row["audit"]["dc_fit_end"] = dc_end
        row["audit"]["evaluation_start"] = evaluation_start
    return rows


def _replay_prefix(state: Any, frame: pd.DataFrame) -> Any:
    """Actualiza el estado sobre la banda temporal intermedia."""

    filter_ = KalmanV2Filter(KalmanV2Config())
    for cutoff, bucket in frame.groupby("match_date", sort=True):
        updates = [_predict_update(filter_, state, row) for _, row in bucket.iterrows()]
        state = filter_._update_batch(state, updates)
        state.cutoff_ts = cutoff.isoformat()
    return state


def _evaluate_tail(
    model: Any, state: Any, league: pd.DataFrame, start: int,
) -> list[dict[str, Any]]:
    """Predice la cola y actualiza sólo después de cada bucket de kickoff."""

    rows: list[dict[str, Any]] = []
    filter_ = KalmanV2Filter(KalmanV2Config())
    for cutoff, bucket in league.iloc[start:].groupby("match_date", sort=True):
        updates = []
        history = league[league["match_date"] < cutoff]
        for _, row in bucket.iterrows():
            if (int(row.home_team_id) not in model.team_ids
                    or int(row.away_team_id) not in model.team_ids):
                continue
            candidate, update = _candidate(model, state, row)
            materialized = _prediction_row(
                row, candidate, _baseline(history, row))
            materialized["audit"] = {
                "cutoff_causal": bool(
                    history.empty or history["match_date"].max() < cutoff),
                "history_max_ts": None if history.empty else
                    history["match_date"].max().isoformat(),
                "teams_observed_before_evaluation": True,
            }
            rows.append(materialized)
            updates.append(update)
        state = filter_._update_batch(state, updates)
        state.cutoff_ts = cutoff.isoformat()
    return rows


def _prediction_row(
    row: pd.Series, candidate: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, Any]:
    """Materializa predicción y outcome sin mezclarlos durante inferencia."""

    return {
        "match_id": int(row.match_id), "league_slug": str(row.league_slug),
        "match_date": row.match_date.isoformat(),
        "candidate": candidate, "baseline": baseline,
        "outcomes": _outcomes(row)}


def _binary_loss(probability: float, outcome: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier binarios."""

    selected = probability if outcome else 1.0 - probability
    return -math.log(max(selected, 1e-15)), (probability - float(outcome)) ** 2


def _market_losses(row: dict[str, Any], provider: str, market: str) -> tuple[float, float]:
    """Calcula pérdidas de un proveedor para un mercado."""

    probabilities = row[provider]
    outcome = row["outcomes"][market]
    if market != "1x2":
        return _binary_loss(float(probabilities[market]), bool(outcome))
    keys = ("home", "draw", "away")
    log_loss = -math.log(max(float(probabilities[str(outcome)]), 1e-15))
    brier = sum(
        (float(probabilities[key]) - float(key == outcome)) ** 2
        for key in keys)
    return log_loss, brier


def _bootstrap(improvements: np.ndarray) -> list[float]:
    """Calcula IC95% pareado por partido."""

    rng = np.random.default_rng(SEED)
    indexes = rng.integers(0, len(improvements), size=(BOOTSTRAP_SAMPLES, len(improvements)))
    means = improvements[indexes].mean(axis=1)
    return [float(value) for value in np.quantile(means, [0.025, 0.975])]


def _market_metrics(rows: list[dict[str, Any]], market: str) -> dict[str, Any]:
    """Agrega métricas, bootstrap y estabilidad por liga."""

    candidate = np.array([_market_losses(row, "candidate", market)[0] for row in rows])
    baseline = np.array([_market_losses(row, "baseline", market)[0] for row in rows])
    candidate_brier = np.array([_market_losses(row, "candidate", market)[1] for row in rows])
    baseline_brier = np.array([_market_losses(row, "baseline", market)[1] for row in rows])
    improvements = baseline - candidate
    stability = _league_stability(rows, market)
    ci = _bootstrap(improvements)
    passed = ci[0] > 0 and candidate_brier.mean() <= baseline_brier.mean() and stability >= 0.70
    return {
        "candidate_log_loss": float(candidate.mean()),
        "baseline_log_loss": float(baseline.mean()),
        "candidate_brier": float(candidate_brier.mean()),
        "baseline_brier": float(baseline_brier.mean()),
        "improvement": float(improvements.mean()), "ci_95": ci,
        "league_non_degradation": stability, "passed": bool(passed)}


def _league_stability(rows: list[dict[str, Any]], market: str) -> float:
    """Mide la fracción de ligas donde el candidato no degrada."""

    leagues = sorted({row["league_slug"] for row in rows})
    non_degraded = 0
    for league in leagues:
        selected = [row for row in rows if row["league_slug"] == league]
        differences = [
            _market_losses(row, "baseline", market)[0]
            - _market_losses(row, "candidate", market)[0]
            for row in selected]
        non_degraded += float(np.mean(differences) >= 0)
    return non_degraded / max(len(leagues), 1)


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON reproducible."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    (OUTPUT / name).write_text(text + "\n", encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta Fase 104 y materializa su gate por mercado."""

    frame = _frame()
    cold_start_audit = _cold_start_audit(frame)
    predictions = []
    for _, league in frame.groupby("league_slug", sort=True):
        predictions.extend(_league_predictions(league))
    predictions.sort(key=lambda row: (row["match_date"], row["match_id"]))
    selected = predictions[-500:]
    metrics = {
        market: _market_metrics(selected, market)
        for market in ("1x2", "over_2_5", "btts")}
    audit = {
        "classification": "ready_for_selective_promotion"
        if any(item["passed"] for item in metrics.values())
        else "rejected_keep_baseline",
        "prediction_count": len(selected), "league_count": len({
            row["league_slug"] for row in selected}),
        "all_cutoff_causal": bool(selected) and all(
            row.get("audit", {}).get("cutoff_causal") is True
            for row in selected),
        "all_split_boundaries_causal": bool(selected) and all(
            row.get("audit", {}).get("split_boundaries_causal") is True
            for row in selected),
        **cold_start_audit,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "promoted_markets": [
            key for key, value in metrics.items() if value["passed"]]}
    _write("predictions.json", selected)
    _write("metrics.json", metrics)
    _write("audit.json", audit)
    _write_hashes()
    _report(audit, metrics)
    return audit


def _cold_start_audit(frame: pd.DataFrame) -> dict[str, int]:
    """Cuenta filas no servibles sin identidad observada antes del fold."""

    total, excluded = 0, 0
    for _, league in frame.groupby("league_slug", sort=True):
        league = league.sort_values(
            ["match_date", "match_id"]).reset_index(drop=True)
        if len(league) < 40:
            continue
        evaluation_start = aligned_fraction_boundaries(
            league, (0.60, 0.80))[1]
        known = set(_team_ids(league.iloc[:evaluation_start]))
        tail = league.iloc[evaluation_start:]
        total += len(tail)
        excluded += int((
            ~tail["home_team_id"].isin(known)
            | ~tail["away_team_id"].isin(known)).sum())
    return {
        "evaluation_rows_before_cold_start_filter": total,
        "cold_start_rows_excluded": excluded,
    }


def _write_hashes() -> None:
    """Sella los artefactos JSON antes del reporte humano."""

    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.glob("*.json"))
        if path.name != "hashes.json"}
    _write("hashes.json", hashes)


def _report(audit: dict[str, Any], metrics: dict[str, Any]) -> None:
    """Genera un reporte legible del gate."""

    lines = ["# Fase 104 — cadena oficial de goles", "",
             f"Estado: `{audit['classification']}`.", "",
             f"- partidos: {audit['prediction_count']}",
             f"- ligas: {audit['league_count']}",
             f"- mercados promovibles: {', '.join(audit['promoted_markets']) or 'ninguno'}", ""]
    for market, values in metrics.items():
        lines.append(
            f"- {market}: LL {values['candidate_log_loss']:.6f} vs "
            f"{values['baseline_log_loss']:.6f}; IC {values['ci_95']}; "
            f"estabilidad {values['league_non_degradation']:.2%}; "
            f"pass={values['passed']}")
    (OUTPUT / "final_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run()
    LOGGER.info("Fase 104: %s", result["classification"])
    assert result["prediction_count"] <= 500
    assert result["bootstrap_samples"] == BOOTSTRAP_SAMPLES
    assert isinstance(result["promoted_markets"], list)

# Version: 1.0.0
# Created: 2026-07-29
