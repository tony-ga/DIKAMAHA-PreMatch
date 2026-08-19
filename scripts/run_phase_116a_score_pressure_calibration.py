"""Calibra la forma temporal de la presión de marcador (`DEC-216`, Fase 116A).

Mide el ratio empírico presión(perdiendo)/presión(ganando) por ventana de 15
minutos sobre el corpus causal de Fase 74, ajusta la rampa con umbral que
`live_probability_engine_v1` expone como `ramp_v2`, y la compara contra la
forma lineal vigente.

Protocolo:

- los parámetros se ajustan **sólo** sobre `split=fit`;
- la elección entre rampa y lineal se decide **sólo** sobre `split=selection`;
- `split=confirmation` no se lee -`load_windows` falla cerrado si se pide-;
- los ratios se pasan por `check_confounding` (`DEC-218`) agrupando por liga,
  porque un efecto que vive en dos ligas no es una forma temporal universal.

Uso:
    python -m scripts.run_phase_116a_score_pressure_calibration

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.confounding_check_v1 import (  # noqa: E402
    TRUSTWORTHY_VERDICTS,
    ConfoundingObservation,
    check_confounding,
)
from src.score_pressure_calibration_v1 import (  # noqa: E402
    empirical_ratios,
    fit_ramp,
    linear_ratio,
    load_windows,
    parameters_as_dict,
    ramp_ratio,
    weighted_error,
)

SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
OUTPUT = ROOT / "artifacts/phase_116a_score_pressure_calibration"


def _confounding_observations(
    rows: list[dict[str, Any]],
) -> list[ConfoundingObservation]:
    """Contrasta presión de quien pierde contra quien gana, agrupando por liga.

    `exposure` es ir perdiendo; `effect` la presión observada; `strength` la
    ventaja de marcador, que es justamente la variable que podría estar
    generando el efecto por sí sola. Si la presión extra fuera sólo un reflejo
    de la magnitud del marcador y no del tiempo, el control la delataría.
    """

    observations: list[ConfoundingObservation] = []
    for row in rows:
        difference = row["score_for_start"] - row["score_against_start"]
        if difference == 0:
            continue
        # Sólo la segunda mitad: es donde el diagnóstico de `DEC-216` sitúa
        # el efecto, y donde la forma nueva difiere de la vigente.
        if row["window_index"] < 3:
            continue
        observations.append(ConfoundingObservation(
            group_id=row["league_slug"],
            effect=row["pressure"],
            exposure=-1.0 if difference > 0 else 1.0,
            strength=float(abs(difference)),
        ))
    return observations


def main() -> int:
    """Ejecuta la calibración y publica el artefacto de fase."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    print("cargando corpus (sólo fit + selection)...", flush=True)
    fit_rows = load_windows(args.source, {"fit"})
    selection_rows = load_windows(args.source, {"selection"})
    print(f"  fit={len(fit_rows)} filas, selection={len(selection_rows)} filas",
          flush=True)

    fit_ratios = empirical_ratios(fit_rows)
    selection_ratios = empirical_ratios(selection_rows)

    print("\nratio empírico por ventana (fit):", flush=True)
    for window, entry in sorted(fit_ratios.items()):
        print(f"  ventana {window} (min {entry['midpoint']:>5.1f}): "
              f"{entry['ratio']:.4f} IC95%[{entry['ci_low']:.4f},"
              f"{entry['ci_high']:.4f}] partidos={entry['matches']}",
              flush=True)

    parameters = fit_ramp(fit_ratios)
    print(f"\nrampa ajustada en fit: onset={parameters.onset_fraction:.4f} "
          f"(min {parameters.onset_fraction * 90:.1f}), "
          f"curvature={parameters.curvature:.4f}, "
          f"gain={parameters.chasing_gain:.4f}", flush=True)

    ramp_error = weighted_error(
        selection_ratios, lambda midpoint: ramp_ratio(parameters, midpoint))
    linear_error = weighted_error(selection_ratios, linear_ratio)
    ramp_wins = ramp_error < linear_error
    print(f"\nerror ponderado en selection: rampa={ramp_error:.2f} "
          f"lineal={linear_error:.2f} -> "
          f"{'RAMPA' if ramp_wins else 'LINEAL'} gana", flush=True)

    print("\nchequeo de confusión (DEC-218), agrupando por liga...", flush=True)
    confounding = check_confounding(
        _confounding_observations(fit_rows), replicates=2000)
    print(f"  efecto={confounding['baseline_effect']:.4f} "
          f"IC95%[{confounding['ci_low']:.4f},{confounding['ci_high']:.4f}] "
          f"-> {confounding['verdict']}", flush=True)
    print(f"  grupo más influyente: "
          f"{confounding['influence']['most_influential_group']} "
          f"(ratio {confounding['influence']['influence_ratio']:.4f})",
          flush=True)

    confounding_ok = confounding["verdict"] in TRUSTWORTHY_VERDICTS
    promotable = bool(ramp_wins and confounding_ok)

    classification = (
        "ready_for_gate" if promotable else "rejected_for_revision")

    payload = {
        "classification": classification,
        "protocol": "fit_estimates_selection_chooses_confirmation_untouched",
        "unit": "complete_match",
        "coverage": {
            "fit_rows": len(fit_rows),
            "selection_rows": len(selection_rows),
            "confirmation_rows_read": 0,
        },
        "empirical_ratios": {
            "fit": {str(k): v for k, v in fit_ratios.items()},
            "selection": {str(k): v for k, v in selection_ratios.items()},
        },
        "fitted_parameters": parameters_as_dict(parameters),
        "model_comparison": {
            "ramp_weighted_error_selection": ramp_error,
            "linear_weighted_error_selection": linear_error,
            "ramp_wins": ramp_wins,
        },
        "confounding_check": confounding,
        "gates": {
            "confirmation_never_read": True,
            "ramp_beats_linear_in_selection": ramp_wins,
            "effect_not_confounded_by_league": confounding_ok,
            "promotable": promotable,
        },
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "calibration.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")

    print(f"\nclasificación: {classification}", flush=True)
    print(f"artefacto: {args.output / 'calibration.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
