"""Diagnostica sesgo de calibración de los 11 mercados pre-match servidos hoy.

Reutiliza la cohorte de 1,000 partidos ya auditada y publicada por Fase 105
(`artifacts/phase_105_historical_1000_complete/ranked_1000_predictions.json`)
en vez de reconstruir la selección desde cero: es la misma cola cronológica
causal de `confirmation`, ya validada, ya intersecada con Fase 84A y con la
cadena oficial Dixon-Coles/Kalman. La divide en dos mitades disjuntas por
fecha: las 500 más antiguas ("tuning") sirven sólo para elegir hiperparámetros
de corrección en `run_phase_119_market_repair.py`; las 500 más recientes
("test") son el "test grande" que se diagnostica y, después, se vuelve a
liquidar para la comparativa antes/después. Ningún ajuste de esta fase toca
las 500 de prueba.

El diagnóstico corrige dos desalineaciones frente a "lo que el sistema sirve
hoy" que la propia salida cruda de Fase 105 no refleja:

- `btts`: Fase 105 evaluó el baseline estructural, no el calibrador sellado de
  Fase 106 (`artifacts/phase_106_probability_repair/calibrator.json`), que sí
  es lo que sirve producción. Se recalcula aquí con las mismas constantes
  (`prior_rate`, `league_shrinkage`) leídas del artefacto sellado, mediante
  una única pasada causal (predict-antes-de-update) sobre TODO el corpus, no
  sólo la cohorte de 1,000 — la tasa por liga acumula desde el inicio real de
  la historia, igual que en producción.
- mercados en `MARKOV_BASELINE_FALLBACKS` (hoy sólo
  `home_corners_second_half_over_2_5`): producción ya cae al baseline de liga
  para esa línea (`src/team_count_market_runtime.py`); Fase 105 reportó la
  probabilidad Markov cruda. El campo `baseline_probability` que Fase 105 ya
  publicó por partido es exactamente ese baseline, así que basta con
  sustituir la probabilidad servida por ese campo ya existente.

Limitación de datos aceptada: el corpus usado
(`artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl`) no
incluye el pequeño lote adicional de partidos de 2024 que Fase 88 mezcla vía
`_read_2024()` (requiere acceso a base de datos para mapear IDs internos a
ESPN, no disponible en este entorno de diagnóstico). Como esos partidos son
cronológicamente los más antiguos del corpus combinado, no pueden aparecer en
la cohorte de 1,000 más reciente que ya seleccionó y publicó Fase 105 — el
diagnóstico sí necesita el corpus completo para las cuentas causales de BTTS
por liga, y esa omisión no cambia la cohorte de 1,000 en sí (ya congelada por
Fase 105), sólo podría, en teoría, faltar historial muy antiguo de alguna
liga poco frecuente en el conteo BTTS; se documenta en `audit.json`.

Version: 1.0.0
Created: 2026-08-10
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import _matches, _read_current  # noqa: E402
from scripts.run_phase_105_historical_1000_complete import (  # noqa: E402
    AGGREGATE,
    MARKETS,
    MARKOV,
    _loss,
    _score,
)
from scripts.run_phase_106_probability_repair import _bin, _ece  # noqa: E402
from src.btts_probability import ArtifactBttsProbabilityProvider  # noqa: E402
from src.settlement_store import wilson_interval  # noqa: E402
from src.team_count_market_runtime import MARKOV_BASELINE_FALLBACKS  # noqa: E402
from src.temporal_integrity import kickoff_buckets  # noqa: E402

OUTPUT = ROOT / "artifacts/phase_119_bias_backtest_500"
PHASE105_RANKED = (
    ROOT / "artifacts/phase_105_historical_1000_complete"
    / "ranked_1000_predictions.json")
COHORT_SIZE = 500
ENTRY_MIN_PREDICTIONS = 200
ENTRY_RATE_BAND = (0.05, 0.95)
ENTRY_ECE_THRESHOLD = 0.05
RELIABILITY_BINS = 10


def _read_json(path: Path) -> Any:
    """Carga un JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, payload: Any) -> None:
    """Publica artefactos JSON deterministas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")


def _load_cohorts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Divide los 1,000 partidos ya auditados en dos mitades sin solape."""

    if not PHASE105_RANKED.exists():
        raise ValueError("phase119_missing_phase105_artifact")
    rows = _read_json(PHASE105_RANKED)
    if len(rows) != 1000:
        raise ValueError(f"phase119_unexpected_cohort_size:{len(rows)}")
    ordered = sorted(rows, key=lambda row: (row["match_date"], row["match_id"]))
    dates = [row["match_date"] for row in ordered]
    if dates != sorted(dates):
        raise ValueError("phase119_cohort_not_chronological")
    tuning_rows, test_rows = ordered[:COHORT_SIZE], ordered[COHORT_SIZE:]
    tuning_ids = {int(row["match_id"]) for row in tuning_rows}
    test_ids = {int(row["match_id"]) for row in test_rows}
    if len(tuning_ids) != COHORT_SIZE or len(test_ids) != COHORT_SIZE:
        raise ValueError("phase119_cohort_duplicate_match_ids")
    if tuning_ids & test_ids:
        raise ValueError("phase119_cohort_overlap")
    return tuning_rows, test_rows


