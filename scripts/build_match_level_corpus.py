"""Agrega el corpus causal de Fase 74 a nivel partido.

`artifacts/phase_74_causal_sequence_corpus/` guarda micro-ventanas por equipo y
periodo -la unidad que necesita Markov-, pero la cadena de goles y todos los
candidatos de `arquitectura_matematica_v1` puntúan sobre **partidos completos**,
que es además la unidad IID de `DEC-006` y R7.

Este script reconstruye el nivel partido sumando los goles de cada equipo a lo
largo de sus ventanas y conservando el `split` que Fase 74 ya congeló, de modo
que ninguna medición posterior pueda elegir su propia partición -que es
justamente lo que R2 prohíbe-.

El marcador reconstruido se compara contra el propio corpus: una ventana perdida
produciría un marcador silenciosamente bajo, así que se exige que ambos equipos
del partido aparezcan y que el número de ventanas por equipo sea el mismo.

Uso:
    python -m scripts.build_match_level_corpus

# Requirements:
#   Python>=3.10

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl")
DEFAULT_OUTPUT = ROOT / "artifacts/match_level_corpus/matches.csv"

# Conteos que las micro-ventanas traen por equipo y que se suman a lo largo del
# partido. Sin ellos el corpus sólo sirve para mercados de gol; con ellos se
# pueden medir los candidatos de conteo -contracción jerárquica de tasas por
# liga, matriz de correlación entre métricas- sin depender de una ingesta nueva.
COUNT_METRICS = (
    "corners", "shots", "shots_on_target", "yellow_cards", "red_cards", "fouls",
)

FIELDS = (
    "match_id", "match_date", "league_slug", "season", "split",
    "home_team_id", "away_team_id", "home_goals", "away_goals",
    *(f"{side}_{metric}" for side in ("home", "away") for metric in COUNT_METRICS),
)


def build(source: Path, output: Path) -> dict[str, Any]:
    """Agrega ventanas a partidos y escribe el CSV resultante."""

    sides: dict[int, dict[bool, dict[str, Any]]] = defaultdict(dict)
    meta: dict[int, dict[str, Any]] = {}

    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            match_id = int(row["match_id"])
            is_home = bool(row["is_home"])
            side = sides[match_id].setdefault(
                is_home,
                {
                    "team_id": int(row["team_id"]), "goals": 0, "windows": 0,
                    **{metric: 0 for metric in COUNT_METRICS},
                },
            )
            if side["team_id"] != int(row["team_id"]):
                raise ValueError(
                    f"match {match_id}: dos equipos distintos en el mismo lado")
            side["goals"] += int(row["goals"])
            for metric in COUNT_METRICS:
                # Una métrica ausente en la ventana se cuenta como cero sólo si
                # el proveedor la omitió por no haber ocurrido; el guard de
                # cobertura por liga es quien distingue eso, no este agregador.
                side[metric] += int(row.get(metric, 0) or 0)
            side["windows"] += 1
            meta.setdefault(match_id, {
                "match_date": str(row["match_date"]),
                "league_slug": str(row["league_slug"]),
                "season": str(row["season"]),
                "split": str(row["split"]),
            })

    rows: list[dict[str, Any]] = []
    rejected = {"missing_side": 0, "window_mismatch": 0}

    for match_id, side_map in sides.items():
        if True not in side_map or False not in side_map:
            rejected["missing_side"] += 1
            continue
        home, away = side_map[True], side_map[False]
        if home["windows"] != away["windows"]:
            # Un desbalance de ventanas significa que a un lado le falta parte
            # del partido: su marcador estaría incompleto sin que nada lo avise.
            rejected["window_mismatch"] += 1
            continue
        rows.append({
            "match_id": match_id,
            "match_date": meta[match_id]["match_date"],
            "league_slug": meta[match_id]["league_slug"],
            "season": meta[match_id]["season"],
            "split": meta[match_id]["split"],
            "home_team_id": home["team_id"],
            "away_team_id": away["team_id"],
            "home_goals": home["goals"],
            "away_goals": away["goals"],
            **{f"home_{metric}": home[metric] for metric in COUNT_METRICS},
            **{f"away_{metric}": away[metric] for metric in COUNT_METRICS},
        })

    rows.sort(key=lambda item: (item["match_date"], item["match_id"]))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    by_split: dict[str, int] = defaultdict(int)
    by_league: dict[str, int] = defaultdict(int)
    for row in rows:
        by_split[row["split"]] += 1
        by_league[row["league_slug"]] += 1

    return {
        "matches": len(rows),
        "leagues": len(by_league),
        "splits": dict(sorted(by_split.items())),
        "rejected": rejected,
        "date_range": [rows[0]["match_date"], rows[-1]["match_date"]] if rows else [],
        "output": str(output),
    }


def main() -> None:
    """Construye el corpus y publica su resumen."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    summary = build(args.source, args.output)
    summary_path = args.output.parent / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
