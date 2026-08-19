"""¿Aporta `save` información que el motor no tenga ya? (`DEC-217`, 116G).

Fase 116F rechazó proyectar `save` a `shot_on_target`: no es una parada de
portero -7.06 jugadores distintos por partido lo registran, incluidos
delanteros- y el 53.3% coincide con un `shot_blocked` que el modelo ya cuenta.

Pero eso rechaza **un mapeo**, no la señal. Queda una pregunta distinta y
legítima, que 116F dejó apuntada sin responder: ¿el volumen de `save` predice
goles futuros **por encima** de lo que ya predicen los eventos que el motor sí
consume? Si la respuesta es no, `save` no merece un peso propio en
`EVENT_WEIGHTS` y la decisión se cierra del todo. Si es sí, hay un candidato
que sí vale la pena construir.

El diseño evita el error que arruinó los intentos anteriores. Se compara la
capacidad predictiva **condicional**: dos regresiones de Poisson sobre los
goles de la ventana siguiente, una con los eventos que el motor ya ve
(`shot_on_target`, `shot_blocked`, `shot_off_target`, `corner`) y otra que
añade `save`. Si el aporte de `save` fuera sólo el bloqueo que ya está
contado, el segundo modelo no mejoraría. Se mide fuera de muestra, con
partición por partido y remuestreo por partido -nunca por ventana-.

Es de solo lectura: no escribe en la base ni toca ningún artefacto servido.

Uso:
    python -m scripts.run_phase_116g_save_incremental_information

Version: 1.0.0
Created: 2026-08-19
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sklearn.linear_model import PoissonRegressor  # noqa: E402

OUTPUT = ROOT / "artifacts/phase_116g_save_incremental_information"
WINDOW_SECONDS = 900.0
WINDOWS = 6
SEED = 42
BOOTSTRAP = 2000

# Eventos que el motor ya consume y pesa en `EVENT_WEIGHTS`.
BASELINE_EVENTS = ("shot_on_target", "shot_blocked", "shot_off_target", "corner")

QUERY = """
SELECT
    e.provider_match_id AS mid,
    e.team_provider_id  AS tid,
    CASE WHEN e.event_type_raw = 'save' THEN 'save' ELSE e.event_type END AS kind,
    COALESCE(
        NULLIF(e.raw_data -> 'clock' ->> 'value', '')::double precision,
        e.minute * 60.0 + e.second
    ) AS clock
FROM prospective_staging_v2.events e
WHERE NOT e.annulled
  AND e.provider_match_id ~ '^[0-9]+$'
  AND e.team_provider_id IS NOT NULL
  AND (e.event_type IN ('goal','shot_on_target','shot_blocked','shot_off_target','corner')
       OR e.event_type_raw = 'save')
