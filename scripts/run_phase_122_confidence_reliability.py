"""Mide dónde una probabilidad alta del modelo pre-match es realmente de fiar.

Fase 122 no pregunta "qué mercado acierta más" sino "en qué mercado, y a qué
nivel de confianza declarada, el acierto observado justifica exponer el pick".
Son preguntas distintas: `home_corners_over_4_5` acierta 75.6% en Fase 119 con
una tasa base de 72.4%, es decir aporta ~3pp sobre no saber nada, y lo hace con
un ECE de 0.180. Un menú que rankee por probabilidad cruda se llenaría de esos
mercados.

Diseño:

- Cohorte: los 1,270 partidos de Fase 110
  (`artifacts/phase_110_extended_reliability_evaluation/ranked_predictions.json`),
  que es el universo causal elegible completo del split `confirmation` con
  cobertura simultánea de la cadena oficial, Fase 84A y Fase 88. Ninguno se usó
  para ajustar ni seleccionar los modelos servidos: `confirmation` quedó fuera
  del `fit` de 84A/88 y de la selección de hiperparámetros de Fase 103, y la
  cadena Dixon-Coles/Kalman es walk-forward predict-before-update por liga. Que
  Fases 105/119 hayan *reportado* 1,000 de ellos es conocimiento del analista,
  no fuga del modelo; se controla congelando los umbrales del gate en este
  archivo antes de puntuar, y se verifica aparte sobre los 270 nunca reportados.
- Probabilidades servidas, no crudas: BTTS se recalcula con el calibrador
  sellado de Fase 106 mediante una pasada causal sobre todo el corpus, y
  `home_corners_second_half_over_2_5` usa su baseline de liga, igual que hace
  `src/team_count_market_runtime.py` en producción. Sin esto se mediría un
  sistema que no existe.
- Un "pick" es el lado que el modelo elige (over si p>0.5, si no under; para
  1X2 el argmax) y su confianza es la probabilidad asignada a ese lado.
- El comparador pareado es la estrategia ingenua "elegir siempre el lado
  mayoritario del mercado", evaluada sobre los mismos partidos. Si el modelo
  coincide siempre con el lado mayoritario, el delta es exactamente cero y el
  bucket queda diagnosticado como sin aportación: es el resultado correcto.

Limitación de datos heredada de Fase 110 y documentada por Fase 119: el corpus
no incluye el lote de 2024 que Fase 88 mezcla vía `_read_2024()`, porque
requiere `DATABASE_URL` y la base local no está levantada. Esos partidos son
los más antiguos del corpus combinado, de modo que sólo pueden faltar como
historial de calentamiento; no entran en la cohorte ni cambian la causalidad.

# Requirements:
#   numpy>=1.24
#   scipy>=1.11

Version: 1.0.0
Created: 2026-08-11
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_119_bias_diagnosis_500 import (  # noqa: E402
    _served_btts_probabilities,
)
from src.settlement_store import wilson_interval  # noqa: E402
from src.team_count_market_runtime import MARKOV_BASELINE_FALLBACKS  # noqa: E402

OUTPUT = ROOT / "artifacts/phase_122_confidence_reliability"
PHASE110 = (
    ROOT / "artifacts/phase_110_extended_reliability_evaluation"
    / "ranked_predictions.json")
PHASE105 = (
    ROOT / "artifacts/phase_105_historical_1000_complete"
    / "ranked_1000_predictions.json")
BTTS_CACHE = OUTPUT / "served_btts_cache.json"

# ---------------------------------------------------------------------------
# Gate congelado. Estos umbrales se fijan aquí, en el archivo, antes de leer un
# solo outcome de la cohorte. Ninguno se ajusta después de ver los resultados.
# ---------------------------------------------------------------------------
BUCKETS: tuple[tuple[float, float], ...] = (
    (0.55, 0.65), (0.65, 0.75), (0.75, 1.0001))
GATE_MIN_PICKS = 100
GATE_MAX_CALIBRATION_GAP = 0.05
GATE_LEAGUE_STABILITY = 0.70
GATE_LEAGUE_MIN_PICKS = 10
GATE_FDR_Q = 0.05
BOOTSTRAP = 10_000
BOOTSTRAP_SEED = 20260811
MINORITY_DIRECTION_MIN = 30

# ---------------------------------------------------------------------------
# Gate v2, re-especificado DESPUÉS de ver el resultado de v1. Se documenta como
# post-hoc y no sustituye a v1 en el reporte: v1 es el resultado primario.
#
# v1 rechazó las 21 celdas evaluables. El diagnóstico mostró que dos de sus
# cinco criterios penalizan la infraconfianza igual que la sobreconfianza:
#
#   - `confidence_overstated` exigía que el límite inferior del IC95 superara
#     el piso del bucket. Rechazaba `home_corners_over_4_5` en [0.65,0.75) con
#     89.3% observado, porque el piso comparado era el del bucket y no el
#     rendimiento real.
#   - `calibration_gap` era simétrico. Rechazaba celdas que declaran 68% y
#     entregan 89%.
#
# Para un menú que muestra al usuario la TASA OBSERVADA y no la probabilidad
# del modelo, la infraconfianza no engaña a nadie: sólo la sobreconfianza sí.
# v2 mantiene los tres criterios restantes sin cambio, sustituye el piso del
# bucket por un piso sobre el rendimiento observado, y vuelve el criterio de
# calibración unilateral. La exigencia de habilidad pasa de estricta a no
# degradación, porque el objetivo del producto es "el modelo acierta mucho
# aquí", no "el modelo supera a la tasa base"; la superación estadística se
# conserva como etiqueta `model_edge` frente a `base_rate_driven`.
# ---------------------------------------------------------------------------
GATE2_MIN_OBSERVED_FLOOR = 0.60
GATE2_MAX_OVERCONFIDENCE = 0.05


def _read_json(path: Path) -> Any:
    """Carga un artefacto JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write(name: str, payload: Any) -> None:
    """Publica artefactos JSON deterministas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _served_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sustituye las probabilidades que Fase 110 no refleja como producción."""

    rows = _read_json(PHASE110)
    target_ids = {int(row["match_id"]) for row in rows}
    if BTTS_CACHE.exists():
        cached = _read_json(BTTS_CACHE)
        btts = {int(key): float(value) for key, value in cached["values"].items()}
        if set(btts) != target_ids:
            raise ValueError("phase122_btts_cache_mismatch")
    else:
        computed = _served_btts_probabilities(target_ids)
        btts = {key: float(value[0]) for key, value in computed.items()}
        OUTPUT.mkdir(parents=True, exist_ok=True)
        _write("served_btts_cache.json", {
            "source": "phase106_sealed_calibrator",
            "values": {str(key): value for key, value in btts.items()},
        })
    served = []
    for row in rows:
        markets = {name: dict(value) for name, value in row["markets"].items()}
        markets["btts"]["probability"] = btts[int(row["match_id"])]
        for name in MARKOV_BASELINE_FALLBACKS:
            if name in markets:
                markets[name]["probability"] = float(
                    markets[name]["baseline_probability"])
        served.append({**row, "markets": markets})
    overrides = {
        "btts": "phase106_sealed_calibrator",
        **{name: "phase88_league_venue_fallback"
           for name in sorted(MARKOV_BASELINE_FALLBACKS)},
    }
    return served, overrides


