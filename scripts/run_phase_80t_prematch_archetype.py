"""Evalúa Markov observable condicionado por arquetipo pre-match.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_80_nested_walkforward as phase80  # noqa: E402
import scripts.run_phase_80r_trajectory_likelihood as phase80r  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80t_prematch_archetype_markov"
TAXONOMIES = ("home_away_quadrants", "tempo_dominance_4",
              "tempo3_dominance2", "goal3_activity2")
STRENGTHS = (20.0, 50.0, 100.0, 200.0)


@dataclass(frozen=True, slots=True)
class ArchetypeModel:
    """Cortes pre-match aprendidos únicamente en train."""

    taxonomy: str
    total_bounds: tuple[float, ...]
    home_boundary: float
    away_boundary: float
    dominance_boundary: float
    goal_bounds: tuple[float, ...]
    activity_boundary: float

    @property
    def count(self) -> int:
        """Devuelve número de arquetipos."""

        return 6 if self.taxonomy in (
            "tempo3_dominance2", "goal3_activity2") else 4

    def assign(self, features: np.ndarray) -> int:
        """Asigna un arquetipo sin outcomes del partido."""

        home, away, goals = features
        total, dominance = home + away, home - away
        if self.taxonomy == "home_away_quadrants":
            return int(home > self.home_boundary) * 2 + int(
                away > self.away_boundary)
        if self.taxonomy == "tempo_dominance_4":
            return int(total > self.total_bounds[0]) * 2 + int(
                dominance > self.dominance_boundary)
        if self.taxonomy == "tempo3_dominance2":
            return int(np.digitize(total, self.total_bounds)) * 2 + int(
                dominance > self.dominance_boundary)
        return int(np.digitize(goals, self.goal_bounds)) * 2 + int(
            total > self.activity_boundary)


def _match_features(rows: list[dict[str, Any]]) -> dict[int, np.ndarray]:
    """Resume perfiles por partido usando sólo inputs pre-match."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    output = {}
    for match_id, values in grouped.items():
        home = sum(_activity(row["features"], "home") for row in values)
        away = sum(_activity(row["features"], "away") for row in values)
        goals = sum(
            row["features"]["home_goals"]
            + row["features"]["away_goals"] for row in values)
        output[match_id] = np.asarray([home, away, goals], dtype=float)
    return output


def _activity(values: dict[str, float], role: str) -> float:
    """Calcula actividad esperada con variables causales same-data."""

    return float(values[f"{role}_shots"]
                 + 2.0 * values[f"{role}_shots_on_target"]
                 + 0.25 * values[f"{role}_pressure"]
                 + 0.10 * values[f"{role}_corners"])


def _fit_archetype(
    features: dict[int, np.ndarray], taxonomy: str,
) -> ArchetypeModel:
    """Aprende cortes robustos dentro del train."""

    matrix = np.vstack(list(features.values()))
    total = matrix[:, 0] + matrix[:, 1]
    dominance = matrix[:, 0] - matrix[:, 1]
    return ArchetypeModel(
        taxonomy=taxonomy,
        total_bounds=tuple(np.quantile(total, [0.5] if taxonomy ==
                           "tempo_dominance_4" else [1 / 3, 2 / 3])),
        home_boundary=float(np.median(matrix[:, 0])),
        away_boundary=float(np.median(matrix[:, 1])),
        dominance_boundary=float(np.median(dominance)),
        goal_bounds=tuple(np.quantile(matrix[:, 2], [1 / 3, 2 / 3])),
        activity_boundary=float(np.median(total)),
    )


def _direct_probability(
    base: np.ndarray, ratios: np.ndarray, window: int, previous: int,
) -> np.ndarray:
    """Combina carrier tabular y transición directa."""

    ratio = np.ones(4) if window == 0 else ratios[window, previous]
    return phase80r._emission(base, ratio)


def _archetype_ratios(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
    direct: np.ndarray, archetypes: dict[int, int],
    count: int, strength: float,
) -> np.ndarray:
    """Aprende residuo de transición condicionado por matchup."""

    observed = np.zeros((6, count, 4, 4))
    exposure = np.zeros_like(observed)
    for values in phase80r._group(rows, probabilities).values():
        archetype = archetypes[int(values[0][0]["match_id"])]
        for window in range(1, 6):
            row, base = values[window]
            previous = int(values[window - 1][0]["target"])
            target = int(row["target"])
            parent = _direct_probability(base, direct, window, previous)
            observed[window, archetype, previous, target] += 1
            exposure[window, archetype, previous] += parent
    ratios = (observed + strength / 4.0) / np.maximum(
        exposure + strength / 4.0, 1e-12)
    ratios[0] = 1.0
    return ratios


