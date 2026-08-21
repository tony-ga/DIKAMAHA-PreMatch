"""Reevalúa 1,000 partidos históricos con la cadena ya recalibrada.

Fase 105 midió los mismos 1,000 partidos el 2026-08-07, antes de que DEC-200
(peso de mezcla `0.8 -> 0.642848`) y DEC-201 (temperatura `1.198935` sobre
1X2) entraran al código servido el 2026-08-16. Aquel harness además nunca
aplicó la temperatura al 1X2: predecía la matriz cruda, de modo que su cifra
de 1X2 no describe lo que producción sirve hoy.

Esta fase repite la misma medición sin cambiar corpus, ventana ni mercados
-para que la diferencia sea atribuible a la calibración y no a los datos- y
publica el 1X2 en sus dos formas, crudo y calibrado, sobre la misma matriz.
La temperatura no altera el argmax (`x^(1/T)` es monótona creciente), así que
el acierto del 1X2 es idéntico entre ambas por construcción: lo que se mide
aquí es la confianza declarada, vía log-loss y Brier.

Causalidad: la temperatura se ajustó sobre el split `selection` de Fase 74
(`fitted_on: phase_74_selection_split`), disjunto de los partidos de
`confirmation` que esta fase puntúa, de modo que ningún parámetro vio estos
outcomes durante su ajuste.

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-21
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import (  # noqa: E402
    _matches, _read_2024, _read_current, _team_mapping,
)
from scripts.run_phase_94_historical_500_semiofficial import (  # noqa: E402
    AGGREGATE, MARKOV, _counts, _markov_predictions, _outcomes,
    _pbp_evidence, _quality_profile,
)
from scripts.run_phase_104_official_goal_chain import (  # noqa: E402
    _baseline, _candidate, _frame, _initial_state,
)
from src.dixon_coles_v1 import DixonColesEstimatorV1  # noqa: E402
from src.kalman_v2 import KalmanV2Config, KalmanV2Filter  # noqa: E402
from src.official_goal_chain import (  # noqa: E402
    BLEND_WEIGHT_DIXON_COLES, CALIBRATED_MARKET, _dc_config, _team_ids,
)
from src.temperature_calibration import (  # noqa: E402
    ArtifactTemperatureCalibrationProvider,
)
from src.temporal_integrity import aligned_fraction_boundaries  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_134_recalibrated_1000"
PHASE84 = ROOT / "artifacts/phase_84a_team_count_markets/predictions.json"
PHASE105 = ROOT / "artifacts/phase_105_historical_1000_complete/final_report.json"
BOOTSTRAP = 10_000
SEED = 20260821
MARKETS = ("1x2", "over_2_5", "btts") + AGGREGATE + MARKOV
# La cache de filas oficiales de Fase 105 se calculó con el blend previo y sin
# temperatura. Reusarla devolvería en silencio el modelo viejo, que es
# exactamente el defecto que esta fase existe para corregir, así que la
# versión cambia y las filas se recomputan desde cero.
OFFICIAL_CACHE_VERSION = "phase134_recalibrated_v1"


def _read_json(path: Path) -> Any:
    """Carga un JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier para un mercado binario."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _score(probability: float, actual: bool) -> dict[str, Any]:
    """Compara una probabilidad contra el outcome real."""

    log_loss, brier = _loss(probability, actual)
    predicted = float(probability) >= 0.5
    return {
        "probability": float(probability), "actual": bool(actual),
        "predicted": predicted, "correct": predicted == actual,
        "log_loss": log_loss, "brier": brier, "normalized_brier": brier,
    }


def _score_1x2(prediction: dict[str, float], actual: str) -> dict[str, Any]:
    """Liquida el resultado 1X2 desde una terna ya formada."""

    probabilities = {key: float(prediction[key]) for key in ("home", "draw", "away")}
    selected = max(probabilities, key=probabilities.get)
    brier = sum((value - float(key == actual)) ** 2
                for key, value in probabilities.items())
    return {
        "probabilities": probabilities, "actual": actual,
        "predicted": selected, "correct": selected == actual,
        "confidence": probabilities[selected],
        "log_loss": -math.log(max(probabilities[actual], 1e-12)),
        "brier": brier, "normalized_brier": brier / 2.0,
    }


