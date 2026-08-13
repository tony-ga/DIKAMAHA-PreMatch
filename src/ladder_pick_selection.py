"""Selecciona una línea representativa por grupo de la escalera auditada.

Continuación de la auditoría de modelos (`docs/objetivo_auditoria_modelos_v1.md`,
Etapa 4). `audited_market_ladder_view` ya filtra cada línea a las que
`scripts/run_ladder_audit.py` verificó como calibradas y con ventaja medida
(`model_edge` o `base_rate_driven`, IC bootstrap por partido completo). Este
módulo no añade una capa de evidencia nueva: decide, dentro de cada grupo
(métrica × lado × periodo) ya auditado, cuál de sus líneas exponer como "la
estadística más probable" de ese mercado en el menú de mayor probabilidad.

La selección tiene **dos niveles**, y una cota dura que ninguno puede cruzar:

1. **Banda objetivo.** Una línea cuya probabilidad ronda la certeza -"más de
   0.5 córners" ≈ 99%- no aporta información aunque acierte casi siempre; es
   el mismo sesgo que Fase 122 documentó como "aciertos inflados por líneas
   extremas". Tampoco sirve el volado, apenas por encima del 50%. El nivel 1
   exige `[CONFIDENCE_FLOOR, CONFIDENCE_CEILING]`.
2. **Cobertura por mercado, con tope.** Si ningún line del grupo cae en la
   banda, se publica la más cercana a ella -y sólo si además cabe dentro de
   `[HARD_FLOOR, HARD_CEILING]`-, etiquetada `selection: "outside_band"` para
   que la interfaz la distinga. Si tampoco hay ninguna, el mercado **no se
   publica**.

El nivel 2 repone la garantía "al menos una línea por mercado" que DEC-182
retiró, sin reabrir el defecto que motivó su retirada. La versión de DEC-179
(`fallback_outside_band`) no tenía tope: publicaba la línea más cercana
**fuera cual fuera**, y así llegó a producción "menos de 0.5 córners, 96%".
Con la cota dura esa misma línea queda rechazada -0.9617 excede
`HARD_CEILING`- mientras que el tramo 0.55-0.60 y 0.85-0.90, hoy descartado
por completo, vuelve a estar disponible. La regla de obviedad sigue mandando;
lo que se recupera es el margen que la banda estricta tiraba de más.

Efecto medido sobre los 1,895 partidos de `team_predictions.json`: la
cobertura por grupo pasa de 32.4% a 96.5% en tarjetas de primera mitad, de
84.9% a 100% en tarjetas de partido completo y de 93.0% a 96.7% en tiros,
sin publicar ninguna cifra fuera de `[0.55, 0.90]`. Tiros a puerta se queda
en 70.8% porque su escalera es genuinamente extrema, y eso se respeta.

Ambos niveles comprueban **dos cifras** que pueden divergir: la confianza del
modelo para este partido y la tasa histórica observada de esa línea, que es
la cifra que el menú realmente publica. Una línea cuyo modelo declara 0.62
pero cuyo histórico es 0.96 sigue siendo una obviedad para el usuario, porque
96% es lo que ve.

Version: 3.0.0
Created: 2026-08-13
Updated: 2026-08-13 (DEC-182: dirección del histórico y regla de obviedad;
    DEC-187: nivel 2 con cota dura para recuperar cobertura por mercado)
"""

from __future__ import annotations

from typing import Any

try:
    from src.settlement_store import wilson_interval
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from settlement_store import wilson_interval

CONFIDENCE_FLOOR = 0.60
CONFIDENCE_CEILING = 0.85
# Cota que ni el nivel 2 puede cruzar. Deja fuera la obviedad (≥0.90) -el
# caso reportado en DEC-182, "menos de 0.5 córners" con tasa histórica
# 0.9617- y el volado.
#
# El piso es 0.55 y no 0.50 porque 0.50 sería vacuo del lado del modelo:
# `_candidate` publica siempre la dirección dominante, así que su confianza
# es `max(over, under)` y nunca baja de 0.5; con el piso en 0.50 el nivel 2
# habría admitido líneas de 51%, que es exactamente el volado que la regla 1
# existe para evitar. Medido sobre los 1,895 partidos del artefacto, subirlo
# a 0.55 cuesta 0.4 puntos de cobertura (90.5% → 90.1%) y garantiza que
# ninguna cifra publicada baje de 0.55.
HARD_FLOOR = 0.55
HARD_CEILING = 0.90


