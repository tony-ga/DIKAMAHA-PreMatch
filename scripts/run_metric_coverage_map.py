"""Genera el mapa de cobertura de métricas por liga desde el corpus crudo.

Lee directamente el corpus causal de Fase 74 y agrega los conteos reales por
equipo-partido -antes de cualquier filtro-, para evitar una dependencia
circular real que este script tuvo al principio: leer desde
`team_predictions.json` (la salida YA filtrada del script de reparación)
borraba la evidencia de la propia contaminación que debía detectar. Una vez
que una liga queda marcada `absent` para una métrica, esa métrica desaparece
de sus filas en la salida reparada, así que una segunda pasada del mapa de
cobertura ya no encontraría la señal que la primera pasada sí encontró.

El runtime consulta el artefacto resultante para no publicar un mercado
construido sobre datos que el proveedor nunca entregó.

# Requirements:
#   (sin dependencias externas)

Version: 2.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
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

LOGGER = logging.getLogger(__name__)


def _raw_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aplana los partidos agregados en filas equipo-partido sin filtrar."""

    output = []
    for match in matches:
        for side in ("home", "away"):
            output.append({
                "league_slug": match["league_slug"], "actual": match[side]})
    return output


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados."""

    parser = argparse.ArgumentParser(
        description="Mapa de cobertura de métricas por liga")
    parser.add_argument("--source", type=Path, default=CORPUS_SOURCE)
    parser.add_argument("--output", type=Path, default=COVERAGE_ARTIFACT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Construye el mapa desde el corpus crudo y lo persiste sellado."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    matches = _matches(_read_rows())
    rows = _raw_rows(matches)
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
