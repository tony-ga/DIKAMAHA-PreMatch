"""Selecciona una línea representativa por grupo de la escalera auditada.

Continuación de la auditoría de modelos (`docs/objetivo_auditoria_modelos_v1.md`,
Etapa 4). `audited_market_ladder_view` ya filtra cada línea a las que
`scripts/run_ladder_audit.py` verificó como calibradas y con ventaja medida
(`model_edge` o `base_rate_driven`, IC bootstrap por partido completo). Este
módulo no añade una capa de evidencia nueva: decide, dentro de cada grupo
(métrica × lado × periodo) ya auditado, cuál de sus líneas exponer como "la
estadística más probable" de ese mercado en el menú de mayor probabilidad.

Dos reglas gobiernan la selección, y la segunda manda sobre la cobertura:

1. **Nada obvio.** Una línea cuya probabilidad ronda la certeza -"más de 0.5
   córners" ≈ 99%- no aporta información aunque acierte casi siempre; es el
   mismo sesgo que Fase 122 documentó como "aciertos inflados por líneas
   extremas". Tampoco sirve el volado, apenas por encima del 50%.
2. **Antes vacío que obvio.** Si ningún line del grupo cae en la banda
   `[CONFIDENCE_FLOOR, CONFIDENCE_CEILING]`, el mercado **no se publica**.
   Una versión anterior exponía igual la línea más cercana a la banda
   (`fallback_outside_band`) para garantizar "al menos una línea por
   mercado"; en la práctica eso publicó exactamente las obviedades que la
   regla 1 existe para evitar -incluida una línea imposible en producción,
   ver DEC-182-, así que la garantía de cobertura cede ante la regla de
   obviedad.

La banda se comprueba **dos veces**, contra dos cifras que pueden divergir:
la confianza del modelo para este partido y la tasa histórica observada de
esa línea, que es la cifra que el menú realmente publica. Una línea cuyo
modelo declara 0.62 pero cuyo histórico es 0.96 sigue siendo una obviedad
para el usuario, porque 96% es lo que ve.

Version: 2.0.0
Created: 2026-08-13
Updated: 2026-08-13 (DEC-182: dirección del histórico y regla de obviedad)
"""

from __future__ import annotations

from typing import Any

try:
    from src.settlement_store import wilson_interval
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from settlement_store import wilson_interval

CONFIDENCE_FLOOR = 0.60
CONFIDENCE_CEILING = 0.85


def select_ladder_picks(
    audited_market_ladder_view: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Elige como mucho una línea por grupo de la escalera auditada.

    Un grupo sin líneas aptas no produce pick: preferimos que un mercado
    falte a que aparezca con una cifra que no informa.
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
        if _within_band(candidate["confidence"])
        and _within_band(candidate["observed_rate"])
    ]
    if not in_band:
        return None
    # La menos extrema dentro de la banda: es la que más depende de estos dos
    # equipos en concreto, en vez de la más fácil de acertar.
    chosen = min(in_band, key=lambda candidate: candidate["confidence"])
    return _pick(group, chosen)


def _within_band(value: float) -> bool:
    """Comprueba que una probabilidad no sea ni volado ni obviedad."""

    return CONFIDENCE_FLOOR <= value <= CONFIDENCE_CEILING


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


def _pick(group: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Construye el pick final en el shape que consume `high_probability_view`.

    `bucket` reutiliza el mismo campo que ya usan los picks de gol (Fase 122)
    para declarar la zona de confianza, sin añadir una columna nueva a
    `high_probability_pick_freezes`. `observed_ci95` es un intervalo de Wilson
    calculado sobre los aciertos de **la dirección publicada**, no sobre los
    del `over`.
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
        "bucket": [CONFIDENCE_FLOOR, CONFIDENCE_CEILING],
        "selection": "target_band",
    }


# Version: 2.0.0
# Created: 2026-08-13
# Updated: 2026-08-13
