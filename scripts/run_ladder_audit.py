"""Audita la escalera over/under completa contra el histórico real.

Produce, por cada (métrica, lado, línea) desde 0.5 hasta el máximo de cada
métrica, el veredicto de fiabilidad que exige
`docs/objetivo_auditoria_modelos_v1.md`: calibración honesta más ventaja
demostrada sobre la tasa base, con intervalo bootstrap por partido completo.

Consume el artefacto ya reparado de conteos de equipo -dispersiones limpias,
filas contaminadas excluidas- de modo que mide lo que el runtime realmente
sirve, no una reimplementación.

# Requirements:
#   numpy>=2.0

Version: 1.0.0
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

from src.ladder_audit import (  # noqa: E402
    VERDICT_BASE_RATE,
    VERDICT_EDGE,
    audit_ladder,
    summarize,
)
from src.team_count_market_runtime import LADDER_MAXIMUMS  # noqa: E402

LOGGER = logging.getLogger(__name__)
ARTIFACT = ROOT / "artifacts/phase_84a_team_count_markets"
OUTPUT = ROOT / "artifacts/ladder_audit/ladder_reliability.json"

# Qué escalera corresponde a cada métrica del artefacto. El nombre ya codifica
# el periodo, así que el máximo se elige por métrica base y mitad.
METRIC_LADDERS = {
    "corners": ("corners", "full_match"),
    "corners_first_half": ("corners", "half"),
    "shots": ("shots", "full_match"),
    "shots_on_target": ("shots_on_target", "full_match"),
    "yellow_cards": ("yellow_cards", "full_match"),
    "yellow_cards_first_half": ("yellow_cards", "half"),
}
SIDES = ("home", "away", "total")


def _maximum(metric: str, side: str) -> int:
    """Devuelve el umbral entero máximo a auditar para esa métrica y lado."""

    base, period = METRIC_LADDERS[metric]
    maximum = LADDER_MAXIMUMS[base][period]
    # Una línea `total` suma dos equipos, así que su soporte útil es mayor.
    return maximum * 2 if side == "total" else maximum


def _parser() -> argparse.ArgumentParser:
    """Construye flags operativos acotados."""

    parser = argparse.ArgumentParser(
        description="Auditoría de fiabilidad de la escalera completa")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--metric", type=str, default=None,
                        help="Audita una sola métrica (depuración)")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Ejecuta la auditoría completa y publica el artefacto."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    config = json.loads(
        (ARTIFACT / "config.json").read_text(encoding="utf-8"))
    rows = json.loads(
        (ARTIFACT / "team_predictions.json").read_text(encoding="utf-8"))
    dispersions = config["dispersions"]

    selected = [args.metric] if args.metric else list(METRIC_LADDERS)
    cells: list[dict[str, Any]] = []
    for metric in selected:
        phi = float(dispersions[metric])
        for side in SIDES:
            ladder = audit_ladder(
                rows, metric, side, phi, _maximum(metric, side),
                correlation=float(
                    config.get("correlations", {}).get(metric, 0.0)))
            cells.extend(ladder)
            edge = sum(1 for c in ladder if c["verdict"] == VERDICT_EDGE)
            base = sum(1 for c in ladder if c["verdict"] == VERDICT_BASE_RATE)
            LOGGER.info(
                "%-24s %-6s lineas=%2d  model_edge=%2d  base_rate=%2d",
                metric, side, len(ladder), edge, base)

    summary = summarize(cells)
    LOGGER.info("")
    LOGGER.info("resumen=%s", json.dumps(summary["by_verdict"], sort_keys=True))
    LOGGER.info(
        "publicables=%d de %d  con ventaja real=%d",
        summary["publishable"], summary["cells"], summary["with_model_edge"])

    if args.dry_run:
        LOGGER.info("dry-run: no se escribió %s", args.output)
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "version": "ladder_reliability_v1",
        "source_artifact": "phase_84a_team_count_markets",
        "summary": summary,
        "cells": cells,
    }, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    LOGGER.info("escrito %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-08-12
