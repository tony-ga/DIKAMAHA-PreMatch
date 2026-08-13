"""Selecciona una línea representativa por grupo de la escalera auditada.

Continuación de la auditoría de modelos (`docs/objetivo_auditoria_modelos_v1.md`,
Etapa 4). `audited_market_ladder_view` ya filtra cada línea a las que
`scripts/run_ladder_audit.py` verificó como calibradas y con ventaja medida
(`model_edge` o `base_rate_driven`, IC bootstrap por partido completo). Este
módulo no añade una capa de evidencia nueva: decide, dentro de cada grupo
(métrica × lado × periodo) ya auditado, cuál de sus líneas exponer como "la
estadística más probable" de ese mercado en el menú de mayor probabilidad.

La regla evita dos fallos simétricos: el volado (confianza apenas sobre 50%,
no gana nada frente al azar) y lo obvio (confianza cercana a la certeza, tipo
"más de 0.5 tiros" ≈ 99%, que no aporta información aunque acierte casi
siempre -el mismo sesgo que Fase 122 ya documentó como "aciertos inflados por
líneas extremas"-). Se define una banda de confianza
`[CONFIDENCE_FLOOR, CONFIDENCE_CEILING]` y, dentro de ella, se prefiere la
línea menos extrema -la más discriminante entre estos dos equipos en
concreto-. Si ninguna línea del grupo cae en la banda, se expone igual la más
cercana a ella: el pedido es mostrar siempre al menos una estadística por
mercado, nunca dejarlo vacío por indecisión de banda. Ese caso se marca
`selection = "fallback_outside_band"` para que la interfaz y la liquidación
puedan distinguirlo de una selección dentro del rango ideal.

Version: 1.0.0
Created: 2026-08-13
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
    """Elige una línea por grupo de la escalera auditada.

    Un grupo sin líneas no produce pick -nunca se inventa evidencia-. Todo
    grupo con al menos una línea sí produce exactamente un pick, así que un
    mercado cubierto por la escalera nunca queda ausente del menú.
    """

    picks = []
    for group in audited_market_ladder_view:
        pick = _select_group(group)
        if pick is not None:
            picks.append(pick)
    return picks


def _select_group(group: dict[str, Any]) -> dict[str, Any] | None:
    """Elige la línea representativa de un solo grupo."""

    lines = group.get("lines") or []
    candidates = [_candidate(line) for line in lines]
    if not candidates:
        return None
    in_band = [
        candidate for candidate in candidates
        if CONFIDENCE_FLOOR <= candidate["confidence"] <= CONFIDENCE_CEILING]
    if in_band:
        chosen = min(in_band, key=lambda candidate: candidate["confidence"])
        selection = "target_band"
    else:
        chosen = min(
            candidates,
            key=lambda candidate: abs(candidate["confidence"] - CONFIDENCE_FLOOR))
        selection = "fallback_outside_band"
    return _pick(group, chosen, selection)


def _candidate(line: dict[str, Any]) -> dict[str, Any]:
    """Normaliza una línea cruda a su confianza y dirección dominante."""

    over = float(line["over_probability"])
    under = float(line.get("under_probability", 1.0 - over))
    direction = "over" if over >= under else "under"
    confidence = over if direction == "over" else under
    return {"line": line, "confidence": confidence, "direction": direction}


def _pick(
    group: dict[str, Any], candidate: dict[str, Any], selection: str,
) -> dict[str, Any]:
    """Construye el pick final en el shape que consume `high_probability_view`.

    `bucket` reutiliza el mismo campo que ya usan los picks de gol (Fase 122)
    para clasificar la zona de confianza -no hace falta una columna nueva en
    `high_probability_pick_freezes`-: la banda objetivo para una selección
    `target_band`, o el extremo correspondiente para una `fallback_outside_
    band`, que por construcción cae siempre estrictamente por debajo del piso
    o por encima del techo. `observed_ci95` es un intervalo de Wilson real
    sobre `observed_rate_historical`/`sample_size` de esta línea concreta -no
    existía en la escalera auditada, que sólo publicaba el punto-.
    """

    line = candidate["line"]
    observed_rate = float(line["observed_rate_historical"])
    sample_size = int(line["sample_size"])
    hits = round(observed_rate * sample_size)
    ci_low, ci_high = wilson_interval(hits, sample_size)
    confidence = candidate["confidence"]
    if selection == "target_band":
        bucket = [CONFIDENCE_FLOOR, CONFIDENCE_CEILING]
    elif confidence < CONFIDENCE_FLOOR:
        bucket = [0.0, CONFIDENCE_FLOOR]
    else:
        bucket = [CONFIDENCE_CEILING, 1.0]
    return {
        "market": str(group["key"]),
        "metric": str(group["metric"]),
        "team_side": str(group["team_side"]),
        "period": str(group["period"]),
        "line": float(line["line"]),
        "direction": candidate["direction"],
        "model_probability": float(confidence),
        "observed_rate": observed_rate,
        "observed_ci95": [ci_low, ci_high],
        "sample_size": sample_size,
        "edge_source": str(line["reliability"]),
        "bucket": bucket,
        "selection": selection,
    }


# Version: 1.0.0
# Created: 2026-08-13
