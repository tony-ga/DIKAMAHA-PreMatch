"""Evalúa escaleras Markov por equipo con selección y confirmación separadas.

# Requirements:
# pip install numpy python-dotenv sqlalchemy

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import (  # noqa: E402
    SOURCE, SOURCE_2024, _matches, _read_2024, _read_current, _team_mapping,
)
from src.team_count_market_runtime import LADDER_MAXIMUMS  # noqa: E402
from src.team_market_markov import METRICS, TeamMarketMarkov  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_103_distributional_market_walkforward"
BOOTSTRAP = 10_000
MIN_MATCHES = 200
MIN_LEAGUE_MATCHES = 30
PERIODS = ("first_half", "second_half", "full_match")


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_id(side: str, metric: str, period: str, threshold: int) -> str:
    """Construye un identificador estable de línea."""

    return f"{side}_{metric}_{period}_over_{threshold}_5"


def _groups() -> Iterable[tuple[str, str, str, int]]:
    """Enumera las líneas congeladas de Markov."""

    for side in ("home", "away"):
        for metric in METRICS:
            for period in PERIODS:
                key = "full_match" if period == "full_match" else "half"
                for threshold in range(LADDER_MAXIMUMS[metric][key]):
                    yield side, metric, period, threshold


def _over(distribution: dict[int, float], threshold: int) -> float:
    """Obtiene la cola superior de una PMF."""

    return float(sum(value for count, value in distribution.items()
                     if int(count) > threshold))


def _actual(match: dict[str, Any], side: str, metric: str,
            period: str) -> int:
    """Obtiene un conteo observado después de emitir la predicción."""

    counts = [int(row[metric]) for row in match[side]]
    values = counts[:3] if period == "first_half" else counts[3:]
    return sum(counts) if period == "full_match" else sum(values)


def _prediction_rows(match: dict[str, Any],
                     model: TeamMarketMarkov) -> list[dict[str, Any]]:
    """Predice todas las líneas de un partido sin actualizarlo."""

    trajectories = model.predict_match(match)
    rows: list[dict[str, Any]] = []
    for side, metric, period, threshold in _groups():
        trajectory = trajectories[side]
        key = f"{metric}_{period}"
        rows.append(_prediction(match, side, metric, period, threshold,
                                trajectory.distributions[key],
                                trajectory.baseline_distributions[key]))
    return rows


def _prediction(match: dict[str, Any], side: str, metric: str,
                period: str, threshold: int, pmf: dict[int, float],
                baseline: dict[int, float]) -> dict[str, Any]:
    """Materializa una predicción y su target aislado."""

    count = _actual(match, side, metric, period)
    return {
        "match_id": int(match["match_id"]),
        "match_date": str(match["match_date"]),
        "league_slug": str(match["league_slug"]),
        "line_id": _line_id(side, metric, period, threshold),
        "group": f"{side}_{metric}_{period}",
        "line": threshold + 0.5,
        "probability": _over(pmf, threshold),
        "baseline_probability": _over(baseline, threshold),
        "actual": int(count > threshold),
    }


def _walk_forward(matches: list[dict[str, Any]],
                  split: str) -> list[dict[str, Any]]:
    """Ejecuta replay causal y conserva una sola partición."""

    model, rows = TeamMarketMarkov(), []
    for index, match in enumerate(matches, start=1):
        if match["split"] == split:
            rows.extend(_prediction_rows(match, model))
        model.update(match)
        if index % 1000 == 0:
            LOGGER.info("%s: %s/%s partidos", split, index, len(matches))
    return rows


def _loss(probability: float, actual: int) -> float:
    """Calcula log-loss binario acotado."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def _ece(rows: list[dict[str, Any]], field: str) -> float:
    """Calcula ECE con diez bins fijos."""

    total, error = len(rows), 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        values = [row for row in rows
                  if lower <= float(row[field]) < lower + 0.1]
        if values:
            confidence = np.mean([row[field] for row in values])
            frequency = np.mean([row["actual"] for row in values])
            error += len(values) * abs(float(confidence - frequency))
    return float(error / max(total, 1))


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega scores propios y calibración de una línea."""

    model_loss = [_loss(row["probability"], row["actual"]) for row in rows]
    base_loss = [_loss(row["baseline_probability"], row["actual"])
                 for row in rows]
    model_brier = [(row["probability"] - row["actual"]) ** 2 for row in rows]
    base_brier = [(row["baseline_probability"] - row["actual"]) ** 2
                  for row in rows]
    return {
        "matches": len(rows),
        "positive_rate": float(np.mean([row["actual"] for row in rows])),
        "model_log_loss": float(np.mean(model_loss)),
        "baseline_log_loss": float(np.mean(base_loss)),
        "log_loss_improvement": float(np.mean(np.subtract(base_loss,
                                                          model_loss))),
        "model_brier": float(np.mean(model_brier)),
        "baseline_brier": float(np.mean(base_brier)),
        "model_ece": _ece(rows, "probability"),
        "baseline_ece": _ece(rows, "baseline_probability"),
    }


def _by_line(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Agrupa predicciones por línea."""

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[str(row["line_id"])].append(row)
    return dict(output)