def _calibrated_1x2(
    raw: dict[str, float], provider: ArtifactTemperatureCalibrationProvider,
) -> tuple[dict[str, float], float]:
    """Aplica la temperatura sellada al 1X2, igual que `_calibrate_1x2`.

    Reproduce el vocabulario de producción (`1`/`X`/`2`) y lo devuelve al
    vocabulario del harness (`home`/`draw`/`away`) sin tocar el orden, de modo
    que el argmax sea comparable fila a fila contra la versión cruda.
    """

    calibrated, meta = provider.predict(CALIBRATED_MARKET, {
        "1": raw["home"], "X": raw["draw"], "2": raw["away"]})
    return ({
        "home": calibrated["1"], "draw": calibrated["X"],
        "away": calibrated["2"]}, float(meta["temperature"]))


def _goal_actual(match: dict[str, Any]) -> dict[str, Any]:
    """Reconcilia goles del marcador desde las ventanas admitidas."""

    home = sum(int(row.get("goals", 0) or 0) for row in match["home"])
    away = sum(int(row.get("goals", 0) or 0) for row in match["away"])
    return {
        "1x2": "home" if home > away else "away" if away > home else "draw",
        "over_2_5": home + away > 2, "btts": home > 0 and away > 0,
        "score": f"{home}-{away}",
    }


def _evaluate_tail_calibrated(
    model: Any, state: Any, league: Any, start: int,
    provider: ArtifactTemperatureCalibrationProvider,
) -> list[dict[str, Any]]:
    """Predice la cola añadiendo el 1X2 calibrado junto al crudo.

    Copia deliberada de `run_phase_104._evaluate_tail`: mismo orden de
    predicción y actualización -predecir siempre antes de actualizar con el
    bucket de kickoff- porque invertirlo filtraría el partido objetivo en su
    propio estado. Lo único que añade es la terna calibrada.
    """

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
            calibrated, temperature = _calibrated_1x2(candidate, provider)
            rows.append({
                "match_id": int(row.match_id),
                "league_slug": str(row.league_slug),
                "match_date": row.match_date.isoformat(),
                "candidate": candidate,
                "candidate_calibrated": calibrated,
                "temperature": temperature,
                "baseline": _baseline(history, row),
                "audit": {
                    "cutoff_causal": bool(
                        history.empty or history["match_date"].max() < cutoff),
                    "history_max_ts": None if history.empty else
                        history["match_date"].max().isoformat(),
                },
            })
            updates.append(update)
        state = filter_._update_batch(state, updates)
        state.cutoff_ts = cutoff.isoformat()
    return rows


def _official_rows() -> dict[int, dict[str, Any]]:
    """Ejecuta la cadena oficial recalibrada por liga sobre su cola causal."""

    cache = OUTPUT / "official_goal_rows.json"
    if cache.exists():
        cached = _read_json(cache)
        if cached.get("version") == OFFICIAL_CACHE_VERSION:
            LOGGER.info("Reusando filas oficiales cacheadas de esta fase")
            return {int(key): value for key, value in cached["rows"].items()}
    provider = ArtifactTemperatureCalibrationProvider()
    rows: dict[int, dict[str, Any]] = {}
    frame = _frame()
    leagues = list(frame.groupby("league_slug", sort=True))
    for index, (slug, league) in enumerate(leagues, start=1):
        league = league.sort_values(["match_date", "match_id"]).reset_index(drop=True)
        if len(league) < 40:
            continue
        start = aligned_fraction_boundaries(league, (0.60,))[0]
        model = DixonColesEstimatorV1(_dc_config()).fit(
            league.iloc[:start], team_universe=_team_ids(league.iloc[:start]))
        state = _initial_state(
            model, league.iloc[:start], league.iloc[start].match_date.isoformat())
        for row in _evaluate_tail_calibrated(model, state, league, start, provider):
            rows[int(row["match_id"])] = row
        LOGGER.info("Liga %s/%s (%s): %s filas acumuladas",
                    index, len(leagues), slug, len(rows))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "version": OFFICIAL_CACHE_VERSION, "rows": rows,
    }, default=str), encoding="utf-8")
    return rows


def _market_probability(
    name: str, official: dict[str, Any], aggregate: dict[str, Any],
    markov: dict[str, Any],
) -> float:
    """Obtiene la probabilidad del motor responsable de cada mercado."""

    if name == "1x2":
        return float(max(official["candidate_calibrated"].values()))
    if name == "btts":
        return float(official["baseline"][name])
    if name == "over_2_5":
        return float(official["candidate"][name])
    if name in AGGREGATE:
        return float(aggregate["markets"][name]["probability"])
    return float(markov["probabilities"][name])


