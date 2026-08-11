"""Corrige por shrinkage bayesiano los mercados que el diagnóstico marcó.

Lee `artifacts/phase_119_bias_backtest_500/audit.json` (ya escrito por
`run_phase_119_bias_diagnosis_500.py`) para saber qué mercados entraron a
corrección. Para cada uno:

1. Elige `shrinkage` por grid search evaluando log-loss causal **sólo sobre
   la cohorte de ajuste** (`tuning_ids`, las 500 más antiguas de las 1,000 de
   Fase 105) — nunca sobre la cohorte de prueba.
2. Congela `prior_rate` como la tasa empírica observada **también sólo en la
   cohorte de ajuste** (no un 0.5 fijo como BTTS, porque estos mercados no
   son necesariamente simétricos — p. ej. `home_corners_over_4_5` observa
   72% de positivos; anclar a 0.5 sesgaría la contracción en la dirección
   equivocada para muestras chicas de liga).
3. Corre el gate de Fase 106 sin modificarlo (`_bootstrap`, `_stability`,
   `_passed`, `_metrics` importados de `run_phase_106_probability_repair`)
   sobre la cohorte de prueba, con el hiperparámetro ya congelado.
4. Sólo si pasa, escribe `calibrators/<market>.json` (contrato de
   `src/market_calibration.py`) y actualiza `calibrators/hashes.json`.

Un mercado que no pasa se reporta diagnosticado y no corregido; el script no
aborta por el fallo de uno solo.

Version: 1.0.0
Created: 2026-08-10
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import _matches, _read_current  # noqa: E402
from scripts.run_phase_94_historical_500_semiofficial import _counts, _outcomes  # noqa: E402
from scripts.run_phase_105_historical_1000_complete import _loss  # noqa: E402
from scripts.run_phase_106_probability_repair import (  # noqa: E402
    _bootstrap,
    _metrics,
    _passed,
    _stability,
)
from scripts.run_phase_119_bias_diagnosis_500 import (  # noqa: E402
    OUTPUT as DIAGNOSIS_OUTPUT,
    _load_cohorts,
    _match_goals,
    _read_json,
)
from src.market_calibration import CONTRACT_VERSION, league_shrinkage_probability  # noqa: E402
from src.temporal_integrity import kickoff_buckets  # noqa: E402

OUTPUT = DIAGNOSIS_OUTPUT
CALIBRATOR_DIR = OUTPUT / "calibrators"
SHRINKAGE_GRID = (20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)


def _write(name: str, payload: Any, directory: Path = OUTPUT) -> None:
    """Publica artefactos JSON deterministas."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")


def _market_actual(name: str, match: dict[str, Any]) -> bool:
    """Resuelve el outcome real de cualquier mercado corregible."""

    if name == "over_2_5":
        home, away = _match_goals(match)
        return home + away > 2
    return bool(_outcomes(_counts(match))[name])


def _league_shrinkage_series(
    name: str, matches: list[dict[str, Any]], target_ids: set[int],
    shrinkage: float, prior_rate: float,
) -> dict[int, tuple[float, bool, str]]:
    """Camina el corpus completo una sola vez, causal, por liga."""

    positives: dict[str, float] = defaultdict(float)
    totals: dict[str, float] = defaultdict(float)
    output: dict[int, tuple[float, bool, str]] = {}
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            match_id = int(match["match_id"])
            if match_id in target_ids:
                league = str(match["league_slug"])
                probability = league_shrinkage_probability(
                    positives[league], totals[league], shrinkage, prior_rate)
                output[match_id] = (
                    probability, _market_actual(name, match), league)
        for match in bucket:
            league = str(match["league_slug"])
            totals[league] += 1.0
            if _market_actual(name, match):
                positives[league] += 1.0
    missing = target_ids - set(output)
    if missing:
        raise ValueError(f"phase119_repair_coverage_incomplete:{name}:{len(missing)}")
    return output