def _eligible(metric: dict[str, Any]) -> bool:
    """Aplica el gate de desarrollo congelado."""

    return (
        metric["matches"] >= MIN_MATCHES
        and 0.05 <= metric["positive_rate"] <= 0.95
        and metric["log_loss_improvement"] > 0.0
        and metric["model_brier"] <= metric["baseline_brier"]
        and metric["model_ece"] <= metric["baseline_ece"] + 0.01
    )


def _select(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                dict[str, Any]]:
    """Elige como máximo una línea por grupo usando sólo selection."""

    grouped, selected = _by_line(rows), []
    metrics = {name: _metrics(values) for name, values in grouped.items()}
    candidates: dict[str, list[str]] = defaultdict(list)
    for name, values in grouped.items():
        if _eligible(metrics[name]):
            candidates[str(values[0]["group"])].append(name)
    for group, names in sorted(candidates.items()):
        best = min(names, key=lambda name: _selection_rank(
            name, metrics[name], grouped[name][0]["line"]))
        selected.append({"group": group, "line_id": best,
                         "line": grouped[best][0]["line"],
                         "selection_metrics": metrics[best]})
    return selected, metrics


def _selection_rank(name: str, metric: dict[str, Any],
                    line: float) -> tuple[float, float, float, str]:
    """Define desempate determinista de selección."""

    brier_gain = metric["baseline_brier"] - metric["model_brier"]
    return (-metric["log_loss_improvement"], -brier_gain, line, name)


def _bootstrap(rows: list[dict[str, Any]]) -> list[float]:
    """Remuestrea mejora pareada por partido completo."""

    values = np.asarray([
        _loss(row["baseline_probability"], row["actual"])
        - _loss(row["probability"], row["actual"]) for row in rows
    ])
    generator = np.random.default_rng(103_000)
    samples: list[float] = []
    for size in _bootstrap_chunks(BOOTSTRAP, 250):
        indices = generator.integers(0, len(values), (size, len(values)))
        samples.extend(np.mean(values[indices], axis=1).tolist())
    return [float(np.quantile(samples, 0.025)),
            float(np.quantile(samples, 0.975))]


def _bootstrap_chunks(total: int, maximum: int) -> Iterable[int]:
    """Divide remuestras para limitar memoria sin alterar el cálculo."""

    completed = 0
    while completed < total:
        size = min(maximum, total - completed)
        yield size
        completed += size


def _league_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mide no degradación entre ligas con soporte suficiente."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(row)
    eligible = {name: _metrics(values) for name, values in grouped.items()
                if len(values) >= MIN_LEAGUE_MATCHES}
    stable = sum(value["log_loss_improvement"] >= 0.0
                 for value in eligible.values())
    return {"eligible_leagues": len(eligible), "non_degraded": stable,
            "non_degraded_rate": stable / max(len(eligible), 1),
            "leagues": eligible}