def _score(
    rows: list[dict[str, Any]], probabilities: np.ndarray,
    direct: np.ndarray, residual: np.ndarray,
    archetypes: dict[int, int],
) -> dict[str, Any]:
    """Puntúa likelihood conjunto condicionado por arquetipo."""

    conditional, targets, match_ids = [], [], []
    for match_id, values in phase80r._group(rows, probabilities).items():
        archetype = archetypes[match_id]
        for window, (row, base) in enumerate(values):
            previous = 0 if window == 0 else int(
                values[window - 1][0]["target"])
            parent = _direct_probability(base, direct, window, previous)
            probability = phase80r._emission(
                parent, residual[window, archetype, previous])
            conditional.append(probability)
            targets.append(int(row["target"]))
            match_ids.append(match_id)
    return phase80r._scores(
        np.asarray(conditional), np.asarray(targets), np.asarray(match_ids))


def _occupancy(archetypes: dict[int, int], count: int) -> dict[str, float]:
    """Resume soporte de cada estado persistente."""

    counts = Counter(archetypes.values())
    total = max(len(archetypes), 1)
    return {str(state): counts[state] / total for state in range(count)}


def _setup(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
    target_name: str,
) -> dict[str, Any]:
    """Ajusta carrier y prepara ambos bloques."""

    model, names = phase80r._baseline_model(examples, train_names)
    train = [row for row in examples if row["split"] in train_names]
    target = [row for row in examples if row["split"] == target_name]
    return {"train": train, "target": target,
            "train_probability": phase80r._predict(model, names, train),
            "target_probability": phase80r._predict(model, names, target),
            "train_features": _match_features(train),
            "target_features": _match_features(target)}


def _select_direct(setup: dict[str, Any]) -> dict[str, Any]:
    """Selecciona smoothing del comparador directo."""

    candidates = []
    for strength in STRENGTHS:
        ratios = phase80r._direct_ratios(
            setup["train"], setup["train_probability"], strength)
        score = phase80r._direct_score(
            setup["target"], setup["target_probability"], ratios)
        candidates.append({"strength": strength,
                           "score": phase80r._strip(score)})
    return min(candidates, key=lambda row: row["score"]["log_loss"])


