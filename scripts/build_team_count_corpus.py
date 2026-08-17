"""Agrega conteos por equipo-partido desde el corpus causal de Fase 74.

`market_calibration.py` contrae la tasa de un mercado hacia un prior con un
`shrinkage` **fijo por mercado**, igual para una liga con 40 observaciones que
para una con 4,000. B.2 de `arquitectura_matematica_v1` propone derivarlo de los
datos con `w_j = σ_j²/(σ_j²+τ²)` (Murphy *PML* p.146). Para medir esa diferencia
hace falta el conteo por equipo y partido, que las micro-ventanas ya contienen.

La unidad de salida es el **equipo-partido**: dos filas por partido. El scoring
posterior sigue agregando por partido completo (R7), pero la contracción se
estima sobre la tasa que el mercado realmente publica, que es por equipo.

Uso:
    python -m scripts.build_team_count_corpus

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
DEFAULT_OUTPUT = ROOT / "artifacts/team_count_corpus/team_matches.csv"

# Métricas cuyo cero es plausible en un partido profesional se conservan tal
# cual; el guard de cobertura por liga vive aguas abajo, en `metric_coverage`.
METRICS = ("corners", "shots", "shots_on_target", "yellow_cards", "fouls")

FIELDS = (
    "match_id", "match_date", "league_slug", "season", "split",
    "team_id", "opponent_team_id", "is_home", *METRICS,
)


def build(source: Path, output: Path) -> dict[str, Any]:
    """Suma las ventanas de cada equipo-partido y escribe el CSV."""

    rows: dict[tuple[int, int], dict[str, Any]] = {}

    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            key = (int(record["match_id"]), int(record["team_id"]))
            entry = rows.get(key)
            if entry is None:
                entry = {
                    "match_id": int(record["match_id"]),
                    "match_date": str(record["match_date"]),
                    "league_slug": str(record["league_slug"]),
                    "season": str(record["season"]),
                    "split": str(record["split"]),
                    "team_id": int(record["team_id"]),
                    "opponent_team_id": int(record["opponent_team_id"]),
                    "is_home": bool(record["is_home"]),
                    "windows": 0,
                    **{metric: 0 for metric in METRICS},
                }
                rows[key] = entry
            entry["windows"] += 1
            for metric in METRICS:
                entry[metric] += int(record.get(metric, 0) or 0)

    windows_per_match: dict[int, set[int]] = defaultdict(set)
    for (match_id, _), entry in rows.items():
        windows_per_match[match_id].add(entry["windows"])

    # Un partido cuyos dos lados no comparten número de ventanas tiene un conteo
    # incompleto en alguno: sin este filtro publicaría un total bajo sin avisar.
    unbalanced = {
        match_id for match_id, counts in windows_per_match.items()
        if len(counts) != 1
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    kept = [
        {field: entry[field] for field in FIELDS}
        for entry in rows.values() if entry["match_id"] not in unbalanced
    ]
    kept.sort(key=lambda item: (item["match_date"], item["match_id"],
                                item["team_id"]))

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(kept)

    by_split: dict[str, int] = defaultdict(int)
    for row in kept:
        by_split[row["split"]] += 1

    return {
        "team_matches": len(kept),
        "matches": len({row["match_id"] for row in kept}),
        "leagues": len({row["league_slug"] for row in kept}),
        "splits": dict(sorted(by_split.items())),
        "rejected_unbalanced_matches": len(unbalanced),
        "metrics": list(METRICS),
        "output": str(output),
    }


def main() -> None:
    """Construye el corpus de conteos y publica su resumen."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    summary = build(args.source, args.output)
    (args.output.parent / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