def _market_baseline(
    name: str, official: dict[str, Any], aggregate: dict[str, Any],
    markov: dict[str, Any],
) -> float:
    """Obtiene la referencia baseline correspondiente."""

    if name == "1x2":
        return float(max(official["baseline"].values()))
    if name in {"over_2_5", "btts"}:
        return float(official["baseline"][name])
    if name in AGGREGATE:
        return float(aggregate["markets"][name]["baseline_probability"])
    return float(markov["baselines"][name])


def _score_match(
    match: dict[str, Any], official: dict[str, Any],
    aggregate: dict[str, Any], markov: dict[str, Any],
) -> dict[str, Any]:
    """Compara los mercados recalibrados contra outcomes reconciliados."""

    actual = {**_goal_actual(match), **_outcomes(_counts(match))}
    markets: dict[str, dict[str, Any]] = {}
    for name in MARKETS:
        if name == "1x2":
            scored = _score_1x2(official["candidate_calibrated"], actual["1x2"])
            raw = _score_1x2(official["candidate"], actual["1x2"])
            scored["uncalibrated"] = {
                "probabilities": raw["probabilities"],
                "predicted": raw["predicted"], "correct": raw["correct"],
                "confidence": raw["confidence"], "log_loss": raw["log_loss"],
                "brier": raw["brier"],
                "normalized_brier": raw["normalized_brier"],
            }
            scored["temperature"] = float(official["temperature"])
            scored["argmax_preserved"] = raw["predicted"] == scored["predicted"]
            base_probs = {
                key: float(official["baseline"][key])
                for key in ("home", "draw", "away")}
            base_pred = max(base_probs, key=base_probs.get)
            scored["baseline_predicted"] = base_pred
            scored["baseline_correct"] = base_pred == actual["1x2"]
            scored["baseline_log_loss"] = -math.log(
                max(base_probs[actual["1x2"]], 1e-12))
            scored["baseline_brier"] = sum(
                (value - float(key == actual["1x2"])) ** 2
                for key, value in base_probs.items())
            scored["baseline_normalized_brier"] = scored["baseline_brier"] / 2.0
        else:
            probability = _market_probability(name, official, aggregate, markov)
            scored = _score(probability, bool(actual[name]))
            base = _score(
                _market_baseline(name, official, aggregate, markov),
                bool(actual[name]))
            scored.update({
                "baseline_correct": base["correct"],
                "baseline_log_loss": base["log_loss"],
                "baseline_brier": base["brier"],
                "baseline_normalized_brier": base["normalized_brier"]})
        scored["baseline_probability"] = _market_baseline(
            name, official, aggregate, markov)
        scored["model"] = (
            "dixon_coles_kalman_calibrated" if name == "1x2"
            else "dixon_coles_kalman" if name == "over_2_5"
            else "structural_poisson_baseline" if name == "btts"
            else "phase84a_team_count" if name in AGGREGATE
            else "phase88_team_market_markov")
        markets[name] = scored
    correct = sum(bool(value["correct"]) for value in markets.values())
    return {
        "match_id": int(match["match_id"]),
        "match_date": str(match["match_date"]),
        "league_slug": str(match["league_slug"]),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "observed_score": actual["score"],
        "correct_markets": correct, "accuracy": correct / len(MARKETS),
        "all_markets_correct": correct == len(MARKETS),
        "no_markets_correct": correct == 0, "markets": markets,
        "play_by_play": _pbp_evidence(match),
    }


def _confidence(value: dict[str, Any]) -> float:
    """Devuelve la probabilidad asignada a la decisión emitida."""

    if "confidence" in value:
        return float(value["confidence"])
    probability = float(value["probability"])
    return probability if value["predicted"] else 1.0 - probability


def _aggregate_metrics(
    rows: list[dict[str, Any]], names: tuple[str, ...],
) -> dict[str, Any]:
    """Resume acierto, confianza, log-loss y Brier por familia."""

    values = [row["markets"][name] for row in rows for name in names]
    mixed = "1x2" in names and any(name != "1x2" for name in names)
    return {
        "predictions": len(values),
        "accuracy": float(np.mean([v["correct"] for v in values])),
        "mean_confidence": float(np.mean([_confidence(v) for v in values])),
        "log_loss": float(np.mean([v["log_loss"] for v in values])),
        "brier": None if mixed else float(np.mean([v["brier"] for v in values])),
        "normalized_brier": float(np.mean([v["normalized_brier"] for v in values])),
        "baseline_accuracy": float(np.mean([v["baseline_correct"] for v in values])),
        "baseline_log_loss": float(np.mean([v["baseline_log_loss"] for v in values])),
        "baseline_brier": None if mixed else float(np.mean([
            v["baseline_brier"] for v in values])),
        "baseline_normalized_brier": float(np.mean([
            v["baseline_normalized_brier"] for v in values])),
    }


