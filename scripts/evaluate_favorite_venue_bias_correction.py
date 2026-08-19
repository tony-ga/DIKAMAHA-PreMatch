"""Recalibración de sesgo por clase para el favorito visitante (Fase 127-129
continuación; ver `DEC-212`/`DEC-214`).

`DEC-212` midió que ni la temperatura ni el peso de mezcla segmentados por
localía del favorito distinguen del baseline servido. Ese resultado no dice
"no hay sesgo por localía" -dice que ESOS DOS parámetros no lo capturan-.
Una investigación adicional sobre el corpus walk-forward completo
(`artifacts/walkforward_predictions/baseline.jsonl`, 3,538 partidos con
favorito, split selection/confirmation) encontró el motivo exacto: el sesgo
del favorito visitante no es una sobreconfianza simétrica -que la temperatura
sí corregiría-, es una reasignación de masa específica entre "gana" y
"pierde", con "empata" casi exactamente calibrado:

    favorito visitante (n=1,075): P(gana) declarada 48.28% vs real 44.37%
    (sesgo +3.91pp, IC95% [+0.95,+6.85], NO cruza cero); P(pierde) declarada
    26.25% vs real 31.26% (sesgo -5.00pp, IC95% [-7.80,-2.30], NO cruza
    cero); P(empata) declarada 25.47% vs real 24.37% (sesgo +1.09pp, IC95%
    [-1.53,+3.65], SÍ cruza cero).
    favorito local (n=2,463): los tres sesgos cruzan cero -bien calibrado-.

Un único parámetro T no puede reproducir esta forma -mueve las tres clases
en la misma dirección relativa, no puede achicar sólo "gana" y agrandar sólo
"pierde" dejando "empata" fijo-. Este candidato prueba la herramienta
correcta para esa forma: dos sesgos aditivos en log-espacio (softmax de
`log(p) + bias`), uno para la clase del favorito y otro para la del
no-favorito, con la clase de empate como referencia fija (`bias_draw = 0`).
Ajustados en selección sólo para el subgrupo favorito-visitante, medidos en
confirmación contra la salida ya servida (peso 0.642848 + T 1.198935).

Aviso de seguridad -no es cosmético-: a diferencia de la temperatura, esta
recalibración NO garantiza preservar el argmax (puede voltear cuál lado es
favorito en partidos muy parejos). Si este candidato pasara el gate,
conectarlo exigiría una decisión de arquitectura aparte, no sólo evidencia
estadística -ver `model_composition_v1.md`, R6, sobre por qué la temperatura
se eligió precisamente por esa propiedad-.

Uso:
    python -m scripts.evaluate_favorite_venue_bias_correction

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from scripts.evaluate_candidates import (  # noqa: E402
    _blend,
    _brier,
    _load,
    _log_loss,
    _paired_bootstrap,
)
from scripts.evaluate_favorite_venue_temperature import (  # noqa: E402
    _favorite_venue,
    _load_adopted,
    _served,
    DEFAULT_ADOPTED,
    DEFAULT_INPUT,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/candidate_evaluation/favorite_venue_bias_correction.json"
LABELS = ("1", "X", "2")


def _favorite_underdog_codes(venue: str) -> tuple[str, str]:
    """Códigos de clase del favorito y del no-favorito segun localía."""

    return ("1", "2") if venue == "home" else ("2", "1")


def _apply_bias(
    probabilities: dict[str, float], favorite_code: str, underdog_code: str,
    bias_favorite: float, bias_underdog: float,
) -> dict[str, float]:
    """Softmax(log(p) + sesgo), con la clase de empate como referencia fija."""

    logits = {
        label: math.log(max(value, 1e-15)) for label, value in probabilities.items()
    }
    logits[favorite_code] += bias_favorite
    logits[underdog_code] += bias_underdog
    exp = {label: math.exp(value) for label, value in logits.items()}
    total = sum(exp.values())
    return {label: value / total for label, value in exp.items()}


def _fit_bias(
    rows: list[dict[str, Any]], venue: str,
) -> tuple[float, float]:
    """Ajusta (sesgo_favorito, sesgo_no_favorito) por máxima verosimilitud."""

    favorite_code, underdog_code = _favorite_underdog_codes(venue)

    def negative_log_likelihood(params: np.ndarray) -> float:
        bias_favorite, bias_underdog = params
        total = 0.0
        for row in rows:
            calibrated = _apply_bias(
                row["_served"], favorite_code, underdog_code,
                bias_favorite, bias_underdog)
            total -= math.log(max(calibrated[row["outcome"]], 1e-15))
        return total / len(rows)

    result = minimize(
        negative_log_likelihood, x0=np.array([0.0, 0.0]),
        method="Nelder-Mead",
        options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 2000},
    )
    if not result.success:
        raise RuntimeError("bias_correction_did_not_converge")
    return float(result.x[0]), float(result.x[1])


def evaluate(rows: list[dict[str, Any]], adopted: dict[str, float]) -> dict[str, Any]:
    weight, temperature = adopted["weight"], adopted["temperature"]
    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]

    for row in selection + confirmation:
        row["_served"] = _served(row, weight, temperature)
        row["_favorite_venue"] = _favorite_venue(row["_served"])

    report: dict[str, Any] = {}
    for venue in ("home", "away"):
        selection_group = [
            row for row in selection if row["_favorite_venue"] == venue]
        confirmation_group = [
            row for row in confirmation if row["_favorite_venue"] == venue]

        bias_favorite, bias_underdog = _fit_bias(selection_group, venue)
        favorite_code, underdog_code = _favorite_underdog_codes(venue)

        baseline = [row["_served"] for row in confirmation_group]
        candidate = [
            _apply_bias(
                row["_served"], favorite_code, underdog_code,
                bias_favorite, bias_underdog)
            for row in confirmation_group
        ]
        outcomes = [row["outcome"] for row in confirmation_group]

        log_loss_delta = np.array([
            _log_loss(base, outcome) - _log_loss(cand, outcome)
            for base, cand, outcome in zip(baseline, candidate, outcomes)
        ])
        brier_delta = np.array([
            _brier(base, outcome) - _brier(cand, outcome)
            for base, cand, outcome in zip(baseline, candidate, outcomes)
        ])

        argmax_flips = sum(
            1 for base, cand in zip(baseline, candidate)
            if max(base, key=base.get) != max(cand, key=cand.get))

        report[venue] = {
            "selection_matches": len(selection_group),
            "confirmation_matches": len(confirmation_group),
            "bias_favorite_logit": bias_favorite,
            "bias_underdog_logit": bias_underdog,
            "log_loss": _paired_bootstrap(log_loss_delta),
            "brier": _paired_bootstrap(brier_delta),
            "argmax_flips_in_confirmation": argmax_flips,
            "argmax_flip_rate": argmax_flips / len(confirmation_group),
        }
    return report


def main() -> None:
    rows = _load(DEFAULT_INPUT)
    adopted = _load_adopted(DEFAULT_ADOPTED)
    report = evaluate(rows, adopted)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for venue, block in report.items():
        print(f"--- favorito {venue} (selección={block['selection_matches']}, "
              f"confirmación={block['confirmation_matches']}) ---", flush=True)
        print(f"  sesgo favorito (logit) = {block['bias_favorite_logit']:+.4f}",
              flush=True)
        print(f"  sesgo no-favorito (logit) = {block['bias_underdog_logit']:+.4f}",
              flush=True)
        for metric in ("log_loss", "brier"):
            stats = block[metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(f"  volteos de argmax en confirmación: "
              f"{block['argmax_flips_in_confirmation']} "
              f"({block['argmax_flip_rate']:.2%})", flush=True)
        print(flush=True)

    print(f"artefacto: {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