"""


def _build_panel(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Panel (equipo, ventana): features de la ventana, goles de la siguiente."""

    by_key: dict[tuple[str, int, int], dict[str, float]] = {}
    for row in rows:
        clock = float(row["clock"])
        if clock < 0 or clock >= WINDOW_SECONDS * WINDOWS:
            continue
        window = int(clock // WINDOW_SECONDS)
        key = (str(row["mid"]), int(row["tid"]), window)
        bucket = by_key.setdefault(key, {})
        bucket[str(row["kind"])] = bucket.get(str(row["kind"]), 0.0) + 1.0

    features, targets, matches = [], [], []
    for (match_id, team_id, window), bucket in by_key.items():
        if window >= WINDOWS - 1:
            continue
        nxt = by_key.get((match_id, team_id, window + 1), {})
        features.append([bucket.get(name, 0.0) for name in BASELINE_EVENTS]
                        + [bucket.get("save", 0.0), float(window)])
        targets.append(nxt.get("goal", 0.0))
        matches.append(match_id)
    return (np.asarray(features, dtype=float),
            np.asarray(targets, dtype=float),
            np.asarray(matches))


def _deviance(actual: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    predicted = np.clip(predicted, 1e-9, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(actual > 0, actual * np.log(actual / predicted), 0.0)
    return 2.0 * (predicted - actual + ratio)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not url:
        raise RuntimeError("DATABASE_URL_missing")

    engine = create_engine(url, future=True, pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        rows = [dict(row) for row in connection.execute(text(QUERY)).mappings()]
    engine.dispose()
    print(f"eventos leídos: {len(rows)}", flush=True)

    features, targets, matches = _build_panel(rows)
    print(f"observaciones (equipo x ventana): {len(features)}", flush=True)
    print(f"partidos: {len(np.unique(matches))}", flush=True)
    print(f"media de saves por observación: {features[:, 4].mean():.3f}", flush=True)

    unique = np.unique(matches)
    generator = np.random.default_rng(SEED)
    generator.shuffle(unique)
    cut = int(len(unique) * 0.7)
    train_ids, test_ids = set(unique[:cut]), set(unique[cut:])
    train = np.array([m in train_ids for m in matches])
    test = np.array([m in test_ids for m in matches])
    print(f"partición por partido: train={train.sum()} test={test.sum()}",
          flush=True)

    # sin `save` (columnas 0..3 + ventana) y con `save` (todas)
    without = [0, 1, 2, 3, 5]
    with_save = [0, 1, 2, 3, 4, 5]

    results = {}
    deviances = {}
    for name, columns in (("baseline", without), ("con_save", with_save)):
        model = PoissonRegressor(alpha=1.0, max_iter=2000, tol=1e-8)
        model.fit(features[train][:, columns], targets[train])
        predicted = model.predict(features[test][:, columns])
        per_row = _deviance(targets[test], predicted)
        deviances[name] = per_row
        results[name] = {
            "mean_deviance": float(per_row.mean()),
            "coefficients": dict(zip(
                [(BASELINE_EVENTS + ("save", "window"))[c] for c in columns],
                [float(v) for v in model.coef_])),
        }
        print(f"\n{name}: deviance fuera de muestra = {per_row.mean():.6f}",
              flush=True)
        for key, value in results[name]["coefficients"].items():
            print(f"    {key}: {value:+.5f}", flush=True)

    # bootstrap por PARTIDO del delta de deviance
    test_matches = matches[test]
    unique_test = np.unique(test_matches)
    delta = deviances["baseline"] - deviances["con_save"]
    index_by_match = {m: np.where(test_matches == m)[0] for m in unique_test}
    samples = np.empty(BOOTSTRAP)
    for replicate in range(BOOTSTRAP):
        drawn = generator.choice(unique_test, size=len(unique_test), replace=True)
        picked = np.concatenate([index_by_match[m] for m in drawn])
        samples[replicate] = delta[picked].mean()
    low, high = float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))
    crosses = bool(low <= 0.0 <= high)
    verdict = ("indistinguible" if crosses
               else ("save aporta información" if delta.mean() > 0
                     else "save degrada"))

    print(f"\ndelta de deviance (baseline - con_save): {delta.mean():+.6f}",
          flush=True)
    print(f"IC95% por partido: [{low:+.6f}, {high:+.6f}] -> {verdict}", flush=True)

    payload = {
        "question": (
            "¿el volumen de `save` predice goles futuros por encima de los "
            "eventos que el motor ya consume?"),
        "protocol": "poisson_regression_out_of_sample_split_by_match",
        "unit": "complete_match",
        "observations": int(len(features)),
        "matches": int(len(unique)),
        "mean_saves_per_observation": float(features[:, 4].mean()),
        "models": results,
        "incremental": {
            "mean_delta_deviance": float(delta.mean()),
            "ci_low": low, "ci_high": high, "crosses_zero": crosses,
            "verdict": verdict,
        },
        "conclusion": (
            "sin aporte incremental: `save` no merece un peso propio en "
            "EVENT_WEIGHTS" if crosses or delta.mean() <= 0 else
            "aporte incremental medido: existe un candidato que construir"),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "incremental.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    print(f"\nconclusión: {payload['conclusion']}", flush=True)
    print(f"artefacto: {args.output / 'incremental.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