def _market_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas independientes para cada mercado."""

    output: dict[str, Any] = {}
    for name in MARKETS:
        values = [row["markets"][name] for row in rows]
        output[name] = {
            "predictions": len(values),
            "accuracy": float(np.mean([v["correct"] for v in values])),
            "mean_confidence": float(np.mean([_confidence(v) for v in values])),
            "log_loss": float(np.mean([v["log_loss"] for v in values])),
            "brier": float(np.mean([v["brier"] for v in values])),
            "normalized_brier": float(np.mean([
                v["normalized_brier"] for v in values])),
            "baseline_accuracy": float(np.mean([v["baseline_correct"] for v in values])),
            "baseline_log_loss": float(np.mean([v["baseline_log_loss"] for v in values])),
            "baseline_brier": float(np.mean([v["baseline_brier"] for v in values])),
            "baseline_normalized_brier": float(np.mean([
                v["baseline_normalized_brier"] for v in values])),
            "model": values[0]["model"],
        }
    return output


def _paired_ci(deltas: np.ndarray) -> dict[str, Any]:
    """Bootstrap pareado con el partido completo como unidad IID."""

    rng = np.random.default_rng(SEED)
    sample = rng.choice(
        deltas, size=(BOOTSTRAP, len(deltas)), replace=True).mean(axis=1)
    low, high = (float(v) for v in np.quantile(sample, [0.025, 0.975]))
    return {
        "delta": float(deltas.mean()), "ci95": [low, high],
        "confirmed": bool(low > 0.0 or high < 0.0),
    }


def _calibration_effect(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aísla lo que aporta la temperatura sobre el mismo 1X2."""

    calibrated_ll = np.array([r["markets"]["1x2"]["log_loss"] for r in rows])
    raw_ll = np.array([
        r["markets"]["1x2"]["uncalibrated"]["log_loss"] for r in rows])
    calibrated_br = np.array([
        r["markets"]["1x2"]["normalized_brier"] for r in rows])
    raw_br = np.array([
        r["markets"]["1x2"]["uncalibrated"]["normalized_brier"] for r in rows])
    return {
        "temperature": float(rows[0]["markets"]["1x2"]["temperature"]),
        "argmax_preserved_all": all(
            r["markets"]["1x2"]["argmax_preserved"] for r in rows),
        "accuracy_calibrated": float(np.mean([
            r["markets"]["1x2"]["correct"] for r in rows])),
        "accuracy_uncalibrated": float(np.mean([
            r["markets"]["1x2"]["uncalibrated"]["correct"] for r in rows])),
        "log_loss_calibrated": float(calibrated_ll.mean()),
        "log_loss_uncalibrated": float(raw_ll.mean()),
        "log_loss_improvement": _paired_ci(raw_ll - calibrated_ll),
        "normalized_brier_calibrated": float(calibrated_br.mean()),
        "normalized_brier_uncalibrated": float(raw_br.mean()),
        "normalized_brier_improvement": _paired_ci(raw_br - calibrated_br),
    }


def _reliability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Construye el diagrama de fiabilidad del 1X2, crudo y calibrado."""

    edges = [0.0, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 1.0]

    def _cells(key: str | None) -> list[dict[str, Any]]:
        points = []
        for row in rows:
            market = row["markets"]["1x2"]
            source = market["uncalibrated"] if key else market
            points.append((float(source["confidence"]), bool(source["correct"])))
        cells = []
        for low, high in zip(edges, edges[1:]):
            bucket = [p for p in points if low <= p[0] < high]
            if not bucket:
                continue
            cells.append({
                "bucket_low": low, "bucket_high": high, "count": len(bucket),
                "declared": float(np.mean([p[0] for p in bucket])),
                "observed": float(np.mean([p[1] for p in bucket])),
            })
        return cells

    return {"calibrated": _cells(None), "uncalibrated": _cells("uncalibrated")}


def _by_league(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Desglosa acierto agregado y de 1X2 por liga."""

    slugs = sorted({row["league_slug"] for row in rows})
    output = []
    for slug in slugs:
        subset = [row for row in rows if row["league_slug"] == slug]
        values = [m for row in subset for m in row["markets"].values()]
        output.append({
            "league_slug": slug, "matches": len(subset),
            "accuracy_all_markets": float(np.mean([v["correct"] for v in values])),
            "accuracy_1x2": float(np.mean([
                row["markets"]["1x2"]["correct"] for row in subset])),
            "baseline_1x2": float(np.mean([
                row["markets"]["1x2"]["baseline_correct"] for row in subset])),
        })
    return sorted(output, key=lambda row: -row["matches"])