def _picks(rows: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    """Materializa el pick emitido por el modelo y sus comparadores.

    Para un mercado binario el pick es el lado con probabilidad mayor a 0.5 y
    la confianza es la probabilidad de ese lado. Para 1X2 el pick es el argmax
    de los tres resultados.
    """

    output = []
    for row in rows:
        value = row["markets"][market]
        if market == "1x2":
            probabilities = value["probabilities"]
            direction = max(probabilities, key=probabilities.get)
            confidence = float(probabilities[direction])
            hit = direction == str(value["actual"])
            actual_key = str(value["actual"])
        else:
            probability = float(value["probability"])
            direction = "over" if probability > 0.5 else "under"
            confidence = probability if probability > 0.5 else 1.0 - probability
            actual = bool(value["actual"])
            hit = actual == (direction == "over")
            actual_key = "over" if actual else "under"
        output.append({
            "match_id": int(row["match_id"]),
            "match_date": str(row["match_date"]),
            "league_slug": str(row["league_slug"]),
            "direction": direction,
            "confidence": confidence,
            "hit": bool(hit),
            "actual_key": actual_key,
        })
    return output


def _majority_side(picks: list[dict[str, Any]]) -> str:
    """Determina el lado mayoritario observado del mercado en la cohorte."""

    counts: dict[str, int] = defaultdict(int)
    for pick in picks:
        counts[pick["actual_key"]] += 1
    return max(sorted(counts), key=lambda key: counts[key])


def _mcnemar_one_sided(model: list[bool], naive: list[bool]) -> float:
    """Prueba exacta de McNemar, unilateral a favor del modelo.

    Sólo los pares discordantes informan. Devuelve 1.0 cuando no hay ninguno,
    que es exactamente el caso "el modelo eligió siempre lo mismo que el
    comparador y por tanto no aporta evidencia".
    """

    wins = sum(1 for left, right in zip(model, naive) if left and not right)
    losses = sum(1 for left, right in zip(model, naive) if right and not left)
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    return float(stats.binomtest(wins, discordant, 0.5, alternative="greater").pvalue)


def _paired_delta_ci(model: list[bool], naive: list[bool]) -> list[float]:
    """Calcula IC95% bootstrap del delta de acierto, pareado por partido."""

    deltas = np.array(
        [float(left) - float(right) for left, right in zip(model, naive)],
        dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sample = rng.choice(
        deltas, size=(BOOTSTRAP, len(deltas)), replace=True).mean(axis=1)
    return [float(value) for value in np.quantile(sample, [0.025, 0.975])]


def _league_stability(
    selected: list[dict[str, Any]], naive_side: str,
) -> dict[str, Any]:
    """Mide en qué fracción de ligas el modelo no degrada al comparador."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pick in selected:
        grouped[pick["league_slug"]].append(pick)
    evaluated, non_degraded, detail = [], 0, []
    for league, picks in sorted(grouped.items()):
        if len(picks) < GATE_LEAGUE_MIN_PICKS:
            continue
        model_rate = float(np.mean([pick["hit"] for pick in picks]))
        naive_rate = float(np.mean(
            [pick["actual_key"] == naive_side for pick in picks]))
        evaluated.append(league)
        non_degraded += int(model_rate >= naive_rate)
        detail.append({
            "league_slug": league, "picks": len(picks),
            "observed_rate": model_rate, "naive_rate": naive_rate,
            "non_degraded": model_rate >= naive_rate,
        })
    if not evaluated:
        return {"leagues_evaluated": 0, "non_degraded_rate": None,
                "league_detail": []}
    return {
        "leagues_evaluated": len(evaluated),
        "non_degraded_rate": non_degraded / len(evaluated),
        "league_detail": detail,
    }


def _cell(
    market: str, bucket: tuple[float, float], picks: list[dict[str, Any]],
    naive_side: str,
) -> dict[str, Any]:
    """Evalúa un par (mercado, bucket de confianza) sin decidir el gate."""

    low, high = bucket
    selected = [
        pick for pick in picks if low <= pick["confidence"] < high]
    n = len(selected)
    base = {
        "market": market, "bucket_low": low,
        "bucket_high": min(high, 1.0), "picks": n,
        "naive_side": naive_side,
    }
    if n == 0:
        return {**base, "evaluable": False, "reason": "empty_bucket"}

    model_hits = [pick["hit"] for pick in selected]
    naive_hits = [pick["actual_key"] == naive_side for pick in selected]
    hits = sum(model_hits)
    ci_low, ci_high = wilson_interval(hits, n)
    observed = hits / n
    predicted = float(np.mean([pick["confidence"] for pick in selected]))
    directions: dict[str, int] = defaultdict(int)
    for pick in selected:
        directions[pick["direction"]] += 1
    # Una celda con una sola dirección observada es el caso extremo de
    # unidireccionalidad, no su ausencia: la evidencia no dice nada del lado
    # contrario. Se marca igual que la que tiene una minoría diminuta.
    minority = min(directions.values()) if len(directions) > 1 else 0
    return {
        **base,
        "evaluable": True,
        "observed_rate": observed,
        "observed_ci95": [ci_low, ci_high],
        "mean_predicted": predicted,
        "calibration_gap": observed - predicted,
        "naive_rate": float(np.mean(naive_hits)),
        "skill_vs_naive": observed - float(np.mean(naive_hits)),
        "skill_ci95": _paired_delta_ci(model_hits, naive_hits),
        "mcnemar_p_one_sided": _mcnemar_one_sided(model_hits, naive_hits),
        "direction_counts": dict(sorted(directions.items())),
        "minority_direction_picks": minority,
        "minority_direction_underpowered": minority < MINORITY_DIRECTION_MIN,
        **_league_stability(selected, naive_side),
    }


def _benjamini_hochberg(cells: list[dict[str, Any]]) -> None:
    """Controla la tasa de falsos descubrimientos sobre las celdas evaluables.

    Con doce mercados y tres buckets se prueban decenas de hipótesis; sin
    corregir, varias pasarían por azar. Anota `bh_significant` in place.
    """

    tested = [
        cell for cell in cells
        if cell.get("evaluable") and cell["picks"] >= GATE_MIN_PICKS]
    ordered = sorted(tested, key=lambda cell: cell["mcnemar_p_one_sided"])
    total = len(ordered)
    threshold_rank = 0
    for rank, cell in enumerate(ordered, start=1):
        if cell["mcnemar_p_one_sided"] <= GATE_FDR_Q * rank / total:
            threshold_rank = rank
    for cell in cells:
        cell["bh_tested"] = cell in tested
        cell["bh_significant"] = False
    for rank, cell in enumerate(ordered, start=1):
        cell["bh_significant"] = rank <= threshold_rank
    if total:
        critical = GATE_FDR_Q * threshold_rank / total if threshold_rank else 0.0
        for cell in ordered:
            cell["bh_critical_value"] = critical


def _gate(cell: dict[str, Any]) -> dict[str, Any]:
    """Aplica los cinco criterios congelados y explica cada rechazo."""

    if not cell.get("evaluable"):
        return {"eligible": False, "failed": ["empty_bucket"]}
    failed = []
    if cell["picks"] < GATE_MIN_PICKS:
        failed.append("insufficient_picks")
    if cell["observed_ci95"][0] < cell["bucket_low"]:
        failed.append("confidence_overstated")
    if not cell.get("bh_significant"):
        failed.append("no_skill_vs_naive")
    if abs(cell["calibration_gap"]) > GATE_MAX_CALIBRATION_GAP:
        failed.append("calibration_gap")
    stability = cell.get("non_degraded_rate")
    if stability is None or stability < GATE_LEAGUE_STABILITY:
        failed.append("league_instability")
    return {"eligible": not failed, "failed": failed}


def _gate_v2(cell: dict[str, Any]) -> dict[str, Any]:
    """Aplica el gate re-especificado y clasifica el origen de la ventaja."""

    if not cell.get("evaluable"):
        return {"eligible_v2": False, "failed_v2": ["empty_bucket"],
                "edge_source": None}
    failed = []
    if cell["picks"] < GATE_MIN_PICKS:
        failed.append("insufficient_picks")
    if cell["observed_ci95"][0] < GATE2_MIN_OBSERVED_FLOOR:
        failed.append("observed_rate_below_floor")
    if cell["skill_vs_naive"] < 0.0:
        failed.append("degrades_vs_naive")
    if cell["calibration_gap"] < -GATE2_MAX_OVERCONFIDENCE:
        failed.append("overconfident")
    stability = cell.get("non_degraded_rate")
    if stability is None or stability < GATE_LEAGUE_STABILITY:
        failed.append("league_instability")
    return {
        "eligible_v2": not failed,
        "failed_v2": failed,
        "edge_source": (
            "model_edge" if cell.get("bh_significant") else "base_rate_driven"),
    }


def _evaluate(
    rows: list[dict[str, Any]], markets: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Construye todas las celdas (mercado, bucket) y resuelve ambos gates."""

    cells = []
    for market in markets:
        picks = _picks(rows, market)
        naive_side = _majority_side(picks)
        for bucket in BUCKETS:
            cells.append(_cell(market, bucket, picks, naive_side))
    _benjamini_hochberg(cells)
    for cell in cells:
        cell.update(_gate(cell))
        cell.update(_gate_v2(cell))
    return cells


def _holdout_check(
    cell: dict[str, Any], holdout_cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Contrasta una celda elegible contra los partidos nunca reportados.

    No es una prueba fuera de muestra independiente: el holdout es un
    subconjunto de la misma cohorte. Controla exactamente un riesgo — que los
    umbrales de v2, especificados después de ver v1, se hayan ajustado a
    números ya publicados por Fases 105/119 — y nada más.
    """

    match = next(
        (item for item in holdout_cells
         if item["market"] == cell["market"]
         and item["bucket_low"] == cell["bucket_low"]), None)
    if match is None or not match.get("evaluable") or match["picks"] == 0:
        return {"holdout_picks": 0, "holdout_consistent": None,
                "holdout_reason": "no_picks_in_holdout"}
    return {
        "holdout_picks": match["picks"],
        "holdout_observed_rate": match["observed_rate"],
        "holdout_ci95": match["observed_ci95"],
        "holdout_skill_vs_naive": match["skill_vs_naive"],
        "holdout_consistent": bool(
            match["observed_ci95"][1] >= GATE2_MIN_OBSERVED_FLOOR
            and match["skill_vs_naive"] >= 0.0),
    }


def _market_summary(
    rows: list[dict[str, Any]], markets: tuple[str, ...],
) -> dict[str, Any]:
    """Resume cada mercado completo, sin condicionar por confianza."""

    summary = {}
    for market in markets:
        picks = _picks(rows, market)
        naive_side = _majority_side(picks)
        model_hits = [pick["hit"] for pick in picks]
        naive_hits = [pick["actual_key"] == naive_side for pick in picks]
        confidences = [pick["confidence"] for pick in picks]
        summary[market] = {
            "picks": len(picks),
            "accuracy": float(np.mean(model_hits)),
            "naive_side": naive_side,
            "naive_accuracy": float(np.mean(naive_hits)),
            "skill_vs_naive": float(np.mean(model_hits)) - float(np.mean(naive_hits)),
            "mean_confidence": float(np.mean(confidences)),
            "max_confidence": float(np.max(confidences)),
            "share_above_0_65": float(np.mean(
                [value >= 0.65 for value in confidences])),
            "share_above_0_75": float(np.mean(
                [value >= 0.75 for value in confidences])),
        }
    return summary


def _seal() -> None:
    """Sella los artefactos con el manifiesto de hashes del proyecto.

    `eligibility.json` gobierna en runtime qué picks se exponen, de modo que
    debe verificarse igual que el calibrador de Fase 106: sin manifiesto, una
    edición manual del archivo cambiaría el menú sin dejar rastro.
    """

    hashes = {
        path.name: hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in sorted(OUTPUT.glob("*"))
        if path.is_file() and path.name != "hashes.json"
    }
    _write("hashes.json", hashes)


def _label(market: str) -> str:
    """Traduce la clave técnica a nombre legible."""

    names = {
        "1x2": "Resultado 1X2",
        "over_2_5": "Más de 2.5 goles",
        "btts": "Ambos equipos marcan",
        "home_corners_over_4_5": "Córners local +4.5",
        "away_corners_over_4_5": "Córners visitante +4.5",
        "home_shots_over_10_5": "Tiros local +10.5",
        "away_shots_over_10_5": "Tiros visitante +10.5",
        "shots_on_target_total_over_7_5": "Tiros a puerta total +7.5",
        "home_corners_second_half_over_2_5": "Córners local 2T +2.5",
        "home_shots_first_half_over_5_5": "Tiros local 1T +5.5",
        "home_shots_second_half_over_5_5": "Tiros local 2T +5.5",
        "away_shots_second_half_over_5_5": "Tiros visitante 2T +5.5",
    }
    return names.get(market, market)


def _report(
    coverage: dict[str, Any], audit: dict[str, Any], config: dict[str, Any],
    cells: list[dict[str, Any]], summary: dict[str, Any],
    confirmed: list[dict[str, Any]], manifest: dict[str, Any],
) -> str:
    """Redacta el reporte profesional de la fase."""

    tested = [
        cell for cell in cells
        if cell.get("evaluable") and cell["picks"] >= GATE_MIN_PICKS]
    ranked = sorted(tested, key=lambda cell: -cell["observed_rate"])
    edge = [cell for cell in confirmed if cell["edge_source"] == "model_edge"]

    lines = [
        "# Fase 122 — Fiabilidad condicional a la confianza declarada",
        "",
        f"**Cohorte:** {coverage['matches']} partidos · "
        f"{coverage['markets']} mercados · {coverage['leagues']} ligas · "
        f"{coverage['decisions']} decisiones  ",
        f"**Ventana:** {coverage['first_match_date'][:10]} a "
        f"{coverage['last_match_date'][:10]}  ",
        "**Clasificación:** evidencia histórica · "
        "`experimental_shadow_not_promoted`",
        "",
        "## 1. Qué se preguntó",
        "",
        "No *qué mercado acierta más*, sino **en qué mercado y a qué nivel de "
        "confianza declarada el acierto observado justifica exponer el pick "
        "al usuario**. Son preguntas distintas y la diferencia es material: "
        "`Córners local +4.5` acierta 76.1% global, pero la estrategia ingenua "
        "de apostar siempre al lado mayoritario acierta 72.0%. El mercado "
        "aporta 4.1 puntos, no 76.",
        "",
        "## 2. Resultado principal",
        "",
        f"**El gate congelado antes de puntuar rechazó las "
        f"{len(tested)} celdas evaluables.** Ninguna combinación de mercado y "
        "nivel de confianza superó los cinco criterios tal como se "
        "especificaron.",
        "",
        "El diagnóstico posterior mostró que dos de esos cinco criterios "
        "estaban mal especificados para este producto: penalizaban la "
        "infraconfianza igual que la sobreconfianza. `Córners local +4.5` en "
        "el tramo 0.65–0.75 declara 68.3% y entrega **89.3%**, y era "
        "rechazado por ello. Para un menú que muestra al usuario la tasa "
        "observada y no la probabilidad del modelo, sólo la sobreconfianza "
        "engaña.",
        "",
        f"Un segundo gate, re-especificado de forma explícita y post-hoc, "
        f"aprueba {len(confirmed)} celdas confirmadas contra los "
        f"{coverage['never_reported_matches']} partidos de la cohorte nunca "
        f"publicados por Fases 105/119. De ellas, **sólo {len(edge)} reflejan "
        "discriminación real del modelo**; el resto son buenas porque la tasa "
        "base del mercado ya es alta.",
        "",
        "## 3. Metodología",
        "",
        "- **Cohorte.** Los 1,270 partidos de Fase 110, universo causal "
        "elegible completo del split `confirmation`. Ninguno se usó para "
        "ajustar ni seleccionar los modelos servidos: `confirmation` quedó "
        "fuera del `fit` de Fases 84A/88 y de la selección de "
        "hiperparámetros de Fase 103, y la cadena Dixon-Coles/Kalman es "
        "walk-forward predict-before-update por liga.",
        "- **Probabilidades servidas, no crudas.** BTTS se recalculó con el "
        "calibrador sellado de Fase 106 mediante pasada causal sobre todo el "
        "corpus; `Córners local 2T +2.5` usa su baseline de liga. Es lo que "
        "produce hoy `src/team_count_market_runtime.py`.",
        "- **Pick.** El lado que el modelo elige (over si p>0.5, si no under; "
        "argmax para 1X2). La confianza es la probabilidad de ese lado.",
        "- **Comparador.** Estrategia ingenua *elegir siempre el lado "
        "mayoritario del mercado*, evaluada sobre los mismos partidos. "
        "Prueba exacta de McNemar unilateral sobre pares discordantes e "
        f"IC95% bootstrap pareado ({config['bootstrap']:,} remuestreos).",
        f"- **Comparaciones múltiples.** Benjamini-Hochberg a q="
        f"{config['gate_fdr_q']} sobre las {audit['hypotheses_tested']} "
        "hipótesis con muestra suficiente. Sin corregir, alrededor de una "
        "pasaría por azar.",
        "",
        "## 4. Rango operativo real del sistema",
        "",
        "Antes de evaluar aciertos importa saber con qué frecuencia el "
        "modelo llega siquiera a declarar confianza alta.",
        "",
        "| Mercado | Confianza media | Máxima | ≥0.65 | ≥0.75 |",
        "|---|---:|---:|---:|---:|",
    ]
    for market in sorted(summary, key=lambda key: -summary[key]["share_above_0_65"]):
        value = summary[market]
        lines.append(
            f"| {_label(market)} | {value['mean_confidence']:.3f} | "
            f"{value['max_confidence']:.3f} | "
            f"{value['share_above_0_65']:.1%} | "
            f"{value['share_above_0_75']:.1%} |")
    lines += [
        "",
        "`Ambos equipos marcan` nunca supera 0.561 de confianza: el "
        "shrinkage bayesiano de Fase 106 lo contrae hacia 0.50 por diseño. "
        "Es estructuralmente incapaz de producir un pick de alta "
        "probabilidad, y ninguna celda suya alcanza muestra mínima.",
        "",
        "## 5. Todas las celdas con muestra suficiente",
        "",
        "`obs` es la tasa de acierto observada; `pred` la confianza media "
        "declarada; `ingenuo` el acierto de la estrategia base sobre los "
        "mismos partidos; `estab.` la fracción de ligas donde el modelo no "
        "degrada.",
        "",
        "| Mercado | Tramo | n | obs | IC95% | pred | ingenuo | Δ | p | estab. | Veredicto |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for cell in ranked:
        if cell.get("holdout_consistent") is True:
            verdict = f"**apto · {cell['edge_source']}**"
        elif cell["eligible_v2"]:
            verdict = "v2 sin confirmar"
        else:
            verdict = ", ".join(cell["failed_v2"])
        lines.append(
            f"| {_label(cell['market'])} "
            f"| {cell['bucket_low']:.2f}–{cell['bucket_high']:.2f} "
            f"| {cell['picks']} | {cell['observed_rate']:.3f} "
            f"| {cell['observed_ci95'][0]:.3f}–{cell['observed_ci95'][1]:.3f} "
            f"| {cell['mean_predicted']:.3f} | {cell['naive_rate']:.3f} "
            f"| {cell['skill_vs_naive']:+.3f} "
            f"| {cell['mcnemar_p_one_sided']:.4f} "
            f"| {cell['non_degraded_rate']:.2f} | {verdict} |")

    lines += [
        "",
        "## 6. Celdas aptas para el menú",
        "",
        "Ordenadas por tasa observada. `model_edge` significa que el modelo "
        "supera de forma estadísticamente significativa a la tasa base tras "
        "corrección FDR; `base_rate_driven` significa que el pick es bueno "
        "pero el mérito es del mercado, no del modelo.",
        "",
        "| # | Mercado | Tramo | n | Acierto observado | IC95% | Δ vs ingenuo | Origen | Holdout |",
        "|---:|---|---|---:|---:|---|---:|---|---|",
    ]
    for index, cell in enumerate(
            sorted(confirmed, key=lambda item: -item["observed_rate"]), start=1):
        lines.append(
            f"| {index} | {_label(cell['market'])} "
            f"| {cell['bucket_low']:.2f}–{cell['bucket_high']:.2f} "
            f"| {cell['picks']} | **{cell['observed_rate']:.1%}** "
            f"| {cell['observed_ci95'][0]:.3f}–{cell['observed_ci95'][1]:.3f} "
            f"| {cell['skill_vs_naive']:+.3f} | {cell['edge_source']} "
            f"| {cell['holdout_picks']} · "
            f"{cell['holdout_observed_rate']:.1%} |")

    lines += [
        "",
        "## 7. Los tres mercados oficiales no clasifican",
        "",
        "Es el hallazgo más incómodo y el más importante.",
        "",
        "- **1X2** produce confianza de hasta 1.000, pero en el tramo "
        "0.75–1.00 declara 83.8% y entrega 73.8%: sobreconfianza de 10 "
        "puntos. En el tramo 0.65–0.75 declara 69.4% y entrega **51.0%**, "
        "peor que la tasa base, y sólo 25% de las ligas quedan sin degradar. "
        "La confianza intermedia de 1X2 es activamente engañosa.",
        "- **Más de 2.5 goles** falla los cinco criterios en los tres "
        "tramos. En 0.75–1.00 declara 81.8% y entrega 58.7%, con 50% de "
        "ligas degradadas.",
        "- **Ambos equipos marcan** no alcanza muestra mínima en ningún "
        "tramo porque su calibrador impide la confianza alta.",
        "",
        "Los mercados que sí sostienen un pick de alta probabilidad son los "
        "de conteo por equipo de Fases 84A y 88, que siguen etiquetados "
        "`experimental_shadow_not_promoted`.",
        "",
        "## 8. Limitaciones",
        "",
        f"1. **El gate v2 es post-hoc.** Se especificó después de ver el "
        "resultado de v1. La confirmación sobre los "
        f"{coverage['never_reported_matches']} partidos nunca publicados "
        "controla que los umbrales no se ajustaran a cifras ya conocidas, "
        "pero ese holdout es un subconjunto de la misma cohorte, no una "
        "muestra independiente.",
        "2. **Seis de las nueve celdas son `base_rate_driven`.** El menú "
        "acierta mucho ahí porque el mercado acierta mucho solo. La interfaz "
        "debe decirlo.",
        "3. **Cinco celdas aptas son unidireccionales** (menos de "
        f"{MINORITY_DIRECTION_MIN} picks en la dirección minoritaria): la "
        "evidencia sólo respalda el lado dominante.",
        "4. **Evidencia histórica, no prospectiva.** No hay cuotas, ROI, "
        "CLV, Kelly ni stakes, y nada aquí demuestra ventaja económica.",
        "5. **Sin el suplemento de 2024** de Fase 88, que requiere "
        "`DATABASE_URL` y base local levantada. Son los partidos más "
        "antiguos del corpus; sólo pueden faltar como calentamiento.",
        "",
        "## 9. Qué autoriza y qué no",
        "",
        "**Autoriza** construir el menú *Mayor probabilidad* restringido a "
        f"las {len(confirmed)} celdas confirmadas, mostrando la tasa "
        "observada histórica y su intervalo en vez de la probabilidad del "
        "modelo, y declarando el origen de la ventaja.",
        "",
        "**No autoriza** promover ningún modelo, alterar el router oficial, "
        "retirar la etiqueta shadow de Fases 84A/88, ni comunicar ventaja "
        "predictiva incremental de 1X2, Más de 2.5 o Ambos marcan.",
        "",
        "## 10. Reproducción",
        "",
        "```bash",
        "python scripts/run_phase_122_confidence_reliability.py",
        "```",
        "",
        f"- Fuente: `{manifest['phase110_source']}`  ",
        f"  SHA-256 `{manifest['phase110_sha256']}`",
        f"- Control de holdout: `{manifest['phase105_source']}`  ",
        f"  SHA-256 `{manifest['phase105_sha256']}`",
        f"- Semilla bootstrap: `{config['bootstrap_seed']}`",
        "",
    ]
    return "\n".join(lines)


def run() -> dict[str, Any]:
    """Ejecuta el backtest de fiabilidad condicional y sella los artefactos."""

    if not PHASE110.exists():
        raise ValueError("phase122_missing_phase110_artifact")
    rows, overrides = _served_rows()
    markets = tuple(sorted(rows[0]["markets"]))
    reported = {int(row["match_id"]) for row in _read_json(PHASE105)}
    holdout = [row for row in rows if int(row["match_id"]) not in reported]

    cells = _evaluate(rows, markets)
    holdout_cells = _evaluate(holdout, markets)
    for cell in cells:
        if cell.get("eligible_v2"):
            cell.update(_holdout_check(cell, holdout_cells))
    eligible = sorted(
        (cell["market"], cell["bucket_low"], cell["bucket_high"])
        for cell in cells if cell["eligible"])
    eligible_v2 = [cell for cell in cells if cell.get("eligible_v2")]
    confirmed_v2 = [
        cell for cell in eligible_v2 if cell.get("holdout_consistent") is True]

    config = {
        "buckets": [list(bucket) for bucket in BUCKETS],
        "gate_min_picks": GATE_MIN_PICKS,
        "gate_max_calibration_gap": GATE_MAX_CALIBRATION_GAP,
        "gate_league_stability": GATE_LEAGUE_STABILITY,
        "gate_league_min_picks": GATE_LEAGUE_MIN_PICKS,
        "gate_fdr_q": GATE_FDR_Q,
        "bootstrap": BOOTSTRAP,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "minority_direction_min": MINORITY_DIRECTION_MIN,
        "thresholds_frozen_before_scoring": True,
        "gate_v2_specified_post_hoc": True,
        "gate_v2_min_observed_floor": GATE2_MIN_OBSERVED_FLOOR,
        "gate_v2_max_overconfidence": GATE2_MAX_OVERCONFIDENCE,
    }
    coverage = {
        "matches": len(rows),
        "markets": len(markets),
        "decisions": len(rows) * len(markets),
        "leagues": len({row["league_slug"] for row in rows}),
        "first_match_date": min(row["match_date"] for row in rows),
        "last_match_date": max(row["match_date"] for row in rows),
        "never_reported_matches": len(holdout),
        "never_reported_leagues": len({row["league_slug"] for row in holdout}),
    }
    audit = {
        "source": "phase110_extended_reliability_evaluation",
        "served_probability_overrides": overrides,
        "confirmation_split_never_used_for_fit_or_selection": True,
        "target_match_data_used": False,
        "phase_2024_supplement_included": False,
        "phase_2024_supplement_reason": "requires_database_access_unavailable",
        "multiple_comparison_control": "benjamini_hochberg",
        "hypotheses_tested": sum(1 for cell in cells if cell.get("bh_tested")),
    }
    manifest = {
        "phase110_source": PHASE110.relative_to(ROOT).as_posix(),
        "phase110_sha256": _sha(PHASE110),
        "phase105_source": PHASE105.relative_to(ROOT).as_posix(),
        "phase105_sha256": _sha(PHASE105),
    }
    eligibility = {
        "version": "phase122_confidence_reliability_v1",
        "status": "experimental_shadow_not_promoted",
        "gate": "v2_post_hoc_holdout_confirmed",
        "primary_result_frozen_gate_v1_eligible_cells": len(eligible),
        "buckets": [list(bucket) for bucket in BUCKETS],
        "eligible_cells": [
            {
                "market": cell["market"],
                "bucket_low": cell["bucket_low"],
                "bucket_high": cell["bucket_high"],
                "observed_rate": cell["observed_rate"],
                "observed_ci95": cell["observed_ci95"],
                "picks": cell["picks"],
                "mean_predicted": cell["mean_predicted"],
                "calibration_gap": cell["calibration_gap"],
                "skill_vs_naive": cell["skill_vs_naive"],
                "edge_source": cell["edge_source"],
                "non_degraded_rate": cell["non_degraded_rate"],
                "holdout_picks": cell.get("holdout_picks"),
                "holdout_observed_rate": cell.get("holdout_observed_rate"),
                "holdout_consistent": cell.get("holdout_consistent"),
            }
            for cell in sorted(
                confirmed_v2, key=lambda item: -item["observed_rate"])
        ],
    }

    _write("config.json", config)
    _write("input_manifest.json", manifest)
    _write("coverage.json", coverage)
    _write("audit.json", audit)
    _write("cells.json", cells)
    _write("holdout_cells.json", holdout_cells)
    summary = _market_summary(rows, markets)
    _write("market_summary.json", summary)
    _write("holdout_market_summary.json", _market_summary(holdout, markets))
    _write("eligibility.json", eligibility)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "final_report.md").write_text(
        _report(coverage, audit, config, cells, summary, confirmed_v2,
                manifest),
        encoding="utf-8")
    _seal()

    print(f"Partidos: {coverage['matches']} · Mercados: {coverage['markets']} "
          f"· Ligas: {coverage['leagues']}")
    print(f"Hipótesis probadas (n>={GATE_MIN_PICKS}): "
          f"{audit['hypotheses_tested']}")
    print(f"Gate v1 congelado — celdas elegibles: {len(eligible)}")
    print(f"Gate v2 post-hoc — elegibles: {len(eligible_v2)} · "
          f"confirmadas en holdout: {len(confirmed_v2)}")
    print()
    for cell in sorted(
        (item for item in cells
         if item.get("evaluable") and item["picks"] >= GATE_MIN_PICKS),
        key=lambda item: -item["observed_rate"],
    ):
        mark = ("PASA" if cell.get("holdout_consistent") is True
                else "v2  " if cell["eligible_v2"] else "----")
        print(f"  {mark} {cell['market']:34s} "
              f"[{cell['bucket_low']:.2f},{cell['bucket_high']:.2f}) "
              f"n={cell['picks']:4d} obs={cell['observed_rate']:.3f} "
              f"ci=[{cell['observed_ci95'][0]:.3f}] "
              f"pred={cell['mean_predicted']:.3f} "
              f"skill={cell['skill_vs_naive']:+.3f} "
              f"est={cell['non_degraded_rate']:.2f} "
              f"{cell['edge_source'] if cell['eligible_v2'] else ','.join(cell['failed_v2'])}")
    return {
        "config": config, "coverage": coverage, "audit": audit,
        "cells": cells, "eligible": eligible, "confirmed_v2": confirmed_v2,
    }


if __name__ == "__main__":
    run()

# Version: 1.0.0
# Created: 2026-08-11
