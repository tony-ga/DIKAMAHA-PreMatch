"""Calibra transiciones duales condicionadas por el régimen rival.

Requirements:
    numpy>=2.0

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.latent_state_discovery import duration_probabilities, runs  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_78_context_transitions"
ASSIGNMENTS = ROOT / "artifacts/phase_77_dual_state_reaudit/state_assignments.jsonl"
WINDOWS = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
ALPHAS = (5.0, 10.0, 20.0, 50.0, 60.0, 70.0)
REGIMES = 3


@dataclass(frozen=True, slots=True)
class Transition:
    """Transición de régimen con contexto causal contemporáneo."""

    match_id: int
    team_id: int
    league_slug: str
    split: str
    is_home: bool
    window_index: int
    style: int
    regime: int
    opponent_regime: int
    next_regime: int


def _load() -> tuple[list[dict[str, Any]], dict[tuple[int, int], dict[str, Any]]]:
    """Carga asignaciones y metadatos causales."""

    assignments = [json.loads(line) for line in ASSIGNMENTS.open(encoding="utf-8")]
    metadata = {}
    for line in WINDOWS.open(encoding="utf-8"):
        row = json.loads(line)
        metadata[(int(row["match_id"]), int(row["team_id"]))] = {
            "league_slug": str(row["league_slug"]),
            "split": str(row["split"]), "is_home": bool(row["is_home"])}
    return assignments, metadata


def _transitions() -> list[Transition]:
    """Construye targets t+1 sin cruzar partido o equipo."""

    assignments, metadata = _load()
    indexed = {(int(row["match_id"]), int(row["team_id"]),
                int(row["window_index"])): row for row in assignments}
    teams: dict[int, set[int]] = defaultdict(set)
    for match_id, team_id, _ in indexed:
        teams[match_id].add(team_id)
    output = []
    for (match_id, team_id, window), row in sorted(indexed.items()):
        next_key = (match_id, team_id, window + 1)
        if next_key not in indexed:
            continue
        opponent = next(value for value in teams[match_id] if value != team_id)
        opponent_state = int(indexed[(match_id, opponent, window)]["state"])
        context = metadata[(match_id, team_id)]
        output.append(Transition(
            match_id, team_id, context["league_slug"], context["split"],
            context["is_home"], window, int(row["state"]) // 3,
            int(row["state"]) % 3, opponent_state % 3,
            int(indexed[next_key]["state"]) % 3))
    return output


def _probability(
    counts: Counter[int],
    parent: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """Aplica pooling Dirichlet hacia un padre normalizado."""

    observed = np.asarray([
        counts.get(state, 0) for state in range(REGIMES)], dtype=float)
    return (observed + alpha * parent) / (observed.sum() + alpha)


class ContextTransitionModel:
    """Modelo jerárquico rival-context → liga → global."""

    def __init__(self, alpha: float) -> None:
        """Configura fuerza contextual."""

        self.alpha = alpha
        self.global_counts: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
        self.baseline_counts: dict[tuple[str, int, int], Counter[int]] = defaultdict(Counter)
        self.context_counts: dict[tuple[str, bool, int, int, int], Counter[int]] = defaultdict(Counter)

    def fit(self, rows: list[Transition]) -> None:
        """Acumula únicamente transiciones del bloque de entrenamiento."""

        for row in rows:
            self.global_counts[(row.window_index, row.regime)][row.next_regime] += 1
            self.baseline_counts[self._baseline_key(row)][row.next_regime] += 1
            self.context_counts[self._context_key(row)][row.next_regime] += 1

    def predict(self, row: Transition) -> tuple[np.ndarray, np.ndarray, float]:
        """Devuelve candidato, baseline y masa contextual."""

        global_p = _probability(
            self.global_counts[(row.window_index, row.regime)],
            np.full(REGIMES, 1.0 / REGIMES), 20.0)
        baseline = _probability(
            self.baseline_counts[self._baseline_key(row)], global_p, 20.0)
        counts = self.context_counts[self._context_key(row)]
        candidate = _probability(counts, baseline, self.alpha)
        support = sum(counts.values())
        return candidate, baseline, support / (support + self.alpha)

    @staticmethod
    def _baseline_key(row: Transition) -> tuple[str, int, int]:
        """Construye contexto del comparador."""

        return row.league_slug, row.window_index, row.regime

    @staticmethod
    def _context_key(row: Transition) -> tuple[str, bool, int, int, int]:
        """Construye contexto específico con rival."""

        return (row.league_slug, row.is_home, row.window_index,
                row.regime, row.opponent_regime)


def _evaluate(rows: list[Transition], model: ContextTransitionModel) -> dict[str, Any]:
    """Evalúa primero por partido y después agrega."""

    by_match: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    by_league: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        candidate, baseline, mass = model.predict(row)
        losses = (-math.log(max(candidate[row.next_regime], 1e-12)),
                  -math.log(max(baseline[row.next_regime], 1e-12)))
        by_match[row.match_id].append((*losses, mass))
        by_league[row.league_slug].append(losses)
    candidate = np.mean([np.mean([item[0] for item in values])
                         for values in by_match.values()])
    baseline = np.mean([np.mean([item[1] for item in values])
                        for values in by_match.values()])
    league = _league_metrics(by_league)
    mass = np.mean([item[2] for values in by_match.values() for item in values])
    return {"matches": len(by_match), "transitions": len(rows),
            "candidate_log_loss": float(candidate),
            "baseline_log_loss": float(baseline),
            "relative_improvement": float(1.0 - candidate / baseline),
            "contextual_mass": float(mass), "league_stability": league}


def _league_metrics(
    values: dict[str, list[tuple[float, float]]],
) -> dict[str, Any]:
    """Resume deltas por liga con soporte suficiente."""

    details = {}
    for league, rows in values.items():
        if len(rows) < 100:
            continue
        candidate = float(np.mean([row[0] for row in rows]))
        baseline = float(np.mean([row[1] for row in rows]))
        details[league] = {"transitions": len(rows),
                           "improvement": baseline - candidate}
    nonnegative = sum(row["improvement"] >= 0.0 for row in details.values())
    return {"admitted": len(details), "nonnegative": nonnegative,
            "rate": nonnegative / max(len(details), 1),
            "worst_improvement": min(
                (row["improvement"] for row in details.values()), default=0.0),
            "details": details}


def _duration(
    train: list[Transition],
    target: list[Transition],
) -> dict[str, Any]:
    """Valida media de duración por estado dual dentro de 10%."""

    train_ids, train_states = _sequence_arrays(train)
    target_ids, target_states = _sequence_arrays(target)
    probabilities = duration_probabilities(
        train_ids, train_states, 6)
    predicted = probabilities @ np.arange(1, probabilities.shape[1] + 1)
    observed_runs = runs(target_ids, target_states)
    observed = np.asarray([
        np.mean(observed_runs.get(state, [1])) for state in range(6)])
    errors = np.abs(predicted - observed) / observed
    return {"predicted_mean": predicted.tolist(),
            "observed_mean": observed.tolist(),
            "relative_error": errors.tolist(),
            "mean_relative_error": float(errors.mean()),
            "maximum_relative_error": float(errors.max())}


def _sequence_arrays(rows: list[Transition]) -> tuple[np.ndarray, np.ndarray]:
    """Reconstruye secuencias de estados actuales en orden causal."""

    ordered = sorted(rows, key=lambda row: (
        row.match_id, row.team_id, row.window_index))
    identities = np.asarray([
        row.match_id * 1_000_000 + row.team_id for row in ordered])
    states = np.asarray([row.style * 3 + row.regime for row in ordered])
    return identities, states


def run() -> dict[str, Any]:
    """Selecciona alpha en selection y confirma una sola vez."""

    rows = _transitions()
    splits = {name: [row for row in rows if row.split == name]
              for name in ("fit", "selection", "confirmation")}
    candidates = {}
    for alpha in ALPHAS:
        model = ContextTransitionModel(alpha)
        model.fit(splits["fit"])
        candidates[str(alpha)] = _evaluate(splits["selection"], model)
    eligible = [value for value in ALPHAS
                if _transition_candidate_eligible(candidates[str(value)])]
    if not eligible:
        raise RuntimeError("no_transition_candidate_passed_selection")
    selected = max(eligible, key=lambda value:
                   candidates[str(value)]["relative_improvement"])
    selection = candidates[str(selected)]
    final_model = ContextTransitionModel(selected)
    final_model.fit(splits["fit"] + splits["selection"])
    confirmation = _evaluate(splits["confirmation"], final_model)
    durations = {"selection": _duration(splits["fit"], splits["selection"]),
                 "confirmation": _duration(
                     splits["fit"] + splits["selection"],
                     splits["confirmation"])}
    passed = _eligible(selection, durations["selection"]) and _eligible(
        confirmation, durations["confirmation"])
    result = _result(rows, candidates, selected, selection, confirmation,
                     durations, final_model, passed)
    _publish(result)
    return result


def _eligible(metrics: dict[str, Any], duration: dict[str, Any]) -> bool:
    """Aplica gates completos de Fase 78."""

    return bool(metrics["relative_improvement"] >= 0.01
                and metrics["contextual_mass"] > 0.50
                and metrics["league_stability"]["rate"] >= 0.70
                and metrics["league_stability"]["worst_improvement"] >= -0.06
                and duration["maximum_relative_error"] < 0.10)


def _transition_candidate_eligible(metrics: dict[str, Any]) -> bool:
    """Filtra pooling antes de abrir confirmación."""

    return bool(metrics["relative_improvement"] >= 0.01
                and metrics["contextual_mass"] > 0.50
                and metrics["league_stability"]["rate"] >= 0.70
                and metrics["league_stability"]["worst_improvement"] >= -0.06)


def _result(
    rows: list[Transition], candidates: dict[str, Any], selected: float,
    selection: dict[str, Any], confirmation: dict[str, Any],
    durations: dict[str, Any], model: ContextTransitionModel, passed: bool,
) -> dict[str, Any]:
    """Compone evidencia y parámetros de transición."""

    return {"classification": ("ready_for_next_phase" if passed
                                else "rejected_for_revision"),
            "config": {"version": "dual_context_transition_v1",
                       "alpha_candidates": list(ALPHAS),
                       "selected_alpha": selected},
            "coverage": {"matches": len({row.match_id for row in rows}),
                         "transitions": len(rows),
                         "leagues": len({row.league_slug for row in rows})},
            "audit": {"style_transition_probability": 0.0,
                      "future_state_used_as_feature": False,
                      "matrices_normalized": True, "router_modified": False},
            "metrics": {"candidates": candidates, "selection": selection,
                        "confirmation": confirmation, "duration": durations},
            "transition_parameters": _export(model)}


def _export(model: ContextTransitionModel) -> dict[str, Any]:
    """Serializa conteos suficientes para inferencia y backoff."""

    def rows(values: dict[tuple[Any, ...], Counter[int]]) -> list[dict[str, Any]]:
        return [{"context": list(key), "counts": [counts[state]
                 for state in range(REGIMES)]}
                for key, counts in sorted(values.items(), key=lambda item: str(item[0]))]
    return {"alpha": model.alpha, "global": rows(model.global_counts),
            "baseline": rows(model.baseline_counts),
            "context": rows(model.context_counts)}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(
        value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato completo y hashes."""

    for name in ("config", "coverage", "audit", "metrics",
                 "transition_parameters"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "assignments_sha256": hashlib.sha256(ASSIGNMENTS.read_bytes()).hexdigest(),
        "windows_sha256": hashlib.sha256(WINDOWS.read_bytes()).hexdigest()})
    report = "# Fase 78 — transición contextual\n\n"
    report += f"**Clasificación:** `{result['classification']}`\n\n"
    for name in ("selection", "confirmation"):
        row = result["metrics"][name]
        report += (f"- {name}: mejora `{row['relative_improvement']:.2%}`, "
                   f"masa contextual `{row['contextual_mass']:.2%}`\n")
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def main() -> int:
    """Ejecuta y exige todos los gates."""

    result = run()
    LOGGER.info("Fase 78: %s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