def _distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume la distribución de aciertos por partido."""

    counts = [row["correct_markets"] for row in rows]
    histogram = {str(value): counts.count(value) for value in range(len(MARKETS) + 1)}
    return {
        "histogram_correct_markets": histogram,
        "mean": float(np.mean(counts)), "median": float(np.median(counts)),
        "std": float(np.std(counts)),
        "p05": float(np.percentile(counts, 5)),
        "p95": float(np.percentile(counts, 95)),
    }


PHASE105_ROWS = (
    ROOT / "artifacts/phase_105_historical_1000_complete/ranked_1000_predictions.json")


def _versus_phase_105(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Contrasta contra la corrida pre-calibración sobre partidos idénticos.

    La comparación se restringe a la intersección de `match_id` porque el
    universo elegible cambió: Fase 84A se regeneró bajo DEC-110 y la
    reparación de cobertura, de modo que 60 de los 1,000 partidos de Fase 105
    ya no traen las cuatro líneas de conteo. Comparar los agregados de las dos
    corridas sobre poblaciones distintas confundiría el efecto de la
    calibración con un cambio de muestra, así que aquí se emparejan partido a
    partido y se descarta lo que no está en ambas.
    """

    if not PHASE105_ROWS.exists():
        return None
    previous = {int(row["match_id"]): row for row in _read_json(PHASE105_ROWS)}
    paired = [(row, previous[int(row["match_id"])]) for row in rows
              if int(row["match_id"]) in previous]
    if not paired:
        return None
    by_market: dict[str, Any] = {}
    for name in MARKETS:
        after = [current["markets"][name] for current, _ in paired]
        before = [old["markets"][name] for _, old in paired]
        by_market[name] = {
            "accuracy_before": float(np.mean([v["correct"] for v in before])),
            "accuracy_after": float(np.mean([v["correct"] for v in after])),
            "log_loss_before": float(np.mean([v["log_loss"] for v in before])),
            "log_loss_after": float(np.mean([v["log_loss"] for v in after])),
            "log_loss_improvement": _paired_ci(np.array([
                b["log_loss"] - a["log_loss"] for a, b in zip(after, before)])),
        }
    return {
        "source": "artifacts/phase_105_historical_1000_complete",
        "matched_matches": len(paired),
        "note": (
            "Fase 105 corrió el 2026-08-07 con peso de mezcla 0.8 y sin "
            "temperatura sobre 1X2. Sólo la cadena de goles (1x2, over_2_5) "
            "fue recalibrada: los mercados de equipo comparten entrada, así "
            "que cualquier diferencia en ellos refleja la regeneración de "
            "Fase 84A/88 bajo DEC-110, no la calibración."
        ),
        "by_market": by_market,
    }


