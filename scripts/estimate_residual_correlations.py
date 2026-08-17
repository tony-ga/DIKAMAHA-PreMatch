"""Reestima la correlación residual local-visitante sobre el corpus completo.

`combined_dispersion` (`src/team_count_markets.py`) añade el término `2ρσ_Hσ_A`
a la varianza del total. Los valores vigentes -córners `-0.31`, tiros `-0.15`,
tarjetas `+0.19`- se estimaron sobre el corpus de Fase 84A. Este script los
recalcula sobre los 9,465 partidos de Fase 74 y responde dos preguntas que la
estimación original no pudo:

1. ¿se sostienen los valores con 39 ligas en vez del corpus reducido?
2. ¿la correlación varía por liga lo bastante como para que un valor global sea
   el instrumento equivocado?

La correlación que entra en la varianza condicional es la **residual**, no la
bruta: la bruta mezcla la covariación de las medias entre partidos con la
covariación alrededor de ellas, y sólo la segunda pertenece a la varianza del
total. El residuo se toma contra la media de (liga, localía), que es el mismo
baseline que usa el modelo de conteo.

`fit`+`selection` estiman; `confirmation` verifica. Que ambos coincidan es la
única evidencia de que la estimación es estable y no un artefacto del bloque.

Uso:
    python -m scripts.estimate_residual_correlations

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "artifacts/team_count_corpus/team_matches.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/residual_correlations"

METRICS = ("corners", "shots", "shots_on_target", "yellow_cards", "fouls")
MIN_LEAGUE_MATCHES = 100

# El valor servido se lee del artefacto, no de la documentación. Los documentos
# de fase citan las correlaciones que se *estimaron* durante la auditoría, que no
# son necesariamente las que quedaron selladas; comparar contra el texto en vez
# de contra el artefacto produce discrepancias inventadas.
PRODUCTION_CONFIG = ROOT / "artifacts/phase_84a_team_count_markets/config.json"


def _production_values() -> dict[str, float]:
    """Lee las correlaciones realmente servidas."""

    if not PRODUCTION_CONFIG.is_file():
        return {}
    config = json.loads(PRODUCTION_CONFIG.read_text(encoding="utf-8"))
    return {
        str(key): float(value)
        for key, value in config.get("correlations", {}).items()
    }


def _pairs(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Reconstruye una fila por partido con el conteo de cada lado."""

    home = frame[frame["is_home"]][
        ["match_id", "league_slug", "split", metric]].rename(
            columns={metric: "home_value"})
    away = frame[~frame["is_home"]][["match_id", metric]].rename(
        columns={metric: "away_value"})
    return home.merge(away, on="match_id", how="inner")


def _residual_correlation(pairs: pd.DataFrame) -> dict[str, Any]:
    """Correlación de los residuos contra la media de (liga, localía)."""

    if len(pairs) < 30:
        return {"matches": len(pairs), "correlation": None}
    home_mean = pairs.groupby("league_slug")["home_value"].transform("mean")
    away_mean = pairs.groupby("league_slug")["away_value"].transform("mean")
    home_residual = pairs["home_value"] - home_mean
    away_residual = pairs["away_value"] - away_mean
    if home_residual.std() == 0 or away_residual.std() == 0:
        return {"matches": len(pairs), "correlation": None}
    return {
        "matches": int(len(pairs)),
        "correlation": float(np.corrcoef(home_residual, away_residual)[0, 1]),
        "raw_correlation": float(
            np.corrcoef(pairs["home_value"], pairs["away_value"])[0, 1]),
    }


def evaluate(frame: pd.DataFrame) -> dict[str, Any]:
    """Estima y verifica las correlaciones de cada métrica."""

    production = _production_values()
    report: dict[str, Any] = {
        "estimation_splits": ["fit", "selection"],
        "verification_split": "confirmation",
        "production_source": str(PRODUCTION_CONFIG),
        "residual_baseline": "league_mean_not_model_mean",
        "metrics": {},
    }

    for metric in METRICS:
        pairs = _pairs(frame, metric)
        estimation = pairs[pairs["split"].isin(("fit", "selection"))]
        verification = pairs[pairs["split"] == "confirmation"]

        estimated = _residual_correlation(estimation)
        verified = _residual_correlation(verification)

        per_league = {}
        for league, group in estimation.groupby("league_slug"):
            if len(group) >= MIN_LEAGUE_MATCHES:
                value = _residual_correlation(group)
                if value["correlation"] is not None:
                    per_league[league] = value["correlation"]

        spread = (
            {
                "leagues": len(per_league),
                "min": float(min(per_league.values())),
                "max": float(max(per_league.values())),
                "std": float(np.std(list(per_league.values()), ddof=1)),
            }
            if len(per_league) > 1 else {"leagues": len(per_league)}
        )

        report["metrics"][metric] = {
            "estimation": estimated,
            "verification": verified,
            "production_value": production.get(metric),
            "per_league": spread,
            "per_league_values": dict(sorted(per_league.items())),
        }

    return report


def main() -> None:
    """Publica el reporte de correlaciones."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    frame = pd.read_csv(args.corpus)
    report = evaluate(frame)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for metric, block in report["metrics"].items():
        estimated = block["estimation"]["correlation"]
        verified = block["verification"]["correlation"]
        production = block["production_value"]
        spread = block["per_league"]
        print(f"--- {metric} ---", flush=True)
        print(f"  residual estimación {estimated:+.4f} "
              f"(n={block['estimation']['matches']})  "
              f"verificación {verified:+.4f} "
              f"(n={block['verification']['matches']})", flush=True)
        if production is not None:
            print(f"  producción usa {production:+.4f}  "
                  f"desvío {estimated - production:+.4f}", flush=True)
        print(f"  bruta {block['estimation']['raw_correlation']:+.4f} "
              f"(mezcla covariación de medias, no entra en la varianza)",
              flush=True)
        if spread.get("leagues", 0) > 1:
            print(f"  por liga: {spread['leagues']} ligas, rango "
                  f"[{spread['min']:+.4f}, {spread['max']:+.4f}], "
                  f"desviación {spread['std']:.4f}", flush=True)
        print(flush=True)

    print(f"artefacto: {args.output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
