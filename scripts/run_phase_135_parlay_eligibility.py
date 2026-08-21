"""Congela los criterios de elegibilidad de piernas de parlay (Fase 135).

Deriva, desde el artefacto sellado de Fase 134, qué mercados pueden entrar al
Constructor de Parlays y con qué umbral de confianza. Los umbrales del gate son
**constantes congeladas**, no parámetros ajustados: se fijaron antes de esta
corrida y optimizarlos contra este mismo corpus devolvería la cifra optimista de
dentro de muestra en vez de la real.

Por qué un gate propio y no reusar el de un pick suelto: un parlay multiplica
probabilidades, así que una pierna sobreconfiada no suma su error, lo compone.
Un mercado puede ser buen predictor y mala pierna -el 1X2 tiene ventaja
confirmada de +4.8pp y aun así declara 0.79 y entrega 0.71 en su zona alta-, de
modo que la ventaja sobre la referencia y la calibración se exigen por separado.

El artefacto queda `experimental_shadow_not_promoted`: define qué se muestra en
un menú, no qué apostar. ROI, CLV y Kelly siguen bloqueados.

# Requirements:
#   numpy>=1.24

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

from src.market_exposure_policy import dependency_components  # noqa: E402

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_134_recalibrated_1000/ranked_1000_predictions.json"
OUTPUT = ROOT / "artifacts/phase_135_parlay_eligibility"
VERSION = "phase135_parlay_eligibility_v1"
STATUS = "experimental_shadow_not_promoted"
SEED = 20260821
BOOTSTRAP = 10_000

# --- Gate congelado. No reajustar contra este corpus. ---
CANDIDATE_THRESHOLDS = (0.60, 0.70, 0.80)
MAX_CALIBRATION_GAP = 0.02     # declarada - observada admitida en la zona
MIN_ZONE_SAMPLE = 40           # partidos en la zona para admitir el umbral
MIN_LEAGUE_SHARE = 0.70        # fracción de ligas que debe batir su referencia
MAX_LEAGUE_RANGE = 0.20        # dispersión admitida de acierto entre ligas
MIN_LEAGUE_MATCHES = 30        # tamaño mínimo para que una liga cuente
MAX_LEGS = 5
MIN_LEGS = 2
MAX_LEGS_PER_MATCH = 1         # la correlación intra-partido no está modelada
CORRELATION_THRESHOLD = 0.10   # sólo informativo: se registra, no se relaja


def _read(path: Path) -> Any:
    """Carga un JSON sellado."""

    return json.loads(path.read_text(encoding="utf-8"))


def leg_probability(market: dict[str, Any]) -> float:
    """Devuelve la probabilidad del lado que el modelo efectivamente emite."""

    if "confidence" in market:
        return float(market["confidence"])
    probability = float(market["probability"])
    return probability if market["predicted"] else 1.0 - probability


def _bootstrap_ci(deltas: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    """IC95% pareado con el partido completo como unidad IID."""

    sample = rng.choice(
        deltas, size=(BOOTSTRAP, len(deltas)), replace=True).mean(axis=1)
    low, high = (float(value) for value in np.quantile(sample, [0.025, 0.975]))
    return float(deltas.mean()), low, high


def _wilson(hits: int, total: int) -> tuple[float, float]:
    """Intervalo de Wilson al 95%, estable con muestras chicas."""

    if total <= 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    rate = hits / total
    denominator = 1.0 + z * z / total
    centre = rate + z * z / (2.0 * total)
    spread = z * math.sqrt(
        rate * (1.0 - rate) / total + z * z / (4.0 * total * total))
    return (max(0.0, (centre - spread) / denominator),
            min(1.0, (centre + spread) / denominator))


def _edge(rows: list[dict[str, Any]], name: str,
          rng: np.random.Generator) -> dict[str, Any]:
    """Filtro 1: ventaja sobre la referencia propia del mercado."""

    markets = [row["markets"][name] for row in rows]
    accuracy = np.array([float(m["correct"]) for m in markets])
    baseline = np.array([float(m["baseline_correct"]) for m in markets])
    delta, low, high = _bootstrap_ci(accuracy - baseline, rng)
    return {
        "accuracy": float(accuracy.mean()),
        "baseline_accuracy": float(baseline.mean()),
        "edge": delta, "ci95": [low, high],
        "passes": bool(low > 0.0),
    }


def _calibration(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Filtro 2: calibración medida en la zona donde el mercado se usaría.

    Se evalúa por umbral y no en promedio: un mercado puede estar bien
    calibrado globalmente y mentir justo en el tramo que el constructor
    tomaría, que es el único que importa para una pierna.
    """

    markets = [row["markets"][name] for row in rows]
    zones = []
    selected = None
    for threshold in CANDIDATE_THRESHOLDS:
        points = [(leg_probability(m), bool(m["correct"])) for m in markets
                  if leg_probability(m) >= threshold]
        if len(points) < MIN_ZONE_SAMPLE:
            zones.append({
                "threshold": threshold, "sample": len(points),
                "sufficient_sample": False})
            continue
        declared = float(np.mean([p for p, _ in points]))
        observed = float(np.mean([h for _, h in points]))
        low, high = _wilson(sum(h for _, h in points), len(points))
        gap = declared - observed
        zone = {
            "threshold": threshold, "sample": len(points),
            "sufficient_sample": True, "declared": declared,
            "observed": observed, "gap": gap,
            "observed_ci95": [low, high],
            "passes": bool(gap <= MAX_CALIBRATION_GAP),
        }
        zones.append(zone)
        if zone["passes"] and selected is None:
            selected = threshold
    return {"zones": zones, "selected_threshold": selected,
            "passes": selected is not None}