def _write(name: str, payload: Any) -> None:
    """Publica artefactos JSON deterministas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta predicción recalibrada, settlement y reporte de 1,000 partidos."""

    LOGGER.info("Cargando corpus causal")
    matches = _matches(_read_2024(_team_mapping()) + _read_current())
    eligible, quality = _quality_profile(matches)
    confirmation = [
        row for row in matches
        if row["split"] == "confirmation" and row["league_slug"] in eligible]
    aggregate = {int(row["match_id"]): row for row in _read_json(PHASE84)}
    LOGGER.info("Ejecutando cadena oficial recalibrada")
    official = _official_rows()
    # Fase 84A se regeneró el 2026-08-13 bajo DEC-110 (`shots + goals`) y la
    # reparación de sesgo de cobertura: corners y tiros ya no están presentes
    # en las 1,895 filas de confirmation sino en ~1,310. Fase 105 sólo exigía
    # que el partido existiera en el artefacto, no que trajera las cuatro
    # líneas, así que aquí el filtro es explícito -un partido sin todas ellas
    # no puede puntuar los once mercados y quedaría fuera del conteo con un
    # `KeyError`, no con una fila incompleta silenciosa-.
    candidate_ids = [
        int(row["match_id"]) for row in confirmation
        if int(row["match_id"]) in official
        and int(row["match_id"]) in aggregate
        and all(name in aggregate[int(row["match_id"])]["markets"]
                for name in AGGREGATE)]
    eligible_pool = len(candidate_ids)
    target_ids = set(candidate_ids[-1000:])
    if len(target_ids) != 1000:
        raise ValueError(f"phase134_official_coverage:{len(target_ids)}")
    target_matches = [row for row in confirmation if int(row["match_id"]) in target_ids]
    LOGGER.info("Ejecutando cadenas de mercados de equipo")
    markov = _markov_predictions(
        [row for row in matches if row["league_slug"] in eligible], target_ids)
    scored = [
        _score_match(
            row, official[int(row["match_id"])],
            aggregate[int(row["match_id"])], markov[int(row["match_id"])])
        for row in target_matches]
    scored.sort(key=lambda row: (-row["correct_markets"], row["match_date"], row["match_id"]))
    result: dict[str, Any] = {
        "classification": "historical_recalibrated_model_evaluation",
        "generated_for": "2026-08-21",
        "calibration": {
            "blend_weight_dixon_coles": BLEND_WEIGHT_DIXON_COLES,
            "decisions": ["DEC-200", "DEC-201"],
            "temperature_fitted_on": "phase_74_selection_split",
            "scored_split": "confirmation",
            "leakage_note": (
                "La temperatura se ajustó sobre `selection`, disjunto de los "
                "partidos de `confirmation` puntuados aquí."
            ),
        },
        "coverage": {
            "matches": len(scored),
            "leagues": len({row["league_slug"] for row in scored}),
            "decisions": len(scored) * len(MARKETS),
            "eligible_pool": eligible_pool,
            "first_match_date": min(row["match_date"] for row in scored),
            "last_match_date": max(row["match_date"] for row in scored),
        },
        "models": {
            "official_goals": "dixon_coles_kalman_blend0.642848_temperature",
            "btts": "structural_poisson_baseline",
            "aggregate_markets": "phase84a_team_count",
            "temporal_markets": "phase88_team_market_markov",
        },
        "metrics_by_market": _market_metrics(scored),
        "families": {
            "official_goal_chain": _aggregate_metrics(scored, ("1x2", "over_2_5")),
            "btts_baseline": _aggregate_metrics(scored, ("btts",)),
            "aggregate_team_markets": _aggregate_metrics(scored, AGGREGATE),
            "markov_temporal_markets": _aggregate_metrics(scored, MARKOV),
            "all_markets": _aggregate_metrics(scored, MARKETS),
        },
        "calibration_effect_1x2": _calibration_effect(scored),
        "reliability_1x2": _reliability(scored),
        "by_league": _by_league(scored),
        "distribution": _distribution(scored),
        "audit": {
            "target_match_data_used": False,
            "predictions_before_updates": True,
            "pbp_reconciled": True,
            "quality_profile_fit_only": True,
            "all_context_strictly_prior": all(
                row["play_by_play"]["context_strictly_prior"] for row in scored),
            "official_cache_version": OFFICIAL_CACHE_VERSION,
        },
        "data_quality": quality,
    }
    result["versus_phase_105"] = _versus_phase_105(scored)
    result["perfect_100_percent"] = [row for row in scored if row["all_markets_correct"]]
    result["zero_0_percent"] = [row for row in scored if row["no_markets_correct"]]
    _write("final_report.json", {
        key: value for key, value in result.items()
        if key not in {"perfect_100_percent", "zero_0_percent"}})
    _write("ranked_1000_predictions.json", scored)
    _write("perfect_100_percent.json", result["perfect_100_percent"])
    _write("zero_0_percent.json", result["zero_0_percent"])
    _write("hashes.json", {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.glob("*.json")) if path.name != "hashes.json"})
    result["predictions_ranked"] = scored
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run()
    assert report["coverage"]["matches"] == 1000
    assert report["coverage"]["decisions"] == 1000 * len(MARKETS)
    LOGGER.info("Fase 134 completada: %s", report["classification"])

# Version: 1.0.0
# Created: 2026-08-21