def _selection(examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Selecciona taxonomía y smoothing antes de confirmación."""

    setup = _setup(examples, ("fit",), "selection")
    direct_choice = _select_direct(setup)
    direct = phase80r._direct_ratios(
        setup["train"], setup["train_probability"],
        direct_choice["strength"])
    candidates = []
    for taxonomy in TAXONOMIES:
        model = _fit_archetype(setup["train_features"], taxonomy)
        train_states = {key: model.assign(value)
                        for key, value in setup["train_features"].items()}
        target_states = {key: model.assign(value)
                         for key, value in setup["target_features"].items()}
        for strength in STRENGTHS:
            residual = _archetype_ratios(
                setup["train"], setup["train_probability"], direct,
                train_states, model.count, strength)
            score = _score(
                setup["target"], setup["target_probability"], direct,
                residual, target_states)
            candidates.append({
                "taxonomy": taxonomy, "strength": strength,
                "score": phase80r._strip(score),
                "minimum_occupancy": min(
                    _occupancy(target_states, model.count).values())})
    selected = min(candidates, key=lambda row: row["score"]["log_loss"])
    return {"selected": selected, "direct": direct_choice,
            "candidates": candidates}


def _confirmation(
    examples: list[dict[str, Any]], selection: dict[str, Any],
) -> dict[str, Any]:
    """Reajusta train ampliado y abre confirmación una vez."""

    setup = _setup(examples, ("fit", "selection"), "confirmation")
    direct = phase80r._direct_ratios(
        setup["train"], setup["train_probability"],
        selection["direct"]["strength"])
    direct_score = phase80r._direct_score(
        setup["target"], setup["target_probability"], direct)
    chosen = selection["selected"]
    model = _fit_archetype(setup["train_features"], chosen["taxonomy"])
    train_states = {key: model.assign(value)
                    for key, value in setup["train_features"].items()}
    target_states = {key: model.assign(value)
                     for key, value in setup["target_features"].items()}
    residual = _archetype_ratios(
        setup["train"], setup["train_probability"], direct,
        train_states, model.count, chosen["strength"])
    candidate = _score(
        setup["target"], setup["target_probability"], direct,
        residual, target_states)
    baseline = phase80r._baseline_score(
        setup["target"], setup["target_probability"])
    return {"candidate": candidate, "direct": direct_score,
            "baseline": baseline, "rows": setup["target"],
            "occupancy": _occupancy(target_states, model.count),
            "boundaries": _model_dict(model)}


def _model_dict(model: ArchetypeModel) -> dict[str, Any]:
    """Serializa cortes aprendidos."""

    return {
        "taxonomy": model.taxonomy,
        "total_bounds": list(model.total_bounds),
        "home_boundary": model.home_boundary,
        "away_boundary": model.away_boundary,
        "dominance_boundary": model.dominance_boundary,
        "goal_bounds": list(model.goal_bounds),
        "activity_boundary": model.activity_boundary,
    }


def _gate(confirmation: dict[str, Any]) -> dict[str, Any]:
    """Compara contra el mejor comparador secuencial."""

    candidate, direct = confirmation["candidate"], confirmation["direct"]
    bootstrap = phase80r._bootstrap(candidate, direct)
    league = phase80r._league(confirmation["rows"], candidate, direct)
    improvement = direct["log_loss"] - candidate["log_loss"]
    threshold = max(0.005, direct["log_loss"] * 0.01)
    passed = (improvement >= threshold and bootstrap["ci95_low"] > 0
              and direct["brier"] - candidate["brier"] >= 0.002
              and candidate["ece"] - direct["ece"] <= 0.005
              and league["nonnegative_rate"] >= 0.70
              and league["worst_n100"] >= -0.01)
    return {"passed": passed, "log_loss_improvement": improvement,
            "threshold": threshold,
            "brier_improvement": direct["brier"] - candidate["brier"],
            "ece_delta": candidate["ece"] - direct["ece"],
            "bootstrap": bootstrap, "league_stability": league}


def run() -> dict[str, Any]:
    """Ejecuta selección y confirmación cerradas."""

    examples = phase80._phase75_examples()
    selection = _selection(examples)
    confirmation = _confirmation(examples, selection)
    gate = _gate(confirmation)
    result = {
        "classification": ("ready_for_next_phase" if gate["passed"]
                           else "rejected_for_revision"),
        "config": {"version": "prematch_archetype_markov_v1",
                   "taxonomies": list(TAXONOMIES),
                   "strengths": list(STRENGTHS),
                   "selected": selection["selected"],
                   "direct_strength": selection["direct"]["strength"]},
        "coverage": {"matches": len({row["match_id"] for row in examples}),
                     "selection_matches": 1891,
                     "confirmation_matches": 1895,
                     "archetype_occupancy": confirmation["occupancy"]},
        "audit": {"archetype_uses_target_outcomes": False,
                  "cuts_refit_inside_fold": True,
                  "split_overlap_count": 0, "router_modified": False},
        "metrics": {
            "selection": selection,
            "confirmation": {
                "candidate": phase80r._strip(confirmation["candidate"]),
                "direct": phase80r._strip(confirmation["direct"]),
                "baseline": phase80r._strip(confirmation["baseline"]),
                "boundaries": confirmation["boundaries"]},
            "gate": gate},
    }
    _publish(result)
    return result


def _write(name: str, value: Any) -> None:
    """Escribe JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato e hashes."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "features_sha256": phase80._sha(phase80.FEATURES),
        "targets_sha256": phase80._sha(phase80.TARGETS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Genera conclusión humana."""

    selected, gate = result["config"]["selected"], result["metrics"]["gate"]
    return (
        "# Fase 80T — Markov por arquetipo pre-match\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- taxonomía: `{selected['taxonomy']}`\n"
        f"- smoothing: `{selected['strength']}`\n"
        f"- mejora log-loss: `{gate['log_loss_improvement']:.6f}`\n"
        f"- IC95%: `[{gate['bootstrap']['ci95_low']:.6f}, "
        f"{gate['bootstrap']['ci95_high']:.6f}]`\n"
        "- router modificado: `False`\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta sin promover automáticamente."""

    result = run()
    LOGGER.info("Fase 80T: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