def _match_goals(match: dict[str, Any]) -> tuple[int, int]:
    """Reconcilia el marcador final desde las ventanas admitidas."""

    home = sum(int(row.get("goals", 0) or 0) for row in match["home"])
    away = sum(int(row.get("goals", 0) or 0) for row in match["away"])
    return home, away


def _served_btts_probabilities(
    target_ids: set[int],
) -> dict[int, tuple[float, dict[str, Any]]]:
    """Recalcula BTTS con el calibrador sellado, causal sobre todo el corpus.

    No se hace `_read_2024()` (requiere base de datos); los partidos de 2024
    son cronológicamente los más antiguos del corpus combinado y no cambian
    ninguna probabilidad de un partido posterior a ellos.
    """

    matches = _matches(_read_current())
    provider = ArtifactBttsProbabilityProvider()
    config = provider._load()  # reutiliza el artefacto sellado de Fase 106
    shrinkage = float(config["league_shrinkage"])
    prior_rate = float(config["prior_rate"])
    positives: dict[str, float] = defaultdict(float)
    totals: dict[str, float] = defaultdict(float)
    output: dict[int, tuple[float, dict[str, Any]]] = {}
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            league = str(match["league_slug"])
            match_id = int(match["match_id"])
            if match_id in target_ids:
                probability = (
                    positives[league] + shrinkage * prior_rate
                ) / (totals[league] + shrinkage)
                if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                    raise FloatingPointError("phase119_btts_probability_invalid")
                output[match_id] = probability, {
                    "model": str(config["model"]),
                    "version": str(config["version"]),
                    "league_shrinkage": shrinkage,
                    "history_matches": totals[league],
                }
        for match in bucket:
            league = str(match["league_slug"])
            home_goals, away_goals = _match_goals(match)
            positives[league] += float(home_goals > 0 and away_goals > 0)
            totals[league] += 1.0
    missing = target_ids - set(output)
    if missing:
        raise ValueError(f"phase119_btts_coverage_incomplete:{len(missing)}")
    return output


def _served_row(
    row: dict[str, Any], btts_probability: float,
) -> dict[str, Any]:
    """Sustituye la probabilidad servida donde Fase 105 no refleja producción."""

    markets = {name: dict(value) for name, value in row["markets"].items()}
    markets["btts"] = {
        **markets["btts"],
        **_score(btts_probability, bool(markets["btts"]["actual"])),
    }
    for name in MARKOV_BASELINE_FALLBACKS:
        if name not in markets:
            continue
        markets[name] = {
            **markets[name],
            **_score(
                float(markets[name]["baseline_probability"]),
                bool(markets[name]["actual"])),
        }
    return {**row, "markets": markets}


def _diagnostic_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrae {probability, actual} por mercado, con confianza para 1X2."""

    output = []
    for row in rows:
        value = row["markets"][name]
        if name == "1x2":
            probability = float(value["confidence"])
            actual = bool(value["correct"])
        else:
            probability = float(value["probability"])
            actual = bool(value["actual"])
        output.append({
            "match_id": row["match_id"], "probability": probability,
            "actual": actual,
        })
    return output


def _reliability_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construye 10 bins fijos con intervalo Wilson por bin."""

    bins = []
    for index, lower in enumerate(np.linspace(0.0, 0.9, RELIABILITY_BINS)):
        lower = float(lower)
        upper = lower + 0.1
        selected = _bin(rows, "probability", lower, upper)
        n = len(selected)
        if n == 0:
            bins.append({
                "bin_index": index, "lower": lower, "upper": upper, "n": 0,
                "predicted_mean": None, "observed_rate": None,
                "ci95_low": None, "ci95_high": None,
            })
            continue
        hits = sum(1 for item in selected if item["actual"])
        low, high = wilson_interval(hits, n)
        bins.append({
            "bin_index": index, "lower": lower, "upper": upper, "n": n,
            "predicted_mean": float(np.mean([item["probability"] for item in selected])),
            "observed_rate": hits / n,
            "ci95_low": low, "ci95_high": high,
        })
    return bins