def _confirm(selected: list[dict[str, Any]],
             rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Abre confirmación sólo para candidatos congelados."""

    grouped, output = _by_line(rows), []
    for candidate in selected:
        values = grouped.get(candidate["line_id"], [])
        metric, stability = _metrics(values), _league_stability(values)
        interval = _bootstrap(values) if values else [float("nan")] * 2
        passed = _passed(metric, interval, stability)
        output.append({**candidate, "confirmation_metrics": metric,
                       "bootstrap_improvement_ci95": interval,
                       "league_stability": stability, "passed": passed})
    return output


def _passed(metric: dict[str, Any], interval: list[float],
            stability: dict[str, Any]) -> bool:
    """Aplica el gate confirmatorio completo."""

    return (
        metric["matches"] >= MIN_MATCHES and interval[0] > 0.0
        and metric["model_brier"] <= metric["baseline_brier"]
        and metric["model_ece"] <= metric["baseline_ece"]
        and stability["eligible_leagues"] > 0
        and stability["non_degraded_rate"] >= 0.70
    )


def _write(name: str, payload: Any) -> Path:
    """Escribe JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")
    return path


def _report(result: dict[str, Any]) -> str:
    """Renderiza el reporte ejecutivo."""

    coverage, confirmed = result["coverage"], result["confirmation"]
    lines = ["# Fase 103 — evaluación distribucional walk-forward", "",
             f"**Clasificación:** `{result['classification']}`", "",
             f"- corpus: `{coverage['matches']}` partidos",
             f"- selection: `{coverage['selection_matches']}` partidos",
             f"- confirmation: `{coverage['confirmation_matches']}` partidos",
             f"- líneas evaluadas: `{coverage['selection_lines']}`",
             f"- candidatos congelados: `{len(result['selection'])}`",
             f"- líneas aprobadas: `{sum(row['passed'] for row in confirmed)}`",
             "- bootstrap: `10,000` remuestras por partido", "",
             "## Resultado por candidato", "",
             "| Línea | Δ log-loss | IC95% | Δ Brier | Ligas | Gate |",
             "| --- | ---: | ---: | ---: | ---: | --- |"]
    for row in confirmed:
        metric, stability = row["confirmation_metrics"], row["league_stability"]
        brier = metric["baseline_brier"] - metric["model_brier"]
        lines.append(f"| {row['line_id']} | "
                     f"{metric['log_loss_improvement']:.6f} | "
                     f"[{row['bootstrap_improvement_ci95'][0]:.6f}, "
                     f"{row['bootstrap_improvement_ci95'][1]:.6f}] | "
                     f"{brier:.6f} | "
                     f"{stability['non_degraded']}/"
                     f"{stability['eligible_leagues']} | "
                     f"{'APROBADA' if row['passed'] else 'NO'} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    """Ejecuta selección sellada y confirmación de Fase 103."""

    matches = _matches(_read_2024(_team_mapping()) + _read_current())
    selection_rows = _walk_forward(matches, "selection")
    selected, selection_metrics = _select(selection_rows)
    manifest = _write("selection_manifest.json", selected)
    manifest_hash = _sha(manifest)
    LOGGER.info("Selección congelada: %s", manifest_hash)
    confirmation_rows = _walk_forward(matches, "confirmation")
    confirmed = _confirm(selected, confirmation_rows)
    result = _result(matches, selection_rows, confirmation_rows, selected,
                     selection_metrics, confirmed, manifest_hash)
    _publish(result, confirmation_rows)
    return result


def _result(matches: list[dict[str, Any]], selection_rows: list[dict[str, Any]],
            confirmation_rows: list[dict[str, Any]],
            selected: list[dict[str, Any]], selection_metrics: dict[str, Any],
            confirmed: list[dict[str, Any]], manifest_hash: str) -> dict[str, Any]:
    """Construye el resultado auditable."""

    passed = sum(row["passed"] for row in confirmed)
    selection_ids = {row["match_id"] for row in selection_rows}
    confirmation_ids = {row["match_id"] for row in confirmation_rows}
    return {
        "classification": "validated_lines" if passed else "no_lines_promoted",
        "config": {"bootstrap_replicates": BOOTSTRAP,
                   "minimum_matches": MIN_MATCHES,
                   "minimum_league_matches": MIN_LEAGUE_MATCHES},
        "coverage": {"matches": len(matches),
                     "selection_matches": len(selection_ids),
                     "confirmation_matches": len(confirmation_ids),
                     "selection_lines": len(selection_metrics),
                     "confirmation_lines": len(_by_line(confirmation_rows)),
                     "leagues": len({row["league_slug"] for row in matches})},
        "audit": {"prediction_before_update": True,
                  "target_match_features_used": False,
                  "selection_confirmation_disjoint":
                      selection_ids.isdisjoint(confirmation_ids),
                  "selection_manifest_sha256": manifest_hash,
                  "confirmation_opened_after_manifest": True,
                  "bootstrap_unit": "complete_match"},
        "selection": selected, "selection_metrics": selection_metrics,
        "confirmation": confirmed,
    }


def _publish(result: dict[str, Any],
             confirmation_rows: list[dict[str, Any]]) -> None:
    """Publica artefactos y hashes de la fase."""

    selected = {row["line_id"] for row in result["selection"]}
    frozen = [row for row in confirmation_rows if row["line_id"] in selected]
    for name in ("config", "coverage", "audit", "selection_metrics",
                 "confirmation"):
        _write(f"{name}.json", result[name])
    _write("confirmation_predictions.json", frozen)
    _write("input_manifest.json", {"phase74_sha256": _sha(SOURCE),
                                    "phase01_sha256": _sha(SOURCE_2024)})
    report = _report(result)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.name != "hashes.json"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    phase_result = run()
    assert phase_result["audit"]["selection_confirmation_disjoint"]
    assert phase_result["audit"]["prediction_before_update"]
    assert phase_result["coverage"]["selection_lines"] == 218
    LOGGER.info("Fase 103: %s", phase_result["classification"])

# Version: 1.0.0
# Created: 2026-07-29
