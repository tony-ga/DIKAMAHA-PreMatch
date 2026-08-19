"""Detecta confusión por fuerza relativa antes de aceptar un efecto medido.

Un intervalo bootstrap que no cruza cero **no** basta para afirmar que una
variable causa un resultado. El caso que motivó este módulo (`DEC-218`) es
exacto: al medir formaciones sobre la Eurocopa Femenina 2025, "el equipo con
más defensores que el rival gana por más goles" daba `+2.43` goles de
diferencia con IC95% `[+0.31, +4.53]` -no cruzaba cero-. Investigando la
causa resultó ser un artefacto: los equipos débiles del torneo eligen línea
de 3 contra rivales favoritos, así que la formación estaba **correlacionada
con la fuerza del equipo**, no causando el resultado. Quitando dos partidos
de un solo equipo dominante el efecto caía a la mitad.

El intervalo no detecta eso porque mide precisión, no identificación. Este
módulo añade las tres comprobaciones que sí lo detectan:

1. **Influencia**: si excluir un solo grupo mueve el efecto más allá de un
   umbral, el efecto vive en ese grupo, no en la población.
2. **Control por fuerza**: si el efecto desaparece al comparar sólo dentro de
   estratos de fuerza similar, lo que se estaba midiendo era la fuerza.
3. **Fragilidad**: si excluir cualquier grupo individual hace que el IC pase
   a cruzar cero, la conclusión no es estable.

La unidad de remuestreo es el **grupo** (`group_id`), no la observación,
igual que el resto del proyecto trata el partido completo como unidad IID y
nunca la ventana ni el snapshot.

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

# Réplicas bootstrap. Mismo orden de magnitud que el resto de evaluaciones de
# candidato del proyecto (`scripts/evaluate_candidates.py` usa 10,000).
DEFAULT_REPLICATES = 10_000

# Semilla por defecto, para que dos corridas del mismo análisis den el mismo
# intervalo. El proyecto usa 42 en sus scripts de evaluación.
DEFAULT_SEED = 42

# Cuántos estratos de fuerza se usan para el control. Cuatro es el compromiso
# entre aislar la fuerza y conservar observaciones de ambos lados dentro de
# cada estrato; con más estratos muchos quedan vacíos de un lado.
DEFAULT_STRENGTH_BINS = 4

# Cuánto puede mover el efecto la exclusión de un solo grupo antes de
# considerarlo dominado por ese grupo. 0.5 = el efecto se reduce a la mitad,
# que es exactamente lo que ocurrió en el caso de las formaciones.
DEFAULT_INFLUENCE_THRESHOLD = 0.5

# Mínimo de grupos para que las comprobaciones de influencia y fragilidad
# signifiquen algo: con dos grupos, excluir uno siempre es catastrófico.
MINIMUM_GROUPS = 4

# Máximo de grupos admitido. El análisis de fragilidad corre un bootstrap
# completo por cada grupo, así que el coste crece como grupos x réplicas x
# observaciones. Medido: con ~2,600 grupos y 28,000 observaciones la llamada
# no termina en 10 minutos.
#
# Falla ruidosamente en vez de colgarse. Una función que tarda para siempre
# sin decir por qué es peor que una que rechaza la entrada explicando cómo
# arreglarla: agrupar por una unidad más gruesa -liga en vez de equipo- suele
# ser además lo correcto estadísticamente, porque la confusión que interesa
# vive al nivel del grupo grande.
MAXIMUM_GROUPS = 500

VERDICT_INDISTINGUISHABLE = "indistinguible"
VERDICT_POSITIVE = "mejora confirmada"
VERDICT_NEGATIVE = "degradación confirmada"
VERDICT_CONFOUNDED = "confundido"
VERDICT_FRAGILE = "frágil"
VERDICT_INSUFFICIENT = "muestra insuficiente"

# Sólo estos dos veredictos autorizan tratar el efecto como señal causal.
TRUSTWORTHY_VERDICTS = frozenset({VERDICT_POSITIVE, VERDICT_NEGATIVE})


@dataclass(frozen=True, slots=True)
class ConfoundingObservation:
    """Una observación con su grupo, su exposición y su fuerza relativa.

    Args:
        group_id: Unidad de agrupación y de remuestreo -equipo, liga, partido-.
            Es lo que se excluye entero en el análisis de influencia.
        effect: Resultado observado, con signo (por ejemplo, diferencia de
            goles a favor del lado expuesto).
        exposure: Variable candidata a causa. Sólo se usa su **signo**: las
            observaciones con `exposure > 0` se contrastan contra las de
            `exposure < 0`, y las de `exposure == 0` quedan fuera del
            contraste -son el grupo sin exposición diferencial-.
        strength: Fuerza relativa previa, conocida antes del hecho (Elo,
            lambda estructural, probabilidad pre-match). Es la variable que se
            controla; si el efecto es en realidad fuerza, desaparece al
            estratificar por ella.
    """

    group_id: str
    effect: float
    exposure: float
    strength: float


def check_confounding(
    observations: Sequence[ConfoundingObservation],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
    strength_bins: int = DEFAULT_STRENGTH_BINS,
    influence_threshold: float = DEFAULT_INFLUENCE_THRESHOLD,
) -> dict[str, Any]:
    """Mide un efecto y comprueba si sobrevive a confusión por fuerza.

    Args:
        observations: Observaciones a analizar.
        replicates: Réplicas bootstrap sobre grupos.
        seed: Semilla del generador, para reproducibilidad exacta.
        strength_bins: Número de estratos de fuerza para el control.
        influence_threshold: Fracción del efecto que puede mover la exclusión
            de un solo grupo antes de declararlo confundido.

    Returns:
        dict[str, Any]: Efecto base con IC95%, análisis de influencia, efecto
        controlado por fuerza, fragilidad y veredicto.
    """

    rows = _validated(observations)
    exposed = [row for row in rows if row.exposure > 0.0]
    unexposed = [row for row in rows if row.exposure < 0.0]
    groups = sorted({row.group_id for row in rows})

    if not exposed or not unexposed:
        return _insufficient(
            "no hay observaciones en ambos lados de la exposición",
            len(rows), len(groups))
    if len(groups) < MINIMUM_GROUPS:
        return _insufficient(
            f"se requieren al menos {MINIMUM_GROUPS} grupos", len(rows),
            len(groups))
    if len(groups) > MAXIMUM_GROUPS:
        raise ValueError(
            f"confounding_check_too_many_groups:{len(groups)}>"
            f"{MAXIMUM_GROUPS}; agrupa por una unidad más gruesa")

    baseline = _contrast(rows)
    interval = _bootstrap_contrast(rows, groups, replicates, seed)

    influence = _influence(rows, groups, baseline)
    stratified = _strength_controlled(rows, strength_bins)
    fragility = _fragility(rows, groups, replicates, seed)

    verdict = _verdict(
        baseline=baseline, interval=interval, influence=influence,
        stratified=stratified, fragility=fragility,
        influence_threshold=influence_threshold)

    return {
        "observations": len(rows),
        "groups": len(groups),
        "exposed": len(exposed),
        "unexposed": len(unexposed),
        "baseline_effect": baseline,
        "ci_low": interval[0],
        "ci_high": interval[1],
        "crosses_zero": bool(interval[0] <= 0.0 <= interval[1]),
        "influence": influence,
        "strength_controlled": stratified,
        "fragility": fragility,
        "influence_threshold": influence_threshold,
        "replicates": replicates,
        "seed": seed,
        "verdict": verdict,
    }


def _validated(
    observations: Sequence[ConfoundingObservation],
) -> list[ConfoundingObservation]:
    """Rechaza entradas no finitas antes de que contaminen el bootstrap."""

    if not observations:
        raise ValueError("confounding_check_empty_observations")
    rows: list[ConfoundingObservation] = []
    for row in observations:
        if not isinstance(row, ConfoundingObservation):
            raise TypeError("confounding_check_invalid_observation")
        for value in (row.effect, row.exposure, row.strength):
            if not math.isfinite(float(value)):
                raise ValueError("confounding_check_non_finite_value")
        if not str(row.group_id).strip():
            raise ValueError("confounding_check_empty_group_id")
        rows.append(row)
    return rows


def _contrast(rows: Sequence[ConfoundingObservation]) -> float:
    """Diferencia de resultado medio entre el lado expuesto y el no expuesto.

    Devuelve ``nan`` cuando falta alguno de los dos lados: es lo correcto en
    una réplica bootstrap o en un estrato vacío, y quien llama decide si esa
    réplica cuenta.
    """

    exposed = [row.effect for row in rows if row.exposure > 0.0]
    unexposed = [row.effect for row in rows if row.exposure < 0.0]
    if not exposed or not unexposed:
        return float("nan")
    return float(np.mean(exposed) - np.mean(unexposed))


def _bootstrap_contrast(
    rows: Sequence[ConfoundingObservation],
    groups: Sequence[str],
    replicates: int,
    seed: int,
) -> tuple[float, float]:
    """IC95% percentil remuestreando **grupos** con reemplazo."""

    by_group: dict[str, list[ConfoundingObservation]] = defaultdict(list)
    for row in rows:
        by_group[row.group_id].append(row)

    generator = np.random.default_rng(seed)
    indices = np.arange(len(groups))
    estimates = np.empty(replicates, dtype=float)
    for replicate in range(replicates):
        drawn = generator.choice(indices, size=len(groups), replace=True)
        sample: list[ConfoundingObservation] = []
        for position in drawn:
            sample.extend(by_group[groups[position]])
        estimates[replicate] = _contrast(sample)

    valid = estimates[~np.isnan(estimates)]
    if valid.size == 0:
        return float("nan"), float("nan")
    return float(np.percentile(valid, 2.5)), float(np.percentile(valid, 97.5))


def _influence(
    rows: Sequence[ConfoundingObservation],
    groups: Sequence[str],
    baseline: float,
) -> dict[str, Any]:
    """Identifica el grupo cuya exclusión más mueve el efecto."""

    worst_group = None
    worst_effect = float("nan")
    worst_ratio = 0.0
    for group in groups:
        remaining = [row for row in rows if row.group_id != group]
        effect = _contrast(remaining)
        if math.isnan(effect):
            continue
        ratio = (
            abs(baseline - effect) / abs(baseline)
            if abs(baseline) > 1e-12 else 0.0)
        if ratio > worst_ratio:
            worst_ratio, worst_group, worst_effect = ratio, group, effect
    return {
        "most_influential_group": worst_group,
        "effect_without_it": worst_effect,
        "influence_ratio": worst_ratio,
    }


def _strength_controlled(
    rows: Sequence[ConfoundingObservation], strength_bins: int,
) -> dict[str, Any]:
    """Recalcula el efecto dentro de estratos de fuerza y lo reagrega.

    Si el efecto sólo existe porque el lado expuesto era además el más
    fuerte, dentro de un estrato de fuerza homogénea desaparece.
    """

    if strength_bins < 2:
        raise ValueError("confounding_check_invalid_strength_bins")

    strengths = np.array([row.strength for row in rows], dtype=float)
    # Cuantiles sobre la fuerza observada; `np.unique` evita estratos
    # degenerados cuando muchos valores empatan.
    edges = np.unique(np.quantile(
        strengths, np.linspace(0.0, 1.0, strength_bins + 1)))
    if edges.size < 2:
        return {
            "effect": float("nan"), "strata_used": 0, "strata_total": 0,
            "coverage": 0.0,
            "note": "la fuerza no tiene variación; no se puede controlar",
        }

    # `right=False` con el último borde incluido deja cada observación en un
    # único estrato y evita que el máximo caiga fuera del rango.
    assigned = np.clip(np.digitize(strengths, edges[1:-1], right=False),
                       0, edges.size - 2)

    weighted_sum, weight_total, used = 0.0, 0, 0
    strata_total = int(edges.size - 1)
    for stratum in range(strata_total):
        members = [row for row, index in zip(rows, assigned)
                   if index == stratum]
        effect = _contrast(members)
        if math.isnan(effect):
            continue
        used += 1
        weighted_sum += effect * len(members)
        weight_total += len(members)

    if weight_total == 0:
        return {
            "effect": float("nan"), "strata_used": 0,
            "strata_total": strata_total, "coverage": 0.0,
            "note": "ningún estrato tiene observaciones de ambos lados",
        }
    return {
        "effect": weighted_sum / weight_total,
        "strata_used": used,
        "strata_total": strata_total,
        "coverage": weight_total / len(rows),
    }


def _fragility(
    rows: Sequence[ConfoundingObservation],
    groups: Sequence[str],
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Fracción de grupos cuya exclusión hace que el IC pase a cruzar cero.

    Se remuestrea con menos réplicas que el intervalo principal porque el
    cálculo se repite una vez por grupo y la cifra que interesa es una
    proporción, no un extremo de la cola.
    """

    trimmed = max(1_000, replicates // 10)
    flipped, evaluated = 0, 0
    for group in groups:
        remaining = [row for row in rows if row.group_id != group]
        remaining_groups = sorted({row.group_id for row in remaining})
        if len(remaining_groups) < MINIMUM_GROUPS - 1:
            continue
        if math.isnan(_contrast(remaining)):
            continue
        low, high = _bootstrap_contrast(
            remaining, remaining_groups, trimmed, seed)
        if math.isnan(low):
            continue
        evaluated += 1
        if low <= 0.0 <= high:
            flipped += 1
    return {
        "groups_evaluated": evaluated,
        "groups_flipping_the_interval": flipped,
        "fragility_fraction": flipped / evaluated if evaluated else 0.0,
    }


def _verdict(
    *,
    baseline: float,
    interval: tuple[float, float],
    influence: dict[str, Any],
    stratified: dict[str, Any],
    fragility: dict[str, Any],
    influence_threshold: float,
) -> str:
    """Combina las cuatro comprobaciones en un único veredicto."""

    low, high = interval
    if math.isnan(low) or math.isnan(baseline):
        return VERDICT_INSUFFICIENT
    if low <= 0.0 <= high:
        return VERDICT_INDISTINGUISHABLE

    if influence["influence_ratio"] >= influence_threshold:
        return VERDICT_CONFOUNDED

    controlled = stratified["effect"]
    if not math.isnan(controlled):
        # El efecto se desvanece o cambia de signo al comparar sólo entre
        # rivales de fuerza parecida: lo que se medía era la fuerza.
        if abs(controlled) < abs(baseline) * (1.0 - influence_threshold):
            return VERDICT_CONFOUNDED
        if math.copysign(1.0, controlled) != math.copysign(1.0, baseline):
            return VERDICT_CONFOUNDED

    if fragility["fragility_fraction"] > 0.0:
        return VERDICT_FRAGILE
    return VERDICT_POSITIVE if baseline > 0.0 else VERDICT_NEGATIVE


def _insufficient(note: str, observations: int, groups: int) -> dict[str, Any]:
    """Respuesta uniforme cuando la muestra no permite ninguna conclusión."""

    return {
        "observations": observations,
        "groups": groups,
        "baseline_effect": float("nan"),
        "ci_low": float("nan"),
        "ci_high": float("nan"),
        "crosses_zero": True,
        "verdict": VERDICT_INSUFFICIENT,
        "note": note,
    }


__all__ = [
    "ConfoundingObservation",
    "check_confounding",
    "TRUSTWORTHY_VERDICTS",
    "VERDICT_CONFOUNDED",
    "VERDICT_FRAGILE",
    "VERDICT_INDISTINGUISHABLE",
    "VERDICT_INSUFFICIENT",
    "VERDICT_NEGATIVE",
    "VERDICT_POSITIVE",
]

# Version: 1.0.0
# Created: 2026-08-18