def select_ladder_picks(
    audited_market_ladder_view: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Elige como mucho una línea por grupo de la escalera auditada.

    Un grupo cuyas líneas no caben ni siquiera en la cota dura no produce
    pick: preferimos que un mercado falte a que aparezca con una cifra que no
    informa.
    """

    picks = []
    for group in audited_market_ladder_view:
        pick = _select_group(group)
        if pick is not None:
            picks.append(pick)
    return picks


def _select_group(group: dict[str, Any]) -> dict[str, Any] | None:
    """Elige la línea representativa de un grupo, o ninguna."""

    candidates = [_candidate(line) for line in group.get("lines") or []]
    in_band = [
        candidate for candidate in candidates
        if _within(candidate, CONFIDENCE_FLOOR, CONFIDENCE_CEILING)]
    if in_band:
        # La menos extrema dentro de la banda: es la que más depende de estos
        # dos equipos en concreto, en vez de la más fácil de acertar.
        chosen = min(in_band, key=lambda candidate: candidate["confidence"])
        return _pick(group, chosen, "target_band",
                     (CONFIDENCE_FLOOR, CONFIDENCE_CEILING))
    tolerated = [
        candidate for candidate in candidates
        if _within(candidate, HARD_FLOOR, HARD_CEILING)]
    if not tolerated:
        return None
    # Fuera de banda se elige por cercanía a ella, no por confianza mínima:
    # dentro de la cota, la línea más informativa es la que menos se aleja
    # del tramo que sí se validó.
    chosen = min(tolerated, key=_distance_to_band)
    return _pick(group, chosen, "outside_band", (HARD_FLOOR, HARD_CEILING))


def _within(candidate: dict[str, Any], floor: float, ceiling: float) -> bool:
    """Comprueba las dos cifras publicadas contra un tramo."""

    return all(
        floor <= candidate[key] <= ceiling
        for key in ("confidence", "observed_rate"))


def _distance_to_band(candidate: dict[str, Any]) -> float:
    """Mide cuánto se aleja de la banda objetivo la peor de sus dos cifras."""

    return max(
        _gap(candidate["confidence"]), _gap(candidate["observed_rate"]))


def _gap(value: float) -> float:
    """Distancia de una probabilidad al tramo `[FLOOR, CEILING]`."""

    if value < CONFIDENCE_FLOOR:
        return CONFIDENCE_FLOOR - value
    if value > CONFIDENCE_CEILING:
        return value - CONFIDENCE_CEILING
    return 0.0


def _candidate(line: dict[str, Any]) -> dict[str, Any]:
    """Normaliza una línea cruda a la dirección dominante y sus dos cifras.

    `observed_rate_historical` del artefacto de auditoría es **siempre** la
    tasa del `over`. Para un pick `under` la cifra que corresponde es su
    complemento: publicar la del `over` sin invertirla es exactamente el
    defecto que llevó a producción "menos de 0.5 córners: 96%", cuando 96%
    era la frecuencia histórica de que hubiera **más** de 0.5 (ver DEC-182).

    La calibración y el veredicto de fiabilidad sí son invariantes a la
    dirección -el Brier de un binario cumple `B(p, y) == B(1-p, 1-y)`-, así
    que `reliability` viaja sin alterarse.
    """

    over = float(line["over_probability"])
    under = float(line.get("under_probability", 1.0 - over))
    observed_over = float(line["observed_rate_historical"])
    sample_size = int(line["sample_size"])
    over_hits = round(observed_over * sample_size)
    is_over = over >= under
    return {
        "line": line,
        "direction": "over" if is_over else "under",
        "confidence": over if is_over else under,
        "observed_rate": observed_over if is_over else 1.0 - observed_over,
        "observed_hits": over_hits if is_over else sample_size - over_hits,
        "sample_size": sample_size,
    }


def _pick(
    group: dict[str, Any], candidate: dict[str, Any], selection: str,
    bucket: tuple[float, float],
) -> dict[str, Any]:
    """Construye el pick final en el shape que consume `high_probability_view`.

    `bucket` reutiliza el mismo campo que ya usan los picks de gol (Fase 122)
    para declarar la zona de confianza, sin añadir una columna nueva a
    `high_probability_pick_freezes`; es también lo que distingue un pick de
    nivel 1 de uno de nivel 2 ya congelado, sin migración de esquema.
    `observed_ci95` es un intervalo de Wilson calculado sobre los aciertos de
    **la dirección publicada**, no sobre los del `over`.
    """

    line = candidate["line"]
    sample_size = candidate["sample_size"]
    ci_low, ci_high = wilson_interval(candidate["observed_hits"], sample_size)
    return {
        "market": str(group["key"]),
        "metric": str(group["metric"]),
        "team_side": str(group["team_side"]),
        "period": str(group["period"]),
        "line": float(line["line"]),
        "direction": candidate["direction"],
        "model_probability": float(candidate["confidence"]),
        "observed_rate": float(candidate["observed_rate"]),
        "observed_ci95": [ci_low, ci_high],
        "sample_size": sample_size,
        "edge_source": str(line["reliability"]),
        "bucket": [bucket[0], bucket[1]],
        "selection": selection,
    }


# Version: 3.0.0
# Created: 2026-08-13
# Updated: 2026-08-13
