"""Genera el mapa de cobertura de métricas por liga desde el corpus real.

Lee el walk-forward de Fase 84A -las mismas filas equipo-partido que
alimentaron el modelo servido- y emite el veredicto de qué liga publica qué
estadística. El runtime consulta ese artefacto para no publicar un mercado
construido sobre datos que el proveedor nunca entregó.

# Requirements:
#   (sin dependencias externas)

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.metric_coverage import (  # noqa: E402
    COVERAGE_ARTIFACT,
    build_coverage_map,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_84a_team_count_markets/team_predictions.json"


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados."""

    parser = argparse.ArgumentParser(
        description="Mapa de cobertura de métricas por liga")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=COVERAGE_ARTIFACT)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Construye el mapa y lo persiste como artefacto sellado."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    rows = json.loads(args.source.read_text(encoding="utf-8"))
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


# Version: 1.0.0
# Created: 2026-08-12
