"""Genera el mapa de cobertura de métricas por liga desde el corpus crudo.

Agrega los conteos reales por equipo-partido **antes de cualquier filtro**,
para evitar una dependencia circular real que este script tuvo al principio:
leer desde `team_predictions.json` (la salida YA filtrada del script de
reparación) borraba la evidencia de la propia contaminación que debía
detectar. Una vez que una liga queda marcada `absent` para una métrica, esa
métrica desaparece de sus filas en la salida reparada, así que una segunda
pasada del mapa de cobertura ya no encontraría la señal que la primera sí
encontró.

Por defecto lee el **snapshot activo**, no el corpus de Fase 74. El motivo es
una brecha real medida en producción (DEC-182): el corpus de Fase 74 tiene 39
ligas y cero filas de las 14 que Fase 120 añadió al catálogo servido, de modo
que 24 de las 63 ligas que ven usuarios no tenían ningún veredicto. Como
`MetricCoverage` degrada abierto -no suprime sin evidencia positiva de
ausencia-, esas ligas publicaban mercados construidos sobre datos que el
proveedor nunca entregó. El snapshot activo es la misma fuente de la que el
runtime deriva sus predicciones, así que el guard describe exactamente los
datos que el modelo usa.

El cambio de fuente se validó midiendo ambas contra las 39 ligas comunes:
**cero desacuerdos** en todas sus métricas, y 17 ligas más cubiertas. Se
conserva `--source` para reproducir el mapa histórico desde el corpus de
Fase 74.

# Requirements:
#   (sin dependencias externas)

Version: 3.0.0
Created: 2026-08-12
Updated: 2026-08-13 (DEC-182: snapshot activo como fuente por defecto)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_84a_team_count_markets import (  # noqa: E402
    SOURCE as CORPUS_SOURCE,
    _matches,
    _read_rows,
)
from src.metric_coverage import (  # noqa: E402
    COVERAGE_ARTIFACT,
    build_coverage_map,
)
from src.prematch_snapshot_registry import (  # noqa: E402
    read_snapshot_rows,
    resolve_active_snapshot,
)

LOGGER = logging.getLogger(__name__)

# Métricas que el snapshot expone por ventana y que se suman por equipo-partido.
WINDOW_METRICS = (
    "corners", "shots", "shots_on_target", "yellow_cards", "red_cards")
# Métricas de primera mitad, derivadas de las ventanas de ese periodo.
FIRST_HALF_METRICS = {
    "corners": "corners_first_half",
    "yellow_cards": "yellow_cards_first_half",
}


def _raw_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aplana los partidos agregados en filas equipo-partido sin filtrar."""

    output = []
    for match in matches:
        for side in ("home", "away"):
            output.append({
                "league_slug": match["league_slug"], "actual": match[side]})
    return output


def _snapshot_rows(path: Path) -> list[dict[str, Any]]:
    """Agrega las ventanas del snapshot a filas equipo-partido sin filtrar.

    Reproduce la misma semántica que `_raw_rows` sobre el corpus de Fase 74:
    una fila por (partido, equipo) con los conteos totales observados, más
    las dos métricas de primera mitad que la escalera audita por separado.
    """

    totals: dict[tuple[Any, Any], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    leagues: dict[tuple[Any, Any], str] = {}
    for row in read_snapshot_rows(path):
        key = (row.get("match_id"), row.get("team_id"))
        leagues[key] = str(row.get("league_slug"))
        accumulated = totals[key]
        for metric in WINDOW_METRICS:
            accumulated[metric] += float(row.get(metric) or 0.0)
        if str(row.get("period")) == "first_half":
            for metric, target in FIRST_HALF_METRICS.items():
                accumulated[target] += float(row.get(metric) or 0.0)
    return [
        {"league_slug": leagues[key], "actual": dict(values)}
        for key, values in totals.items()
    ]


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados."""

    parser = argparse.ArgumentParser(
        description="Mapa de cobertura de métricas por liga")
    parser.add_argument(
        "--source", type=Path, default=None,
        help="Corpus de Fase 74 explícito; por defecto usa el snapshot activo")
    parser.add_argument("--output", type=Path, default=COVERAGE_ARTIFACT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Construye el mapa desde el corpus crudo y lo persiste sellado."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    if args.source is None:
        snapshot = resolve_active_snapshot()
        LOGGER.info("fuente: snapshot activo %s", snapshot.name)
        rows = _snapshot_rows(snapshot)
    else:
        LOGGER.info("fuente: corpus explícito %s", args.source)
        with args.source.open(encoding="utf-8") as handle:
            corpus = [json.loads(line) for line in handle]
        rows = _raw_rows(_matches(corpus))
    LOGGER.info("filas equipo-partido agregadas=%d", len(rows))
    coverage = build_coverage_map(rows)

    absent = {
        league: sorted(
            name for name, entry in values["metrics"].items()
            if entry["status"] == "absent")
        for league, values in coverage["leagues"].items()
    }
    absent = {league: names for league, names in absent.items() if names}
    LOGGER.info(
        "ligas evaluadas=%d  ligas con alguna metrica ausente=%d",
        len(coverage["leagues"]), len(absent))
    for league, names in sorted(absent.items()):
        LOGGER.info("  %-24s sin cobertura: %s", league, ", ".join(names))

    if args.dry_run:
        LOGGER.info("dry-run: no se escribió %s", args.output)
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8")
    LOGGER.info("escrito %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 2.0.0
# Created: 2026-08-12