def _diagnose_market(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Diagnostica un mercado sobre la cohorte de prueba."""

    diag_rows = _diagnostic_rows(name, rows)
    losses = [_loss(item["probability"], item["actual"]) for item in diag_rows]
    log_losses = [value[0] for value in losses]
    briers = [value[1] for value in losses]
    positive_rate = float(np.mean([item["actual"] for item in diag_rows]))
    ece_rows = [{**item, "raw_probability": item["probability"]} for item in diag_rows]
    return {
        "predictions": len(diag_rows),
        "positive_rate": positive_rate,
        "accuracy": float(np.mean([
            (item["probability"] >= 0.5) == item["actual"] for item in diag_rows])),
        "log_loss": float(np.mean(log_losses)),
        "brier": float(np.mean(briers)),
        "ece": _ece(ece_rows, "raw_probability"),
        "reliability_bins": _reliability_bins(diag_rows),
        "is_multiclass": name == "1x2",
    }


def _eligible_for_correction(diagnosis: dict[str, Any]) -> bool:
    """Filtra ruido de muestra chica y mercados casi degenerados."""

    if diagnosis["is_multiclass"]:
        return False
    if diagnosis["predictions"] < ENTRY_MIN_PREDICTIONS:
        return False
    low, high = ENTRY_RATE_BAND
    if not low <= diagnosis["positive_rate"] <= high:
        return False
    return diagnosis["ece"] > ENTRY_ECE_THRESHOLD


def run() -> dict[str, Any]:
    """Diagnostica los 11 mercados sobre la cohorte de prueba de 500."""

    tuning_rows, test_rows = _load_cohorts()
    target_ids = {int(row["match_id"]) for row in tuning_rows + test_rows}
    btts_served = _served_btts_probabilities(target_ids)
    tuning_served = [
        _served_row(row, btts_served[int(row["match_id"])][0])
        for row in tuning_rows]
    test_served = [
        _served_row(row, btts_served[int(row["match_id"])][0])
        for row in test_rows]

    diagnosis = {name: _diagnose_market(name, test_served) for name in MARKETS}
    eligible = sorted(
        name for name, diag in diagnosis.items()
        if _eligible_for_correction(diag))
    not_correctable_multiclass = sorted(
        name for name, diag in diagnosis.items()
        if diag["is_multiclass"] and diag["ece"] > ENTRY_ECE_THRESHOLD)

    coverage = {
        "test_matches": len(test_served), "tuning_matches": len(tuning_served),
        "test_first_match_date": min(row["match_date"] for row in test_served),
        "test_last_match_date": max(row["match_date"] for row in test_served),
        "tuning_first_match_date": min(row["match_date"] for row in tuning_served),
        "tuning_last_match_date": max(row["match_date"] for row in tuning_served),
        "leagues_test": len({row["league_slug"] for row in test_served}),
        "markets_diagnosed": len(MARKETS),
    }
    audit = {
        "source": "phase105_ranked_1000_predictions",
        "cohort_disjoint": True,
        "phase_2024_supplement_included": False,
        "phase_2024_supplement_reason": "requires_database_access_unavailable",
        "served_overrides": ["btts"] + sorted(MARKOV_BASELINE_FALLBACKS),
        "eligible_for_correction": eligible,
        "diagnosed_not_correctable_multiclass": not_correctable_multiclass,
    }
    config = {
        "entry_min_predictions": ENTRY_MIN_PREDICTIONS,
        "entry_rate_band": list(ENTRY_RATE_BAND),
        "entry_ece_threshold": ENTRY_ECE_THRESHOLD,
        "reliability_bins": RELIABILITY_BINS,
        "aggregate_markets": list(AGGREGATE),
        "markov_markets": list(MARKOV),
    }
    input_manifest = {
        "phase105_source": str(PHASE105_RANKED.relative_to(ROOT)),
        "phase105_source_hash": __import__("hashlib").sha256(
            PHASE105_RANKED.read_bytes()).hexdigest(),
    }
    metrics = {"markets": diagnosis}

    _write("config.json", config)
    _write("input_manifest.json", input_manifest)
    _write("coverage.json", coverage)
    _write("audit.json", audit)
    _write("metrics.json", metrics)
    _write("test_cohort_served.json", test_served)
    _write("tuning_cohort_served.json", tuning_served)

    print(f"Mercados diagnosticados: {len(MARKETS)}")
    print(f"Elegibles para corrección: {eligible or '(ninguno)'}")
    print(f"1X2 diagnosticado, no corregible con este mecanismo: "
          f"{'sí' if not_correctable_multiclass else 'no'}")
    for name in MARKETS:
        diag = diagnosis[name]
        print(f"  {name:38s} n={diag['predictions']:4d} "
              f"ece={diag['ece']:.4f} log_loss={diag['log_loss']:.4f} "
              f"brier={diag['brier']:.4f} pos_rate={diag['positive_rate']:.3f}")
    return {"config": config, "coverage": coverage, "audit": audit, "metrics": metrics}


if __name__ == "__main__":
    run()

# Version: 1.0.0
# Created: 2026-08-10