def _stability(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Filtro 3: la ventaja no puede depender de una liga concreta."""

    leagues: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        leagues.setdefault(row["league_slug"], []).append(row)

    big = {slug: sub for slug, sub in leagues.items()
           if len(sub) >= MIN_LEAGUE_MATCHES}
    if not big:
        return {"passes": False, "reason": "no_league_with_minimum_sample"}
    rates, beats = [], 0
    for sub in big.values():
        markets = [row["markets"][name] for row in sub]
        accuracy = float(np.mean([m["correct"] for m in markets]))
        baseline = float(np.mean([m["baseline_correct"] for m in markets]))
        rates.append(accuracy)
        beats += int(accuracy >= baseline)
    share = beats / len(big)
    spread = max(rates) - min(rates)
    return {
        "leagues": len(big), "beating_baseline": beats,
        "share": share, "min_rate": min(rates), "max_rate": max(rates),
        "range": spread,
        "passes": bool(share >= MIN_LEAGUE_SHARE and spread <= MAX_LEAGUE_RANGE),
    }


def _correlation_matrix(rows: list[dict[str, Any]],
                        names: list[str]) -> list[list[float]]:
    """Correlación de aciertos entre mercados del mismo partido."""

    hits = {n: np.array([float(r["markets"][n]["correct"]) for r in rows])
            for n in names}
    matrix = []
    for left in names:
        line = []
        for right in names:
            if left == right:
                line.append(1.0)
            elif hits[left].std() == 0 or hits[right].std() == 0:
                line.append(0.0)
            else:
                line.append(float(np.corrcoef(hits[left], hits[right])[0, 1]))
        matrix.append(line)
    return matrix


def _evaluate(rows: list[dict[str, Any]], names: list[str],
              rng: np.random.Generator) -> dict[str, Any]:
    """Aplica los tres filtros en orden a cada mercado."""

    verdicts = {}
    for name in names:
        edge = _edge(rows, name, rng)
        calibration = _calibration(rows, name)
        stability = _stability(rows, name)
        failed = [label for label, block in (
            ("edge", edge), ("calibration", calibration),
            ("stability", stability)) if not block["passes"]]
        verdicts[name] = {
            "edge": edge, "calibration": calibration, "stability": stability,
            "eligible": not failed, "failed_filters": failed,
            "threshold": calibration["selected_threshold"] if not failed else None,
        }
    return verdicts


def _simulate(rows: list[dict[str, Any]], pool: dict[str, float],
              legs: int, rng: np.random.Generator,
              trials: int = 6000) -> dict[str, Any] | None:
    """Arma parlays reales y contrasta lo declarado contra lo entregado."""

    declared_total = hits = made = 0.0
    for _ in range(trials):
        picks: list[tuple[str, dict[str, Any]]] = []
        for index in rng.permutation(len(rows)):
            row = rows[int(index)]
            candidates = [(n, row["markets"][n]) for n, t in pool.items()
                          if leg_probability(row["markets"][n]) >= t]
            if not candidates:
                continue
            take = min(MAX_LEGS_PER_MATCH, legs - len(picks), len(candidates))
            chosen = rng.choice(len(candidates), size=take, replace=False)
            picks.extend(candidates[int(i)] for i in chosen)
            if len(picks) >= legs:
                break
        if len(picks) < legs:
            continue
        declared_total += float(np.prod([leg_probability(m) for _, m in picks]))
        hits += float(all(m["correct"] for _, m in picks))
        made += 1
    if made < 200:
        return None
    declared = declared_total / made
    observed = hits / made
    return {
        "parlays": int(made), "declared": declared, "observed": observed,
        "delivery_ratio": observed / declared if declared else 0.0,
    }


def _out_of_sample(rows: list[dict[str, Any]], names: list[str],
                   rng: np.random.Generator) -> dict[str, Any]:
    """Deriva el gate en la mitad temprana y lo mide en la tardía.

    Es la única comprobación fuera de muestra posible sin esperar a una
    cohorte prospectiva. Existe porque medir el gate sobre el mismo corpus que
    lo eligió devuelve un ratio optimista: dentro de muestra sale por encima de
    1.00 y aquí, que es lo real, no llega.
    """

    ordered = sorted(rows, key=lambda r: (r["match_date"], r["match_id"]))
    cut = len(ordered) // 2
    early, late = ordered[:cut], ordered[cut:]
    verdicts = _evaluate(early, names, rng)
    pool = {n: v["threshold"] for n, v in verdicts.items() if v["eligible"]}
    results = {}
    for legs in range(MIN_LEGS, MAX_LEGS + 1):
        outcome = _simulate(late, pool, legs, rng) if pool else None
        if outcome:
            results[str(legs)] = outcome
    return {
        "derivation": {
            "matches": len(early), "from": early[0]["match_date"],
            "to": early[-1]["match_date"],
            "selected_markets": sorted(pool),
        },
        "validation": {
            "matches": len(late), "from": late[0]["match_date"],
            "to": late[-1]["match_date"],
        },
        "by_legs": results,
        "note": (
            "La partición es desigual por densidad de calendario, no por "
            "diseño: la mitad temprana cubre menos tiempo que la tardía."
        ),
    }


def _availability(rows: list[dict[str, Any]],
                  pool: dict[str, float]) -> dict[str, Any]:
    """Mide cuántas piernas elegibles produce un partido cualquiera."""

    counts = [sum(1 for n, t in pool.items()
                  if leg_probability(row["markets"][n]) >= t) for row in rows]
    return {
        "mean_legs_per_match": float(np.mean(counts)) if counts else 0.0,
        "matches_with_any_leg": float(
            np.mean([c >= 1 for c in counts])) if counts else 0.0,
        "histogram": {str(k): int(sum(1 for c in counts if c == k))
                      for k in range(0, max(counts, default=0) + 1)},
    }


def _write(name: str, payload: Any) -> None:
    """Publica un artefacto determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")


def run() -> dict[str, Any]:
    """Deriva, valida y sella el gate de elegibilidad de parlays."""

    rows = _read(SOURCE)
    names = sorted(rows[0]["markets"])
    rng = np.random.default_rng(SEED)
    verdicts = _evaluate(rows, names, rng)
    pool = {n: v["threshold"] for n, v in verdicts.items() if v["eligible"]}
    LOGGER.info("Mercados elegibles: %s", sorted(pool) or "ninguno")

    in_sample = {}
    for legs in range(MIN_LEGS, MAX_LEGS + 1):
        outcome = _simulate(rows, pool, legs, rng) if pool else None
        if outcome:
            in_sample[str(legs)] = outcome

    matrix = _correlation_matrix(rows, names)
    criteria = {
        "version": VERSION,
        "status": STATUS,
        "generated_for": "2026-08-21",
        "source": "artifacts/phase_134_recalibrated_1000",
        "source_matches": len(rows),
        "gate": {
            "max_calibration_gap": MAX_CALIBRATION_GAP,
            "min_zone_sample": MIN_ZONE_SAMPLE,
            "min_league_share": MIN_LEAGUE_SHARE,
            "max_league_range": MAX_LEAGUE_RANGE,
            "min_league_matches": MIN_LEAGUE_MATCHES,
            "candidate_thresholds": list(CANDIDATE_THRESHOLDS),
            "frozen": True,
            "note": (
                "Umbrales fijados antes de la derivación. Reajustarlos contra "
                "este corpus invalida la medición prospectiva."
            ),
        },
        "structural_rules": {
            "min_legs": MIN_LEGS, "max_legs": MAX_LEGS,
            "max_legs_per_match": MAX_LEGS_PER_MATCH,
            "rationale": (
                "La correlación entre mercados del mismo partido es real y no "
                "está modelada (DEC-203: tres autovalores fuera de la banda de "
                "Marchenko-Pastur con el 72.5% de la varianza). Una pierna por "
                "partido evita multiplicar bajo una independencia falsa."
            ),
        },
        "eligible_markets": {n: {"threshold": t} for n, t in sorted(pool.items())},
        "market_verdicts": verdicts,
        "correlation": {
            "markets": names, "matrix": matrix,
            "threshold_recorded": CORRELATION_THRESHOLD,
            "components": [list(c) for c in dependency_components(
                names, matrix, CORRELATION_THRESHOLD)],
        },
        "availability": _availability(rows, pool),
        "in_sample_simulation": in_sample,
        "out_of_sample_simulation": _out_of_sample(rows, names, rng),
        "limitations": [
            "El gate se derivó sobre el mismo corpus donde se simuló dentro de "
            "muestra; la cifra creíble es la de `out_of_sample_simulation`.",
            "No autoriza staking. ROI, CLV y Kelly siguen bloqueados por falta "
            "de cuotas históricas comparables.",
            "Elegibilidad no es promoción: las líneas siguen siendo "
            "experimentales y así deben comunicarse.",
        ],
    }
    _write("criteria.json", criteria)
    _write("hashes.json", {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.glob("*.json")) if path.name != "hashes.json"})
    _report(criteria)
    return criteria