def _prior_rate(
    name: str, matches: list[dict[str, Any]], tuning_ids: set[int],
) -> float:
    """Congela el prior como la tasa empírica de la cohorte de ajuste."""

    outcomes = [
        _market_actual(name, match) for match in matches
        if int(match["match_id"]) in tuning_ids]
    if len(outcomes) != len(tuning_ids):
        raise ValueError(f"phase119_prior_rate_coverage_incomplete:{name}")
    return float(np.mean(outcomes))


def _select_shrinkage(
    name: str, matches: list[dict[str, Any]], tuning_ids: set[int],
    prior_rate: float,
) -> float:
    """Elige shrinkage priorizando estabilidad por liga, no sólo log-loss.

    Minimizar únicamente el log-loss agregado de la cohorte de ajuste premia
    valores de shrinkage agresivos que sobreajustan a las ligas dominantes de
    ese bloque y no generalizan — es justo lo que el gate de Fase 106
    penaliza después vía `non_degradation_rate >= 0.70`. Seleccionar aquí
    también por estabilidad, con el log-loss sólo como desempate, evita
    proponer al gate un candidato que ya sabemos que no pasará.
    """

    def stats(shrinkage: float) -> tuple[float, float]:
        series = _league_shrinkage_series(
            name, matches, tuning_ids, shrinkage, prior_rate)
        rows = [
            {"league_slug": league,
             "raw_log_loss": _loss(prior_rate, actual)[0],
             "calibrated_log_loss": _loss(p, actual)[0]}
            for p, actual, league in series.values()]
        non_degradation = _stability(rows)["non_degradation_rate"]
        mean_log_loss = float(np.mean([row["calibrated_log_loss"] for row in rows]))
        return non_degradation, mean_log_loss

    scored = {shrinkage: stats(shrinkage) for shrinkage in SHRINKAGE_GRID}
    return max(scored, key=lambda shrinkage: (
        scored[shrinkage][0], -scored[shrinkage][1]))


def _repair_market(
    name: str, matches: list[dict[str, Any]], tuning_ids: set[int],
    test_ids: set[int], raw_probability: dict[int, float],
) -> dict[str, Any]:
    """Ajusta, liquida sobre la cohorte de prueba y aplica el gate."""

    prior_rate = _prior_rate(name, matches, tuning_ids)
    shrinkage = _select_shrinkage(name, matches, tuning_ids, prior_rate)
    series = _league_shrinkage_series(name, matches, test_ids, shrinkage, prior_rate)
    rows = []
    for match_id, (calibrated, actual, league) in series.items():
        raw = raw_probability[match_id]
        rows.append({
            "match_id": match_id, "league_slug": league,
            "raw_probability": raw, "calibrated_probability": calibrated,
            "actual": actual,
            "raw_log_loss": _loss(raw, actual)[0],
            "calibrated_log_loss": _loss(calibrated, actual)[0],
            "raw_brier": (raw - float(actual)) ** 2,
            "calibrated_brier": (calibrated - float(actual)) ** 2,
        })
    metrics = _metrics(rows)
    return {
        "market": name, "method": "league_shrinkage",
        "hyperparameter": {"shrinkage": shrinkage, "prior_rate": prior_rate},
        "metrics": metrics, "rows": rows,
    }


def _publish_calibrator(name: str, hyperparameter: dict[str, float]) -> None:
    """Sella el calibrador y actualiza el manifiesto de hashes del mercado."""

    CALIBRATOR_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONTRACT_VERSION, "market": name,
        "prior_rate": hyperparameter["prior_rate"],
        "shrinkage": hyperparameter["shrinkage"],
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    (CALIBRATOR_DIR / f"{name}.json").write_bytes(serialized)
    manifest_path = CALIBRATOR_DIR / "hashes.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {})
    manifest[f"{name}.json"] = hashlib.sha256(serialized).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta la reparación completa y produce el resumen antes/después."""

    audit = _read_json(OUTPUT / "audit.json")
    diagnosis_metrics = _read_json(OUTPUT / "metrics.json")["markets"]
    eligible = list(audit["eligible_for_correction"])

    tuning_rows, test_rows = _load_cohorts()
    tuning_ids = {int(row["match_id"]) for row in tuning_rows}
    test_ids = {int(row["match_id"]) for row in test_rows}
    matches = _matches(_read_current())
    raw_by_market_and_id = {
        name: {
            int(row["match_id"]): float(row["markets"][name]["probability"])
            for row in test_rows}
        for name in eligible}

    corrected: dict[str, Any] = {}
    dashboard_markets: dict[str, Any] = {}
    for name in eligible:
        repair = _repair_market(
            name, matches, tuning_ids, test_ids, raw_by_market_and_id[name])
        passed = bool(repair["metrics"]["passed"])
        if passed:
            _publish_calibrator(name, repair["hyperparameter"])
        corrected[name] = {
            "method": repair["method"], "hyperparameter": repair["hyperparameter"],
            "gate": repair["metrics"], "passed": passed,
        }
        dashboard_markets[name] = {
            "before": diagnosis_metrics[name],
            "after": _after_summary(repair["rows"]) if passed else None,
            "gate": {
                "entered_correction": True, "method": repair["method"],
                "hyperparameter": repair["hyperparameter"], "passed": passed,
                "bootstrap_improvement_ci95": repair["metrics"]["bootstrap_improvement_ci95"],
                "non_degradation_rate": repair["metrics"]["stability"]["non_degradation_rate"],
                "reason_not_corrected": None if passed else "gate_failed",
            },
        }
    for name, diag in diagnosis_metrics.items():
        if name in dashboard_markets:
            continue
        entered = name in audit.get("eligible_for_correction", [])
        dashboard_markets[name] = {
            "before": diag, "after": None,
            "gate": {
                "entered_correction": entered, "method": None,
                "hyperparameter": None, "passed": False,
                "bootstrap_improvement_ci95": None,
                "non_degradation_rate": None,
                "reason_not_corrected": (
                    "multiclass_not_correctable" if diag["is_multiclass"]
                    else "below_entry_threshold"),
            },
        }

    markets_corrected = sorted(name for name, value in corrected.items() if value["passed"])
    markets_not_corrected = sorted(
        name for name in diagnosis_metrics if name not in markets_corrected)
    dashboard_summary = {
        "phase": "119", "test_cohort": _cohort_summary(test_rows),
        "tuning_cohort": _cohort_summary(tuning_rows),
        "executive_summary": {
            "markets_diagnosed": len(diagnosis_metrics),
            "markets_biased": eligible,
            "markets_corrected": markets_corrected,
            "markets_diagnosed_not_corrected": markets_not_corrected,
            "markets_multiclass_diagnosed_only": sorted(
                audit.get("diagnosed_not_correctable_multiclass", [])),
        },
        "markets": dashboard_markets,
    }
    _write("repair_metrics.json", corrected)
    _write("dashboard_summary.json", dashboard_summary)
    _write("hashes.json", {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.glob("*.json")) if path.name != "hashes.json"})

    print(f"Elegibles evaluados: {eligible}")
    print(f"Corregidos (gate aprobado): {markets_corrected}")
    print(f"Diagnosticados sin corregir: {markets_not_corrected}")
    return dashboard_summary


def _after_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume la cohorte de prueba tras aplicar la corrección aprobada."""

    log_losses = [row["calibrated_log_loss"] for row in rows]
    briers = [row["calibrated_brier"] for row in rows]
    ece_rows = [{"raw_probability": row["calibrated_probability"], "actual": row["actual"]}
                for row in rows]
    from scripts.run_phase_106_probability_repair import _ece
    return {
        "predictions": len(rows),
        "accuracy": float(np.mean([
            (row["calibrated_probability"] >= 0.5) == row["actual"] for row in rows])),
        "log_loss": float(np.mean(log_losses)),
        "brier": float(np.mean(briers)),
        "ece": _ece(ece_rows, "raw_probability"),
    }


def _cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume identidad temporal de una cohorte."""

    return {
        "matches": len(rows),
        "first_match_date": min(row["match_date"] for row in rows),
        "last_match_date": max(row["match_date"] for row in rows),
        "leagues": len({row["league_slug"] for row in rows}),
    }


if __name__ == "__main__":
    run()

# Version: 1.0.0
# Created: 2026-08-10