def _report(criteria: dict[str, Any]) -> None:
    """Genera el reporte Markdown del gate."""

    lines = [
        "# Fase 135 — elegibilidad de piernas de parlay", "",
        f"**Estado:** `{criteria['status']}`", "",
        f"Fuente: {criteria['source']} ({criteria['source_matches']} partidos).",
        "", "## Gate congelado", "",
        f"- brecha de calibración admitida: `{MAX_CALIBRATION_GAP}`",
        f"- muestra mínima en zona: `{MIN_ZONE_SAMPLE}`",
        f"- ligas que deben batir su referencia: `{MIN_LEAGUE_SHARE:.0%}`",
        f"- rango máximo entre ligas: `{MAX_LEAGUE_RANGE:.0%}`",
        f"- piernas: `{MIN_LEGS}`–`{MAX_LEGS}`, máximo `{MAX_LEGS_PER_MATCH}` por partido",
        "", "## Veredicto por mercado", "",
        "| Mercado | Ventaja pp | IC95% | Umbral | Filtros fallados |",
        "|---|---:|---|---:|---|",
    ]
    for name, verdict in sorted(criteria["market_verdicts"].items()):
        edge = verdict["edge"]
        threshold = f"{verdict['threshold']:.2f}" if verdict["threshold"] else "—"
        failed = ", ".join(verdict["failed_filters"]) or "**ninguno**"
        lines.append(
            f"| {name} | {edge['edge']*100:+.2f} | "
            f"[{edge['ci95'][0]*100:+.2f}, {edge['ci95'][1]*100:+.2f}] | "
            f"{threshold} | {failed} |")
    oos = criteria["out_of_sample_simulation"]
    lines.extend(["", "## Entrega fuera de muestra", "",
                  f"Derivado con {oos['derivation']['matches']} partidos "
                  f"({', '.join(oos['derivation']['selected_markets']) or 'ninguno'}), "
                  f"medido en {oos['validation']['matches']}.", "",
                  "| Piernas | Declarado | Observado | Ratio |", "|---:|---:|---:|---:|"])
    for legs, value in sorted(oos["by_legs"].items()):
        lines.append(f"| {legs} | {value['declared']:.4f} | "
                     f"{value['observed']:.4f} | {value['delivery_ratio']:.2f} |")
    lines.extend(["", "Elegibilidad no es promoción. No autoriza staking."])
    (OUTPUT / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    assert result["status"] == STATUS
    LOGGER.info("Fase 135 sellada: %s mercados elegibles",
                len(result["eligible_markets"]))

# Version: 1.0.0
# Created: 2026-08-21
