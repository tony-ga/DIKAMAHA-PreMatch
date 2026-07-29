"""Evalúa estados conjuntos de ritmo y un residual Markov con abstención.

# Requirements:
#     pip install numpy scikit-learn

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.markov_semantic_v3 import (
    TEMPO_STATES,
    LogisticResidualSolver,
    SemanticConfig,
    SemanticStateLabeler,
    TempoTransitionModel,
    chain_features,
    initial_distribution,
    logit,
    pair_windows,
)

WINDOWS = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_71_state_semantic_revision_v1"
REGULARIZATION = (0.0003, 0.001, 0.003, 0.01, 0.03)
ALPHAS = tuple(index / 10.0 for index in range(11))
LOGGER = logging.getLogger(__name__)


def _candidate_configs() -> tuple[SemanticConfig, ...]:
    """Define sensibilidad semántica antes de consultar el replay."""

    return (
        SemanticConfig(tempo_quantiles=(0.15, 0.50, 0.80), initial_prior_strength=4.0, transition_prior_strength=4.0, recent_matches=5),
        SemanticConfig(),
        SemanticConfig(tempo_quantiles=(0.40, 0.70, 0.90), initial_prior_strength=16.0, transition_prior_strength=32.0, recent_matches=12),
        SemanticConfig(tempo_quantiles=(0.10, 0.40, 0.75), initial_prior_strength=4.0, transition_prior_strength=12.0, recent_matches=3),
    )


def _load(path: Path) -> Any:
    """Carga un artefacto JSON local."""

    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    """Calcula SHA-256 de un archivo de entrada."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(name: str, payload: Any) -> None:
    """Publica JSON mediante reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _ordered_matches(rows: Sequence[dict[str, Any]]) -> list[tuple[str, int]]:
    """Ordena partidos completos por kickoff e identificador."""

    return sorted({(str(row["match_date"]), int(row["match_id"])) for row in rows})


def _group(rows: Sequence[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Agrupa filas por partido completo."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    return grouped


def _opening_priors(
    rows: Sequence[dict[str, Any]], development_ids: set[int],
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Calcula priors state_0 sólo desde desarrollo."""

    global_counts = Counter()
    leagues: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if int(row["match_id"]) in development_ids and int(row["window_index"]) == 0:
            global_counts[str(row["tempo_state"])] += 1
            leagues[str(row["league_slug"])][str(row["tempo_state"])] += 1
    global_prior = _counter_distribution(global_counts, np.full(4, 0.25), 16.0)
    return global_prior, {
        league: _counter_distribution(counts, global_prior, 8.0) for league, counts in leagues.items()
    }


def _counter_distribution(counts: Counter[str], prior: np.ndarray, strength: float) -> np.ndarray:
    """Convierte conteos suavizados en una distribución."""

    values = np.asarray([counts[state] for state in TEMPO_STATES], dtype=float)
    values += strength * prior
    return values / values.sum()


class GoalRateBaseline:
    """Replica el baseline causal de tasas por liga/localía/ventana."""

    def __init__(self) -> None:
        """Inicializa acumuladores vacíos."""

        self.league: dict[tuple[str, bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        self.venue: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])

    def update(self, rows: Sequence[dict[str, Any]]) -> None:
        """Incorpora un partido únicamente después de predecirlo."""

        for row in rows:
            key = (str(row["league_slug"]), bool(row["is_home"]), int(row["window_index"]))
            self.league[key][0] += float(row["goals"])
            self.league[key][1] += 1.0
            venue_key = (key[1], key[2])
            self.venue[venue_key][0] += float(row["goals"])
            self.venue[venue_key][1] += 1.0

    def probability(self, league: str) -> float:
        """Predice gol en primera mitad con tres hazards causales."""

        rate = sum(self._rate((league, venue, window)) for venue in (True, False) for window in range(3))
        return 1.0 - math.exp(-rate)

    def _rate(self, key: tuple[str, bool, int]) -> float:
        """Obtiene la tasa suavizada con backoff por localía."""

        values = self.league[key]
        parent = self.venue[(key[1], key[2])]
        base = (parent[0] + 2.0) / (parent[1] + 2.0) if parent[1] else 0.10
        return (values[0] + 2.0 * base) / (values[1] + 2.0)


def _seed_histories(
    semantic: Sequence[dict[str, Any]], development_ids: set[int],
) -> dict[int, list[str]]:
    """Carga aperturas históricas anteriores al replay."""

    histories: dict[int, list[str]] = defaultdict(list)
    for row in semantic:
        if int(row["match_id"]) in development_ids and int(row["window_index"]) == 0:
            state = str(row["tempo_state"])
            histories[int(row["home_team_id"])].append(state)
            histories[int(row["away_team_id"])].append(state)
    return histories


def _state0_losses(probabilities: np.ndarray, prior: np.ndarray, actual: str) -> tuple[float, float]:
    """Calcula pérdidas del estimador y del prior de liga."""

    index = TEMPO_STATES.index(actual)
    return -math.log(max(float(probabilities[index]), 1e-12)), -math.log(max(float(prior[index]), 1e-12))


def _transition_losses(
    sequence: Sequence[dict[str, Any]], model: TempoTransitionModel,
) -> tuple[list[float], list[float]]:
    """Compara pooling de equipos contra prior liga+ventana."""

    candidate, parent = [], []
    teams = (int(sequence[0]["home_team_id"]), int(sequence[0]["away_team_id"]))
    for current, following in zip(sequence[:2], sequence[1:3]):
        state = TEMPO_STATES.index(str(current["tempo_state"]))
        target = TEMPO_STATES.index(str(following["tempo_state"]))
        matrix = model.matrix(str(current["league_slug"]), teams, int(current["window_index"]))
        baseline = model.parent_matrix(str(current["league_slug"]), int(current["window_index"]))
        candidate.append(-math.log(max(float(matrix[state, target]), 1e-12)))
        parent.append(-math.log(max(float(baseline[state, target]), 1e-12)))
    return candidate, parent


def _replay(
    raw: Sequence[dict[str, Any]], semantic: Sequence[dict[str, Any]],
    development: list[tuple[str, int]], targets: list[tuple[str, int]],
    model: TempoTransitionModel, config: SemanticConfig,
) -> list[dict[str, Any]]:
    """Materializa features antes del target y actualiza después."""

    raw_grouped, state_grouped = _group(raw), _group(semantic)
    development_ids = {match_id for _, match_id in development}
    global_prior, league_priors = _opening_priors(semantic, development_ids)
    histories = _seed_histories(semantic, development_ids)
    baseline = GoalRateBaseline()
    for _, match_id in development:
        baseline.update(raw_grouped[match_id])
    return _walk_targets(targets, raw_grouped, state_grouped, histories, baseline, model, league_priors, global_prior, config)


def _walk_targets(
    targets: list[tuple[str, int]], raw: dict[int, list[dict[str, Any]]],
    states: dict[int, list[dict[str, Any]]], histories: dict[int, list[str]],
    baseline: GoalRateBaseline, model: TempoTransitionModel,
    priors: dict[str, np.ndarray], global_prior: np.ndarray, config: SemanticConfig,
) -> list[dict[str, Any]]:
    """Ejecuta el orden walk-forward sin fugas entre partidos."""

    output = []
    for _, match_id in targets:
        sequence = sorted(states[match_id], key=lambda row: int(row["window_index"]))
        output.append(_predict_row(sequence, histories, baseline, model, priors, global_prior, config))
        baseline.update(raw[match_id])
        _update_histories(histories, sequence)
    return output


def _predict_row(
    sequence: Sequence[dict[str, Any]], histories: dict[int, list[str]],
    baseline: GoalRateBaseline, model: TempoTransitionModel,
    priors: dict[str, np.ndarray], global_prior: np.ndarray, config: SemanticConfig,
) -> dict[str, Any]:
    """Congela una fila pre-match antes de leer su target."""

    first, league = sequence[0], str(sequence[0]["league_slug"])
    teams = (int(first["home_team_id"]), int(first["away_team_id"]))
    prior = priors.get(league, global_prior)
    initial, support = initial_distribution(histories, prior, teams, config)
    probability = baseline.probability(league)
    state_loss, prior_loss = _state0_losses(initial, prior, str(first["tempo_state"]))
    transition, parent = _transition_losses(sequence, model)
    return _row_payload(sequence, probability, initial, support, state_loss, prior_loss, transition, parent, model)


def _row_payload(
    sequence: Sequence[dict[str, Any]], baseline: float, initial: np.ndarray,
    support: int, state_loss: float, prior_loss: float, transition: list[float],
    parent: list[float], model: TempoTransitionModel,
) -> dict[str, Any]:
    """Serializa features, targets y auditoría de una predicción."""

    first = sequence[0]
    teams = (int(first["home_team_id"]), int(first["away_team_id"]))
    chain = chain_features(initial, model, str(first["league_slug"]), teams)
    actual = sum(float(row["goals"]) for row in sequence[:3]) > 0.0
    return {
        "match_id": int(first["match_id"]), "match_date": str(first["match_date"]),
        "league_slug": str(first["league_slug"]), "baseline_probability": baseline,
        "features": [logit(baseline), *chain, float(support)], "actual_first_half_goal": actual,
        "state0_loss": state_loss, "state0_prior_loss": prior_loss,
        "transition_losses": transition, "transition_parent_losses": parent,
        "target_used_before_prediction": False,
    }


def _update_histories(histories: dict[int, list[str]], sequence: Sequence[dict[str, Any]]) -> None:
    """Añade state_0 observado sólo después de predecir el partido."""

    first, state = sequence[0], str(sequence[0]["tempo_state"])
    histories[int(first["home_team_id"])].append(state)
    histories[int(first["away_team_id"])].append(state)


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier binarios."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _blend(baseline: np.ndarray, candidate: np.ndarray, alpha: float) -> np.ndarray:
    """Fusiona el residual sin permitir salir del intervalo probabilístico."""

    return np.clip((1.0 - alpha) * baseline + alpha * candidate, 1e-9, 1.0 - 1e-9)


def _select(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Selecciona regularización y alpha sin consultar el holdout."""

    split = int(len(rows) * 2 / 3)
    train, validation = rows[:split], rows[split:]
    best = {"loss": float("inf"), "regularization": REGULARIZATION[0], "alpha": 0.0}
    for regularization in REGULARIZATION:
        solver = LogisticResidualSolver(regularization)
        solver.fit([row["features"] for row in train], [int(row["actual_first_half_goal"]) for row in train])
        candidate = solver.predict([row["features"] for row in validation])
        best = _select_alpha(validation, candidate, regularization, best)
    return best


def _select_alpha(
    rows: Sequence[dict[str, Any]], candidate: np.ndarray,
    regularization: float, best: dict[str, float],
) -> dict[str, float]:
    """Busca el peso residual únicamente en validación."""

    baseline = np.asarray([row["baseline_probability"] for row in rows], dtype=float)
    targets = [bool(row["actual_first_half_goal"]) for row in rows]
    for alpha in ALPHAS:
        probabilities = _blend(baseline, candidate, alpha)
        loss = float(np.mean([_loss(value, actual)[0] for value, actual in zip(probabilities, targets)]))
        if loss < best["loss"] - 1e-15:
            best = {"loss": loss, "regularization": regularization, "alpha": alpha}
    return best


def _score(
    rows: Sequence[dict[str, Any]], candidate: np.ndarray, alpha: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evalúa probabilidades y conserva pérdidas por partido."""

    baseline = np.asarray([row["baseline_probability"] for row in rows], dtype=float)
    probabilities = _blend(baseline, candidate, alpha)
    scored = [_scored_row(row, float(value)) for row, value in zip(rows, probabilities)]
    deltas = [row["baseline_log_loss"] - row["candidate_log_loss"] for row in scored]
    metrics = _aggregate_metrics(scored, deltas)
    return metrics, scored


def _scored_row(row: dict[str, Any], probability: float) -> dict[str, Any]:
    """Añade métricas post-match a una predicción congelada."""

    actual = bool(row["actual_first_half_goal"])
    candidate_loss, candidate_brier = _loss(probability, actual)
    baseline_loss, baseline_brier = _loss(float(row["baseline_probability"]), actual)
    return {
        **row, "candidate_probability": probability, "candidate_log_loss": candidate_loss,
        "baseline_log_loss": baseline_loss, "candidate_brier": candidate_brier,
        "baseline_brier": baseline_brier,
    }


def _aggregate_metrics(scored: Sequence[dict[str, Any]], deltas: list[float]) -> dict[str, Any]:
    """Resume métricas principales y estabilidad por liga."""

    return {
        "matches": len(scored),
        "candidate_log_loss": float(np.mean([row["candidate_log_loss"] for row in scored])),
        "baseline_log_loss": float(np.mean([row["baseline_log_loss"] for row in scored])),
        "candidate_brier": float(np.mean([row["candidate_brier"] for row in scored])),
        "baseline_brier": float(np.mean([row["baseline_brier"] for row in scored])),
        "improvement": _bootstrap(deltas),
        "by_league": _by_league(scored),
    }


def _bootstrap(values: list[float]) -> dict[str, Any]:
    """Calcula bootstrap por partido completo."""

    rng = np.random.default_rng(20260727)
    source = np.asarray(values, dtype=float)
    indexes = rng.integers(0, len(source), size=(5000, len(source)))
    means = source[indexes].mean(axis=1)
    interval = np.quantile(means, (0.025, 0.975))
    return {
        "mean": float(source.mean()), "ci_95": [float(interval[0]), float(interval[1])],
        "strictly_positive": bool(interval[0] > 0.0), "samples": len(means),
    }


def _by_league(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Calcula mejora por liga con unidad partido."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(row)
    return {
        league: {
            "matches": len(values),
            "improvement": float(np.mean([
                row["baseline_log_loss"] - row["candidate_log_loss"] for row in values
            ])),
        }
        for league, values in sorted(grouped.items())
    }


def _semantic_signal(
    rows: Sequence[dict[str, Any]], development_ids: set[int],
) -> dict[str, Any]:
    """Mide riesgo de la ventana siguiente por evidencia de ritmo."""

    grouped = _group([row for row in rows if int(row["match_id"]) in development_ids])
    values: dict[str, list[float]] = defaultdict(list)
    for sequence in grouped.values():
        ordered = sorted(sequence, key=lambda row: int(row["window_index"]))
        for current, following in zip(ordered, ordered[1:]):
            values[str(current["tempo_state"])].append(float(following["goals"] > 0.0))
    rates = {state: float(np.mean(values[state])) for state in TEMPO_STATES}
    return {
        "support": {state: len(values[state]) for state in TEMPO_STATES}, "next_goal_rates": rates,
        "spread": max(rates.values()) - min(rates.values()),
        "risk_order_monotonic": all(rates[a] <= rates[b] for a, b in zip(TEMPO_STATES, TEMPO_STATES[1:])),
    }


def _predictability(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Resume si state_0 y transiciones superan sus priors."""

    state_gain = float(np.mean([row["state0_prior_loss"] - row["state0_loss"] for row in rows]))
    transition = [value for row in rows for value in row["transition_losses"]]
    parent = [value for row in rows for value in row["transition_parent_losses"]]
    transition_gain = float(np.mean(np.asarray(parent) - np.asarray(transition)))
    return {
        "state0_logloss_improvement_vs_league": state_gain,
        "transition_logloss_improvement_vs_league": transition_gain,
        "state0_predictive": state_gain > 0.0, "transition_predictive": transition_gain > 0.0,
    }


def _fit_and_evaluate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Selecciona en validación y evalúa una sola vez el holdout."""

    middle = len(rows) // 2
    validation, holdout = rows[:middle], rows[middle:]
    selected = _select(validation)
    solver = LogisticResidualSolver(float(selected["regularization"]))
    solver.fit([row["features"] for row in validation], [int(row["actual_first_half_goal"]) for row in validation])
    candidate = solver.predict([row["features"] for row in holdout])
    metrics, scored = _score(holdout, candidate, float(selected["alpha"]))
    return {"selection": selected, "holdout_metrics": metrics, "holdout_predictions": scored}


def _audit(
    semantic: dict[str, Any], predictability: dict[str, Any],
    evaluation: dict[str, Any], coverage: dict[str, int],
) -> dict[str, Any]:
    """Aplica los tres gates sin promover automáticamente."""

    holdout = evaluation["holdout_metrics"]
    semantic_gate = semantic["risk_order_monotonic"] and min(semantic["support"].values()) >= 500
    predictive_gate = predictability["state0_predictive"] and predictability["transition_predictive"]
    incremental_gate = holdout["improvement"]["strictly_positive"] and holdout["candidate_brier"] < holdout["baseline_brier"]
    passed = semantic_gate and predictive_gate and incremental_gate
    return {
        "classification": "promising_unconfirmed" if passed else "rejected_for_revision",
        "semantic_gate": semantic_gate, "predictability_gate": predictive_gate,
        "incremental_gate": incremental_gate, "development_matches": coverage["development_matches"],
        "replay_matches": coverage["replay_matches"], "target_used_before_prediction": False,
        "walk_forward_order": True, "official_router_modified": False, "markov_promoted": False,
        "fallback_probability_equals_baseline_when_alpha_zero": evaluation["selection"]["alpha"] == 0.0,
    }


def _reports(result: dict[str, Any]) -> None:
    """Publica interpretación y clasificación de la fase."""

    audit, metrics = result["audit"], result["metrics"]["holdout"]
    lines = [
        "# Fase 71 — revisión semántica Markov", "",
        f"**Clasificación:** `{audit['classification']}`", "",
        f"- gates semántica/predictibilidad/incremental: `{audit['semantic_gate']}` / "
        f"`{audit['predictability_gate']}` / `{audit['incremental_gate']}`",
        f"- alpha seleccionado: `{result['config']['selection']['alpha']}`",
        f"- holdout candidato/baseline: `{metrics['candidate_log_loss']}` / `{metrics['baseline_log_loss']}`",
        f"- mejora e IC: `{metrics['improvement']['mean']}` / `{metrics['improvement']['ci_95']}`",
        "- router oficial modificado: `False`", "- Markov promovido: `False`",
    ]
    (OUTPUT / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUTPUT / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(config: SemanticConfig | None = None) -> dict[str, Any]:
    """Ejecuta Fase 71 y genera artefactos reproducibles."""

    raw = _load(WINDOWS)
    ordered, paired = _ordered_matches(raw), pair_windows(raw)
    cut = int(len(ordered) * 0.60)
    development, targets = ordered[:cut], ordered[cut:]
    development_ids = {match_id for _, match_id in development}
    configs = (config,) if config is not None else _candidate_configs()
    candidates = [_candidate(active, raw, paired, development, targets, development_ids) for active in configs]
    chosen = min(candidates, key=_candidate_rank)
    sensitivity = [_candidate_summary(item, development_ids) for item in candidates]
    return _publish(chosen["config"], chosen["labeler"], chosen["semantic"], chosen["replay"], development_ids, sensitivity)


def _candidate(
    active: SemanticConfig, raw: list[dict[str, Any]], paired: list[dict[str, Any]],
    development: list[tuple[str, int]], targets: list[tuple[str, int]],
    development_ids: set[int],
) -> dict[str, Any]:
    """Materializa un candidato usando el mismo corte causal."""

    labeler = SemanticStateLabeler(active).fit([row for row in paired if int(row["match_id"]) in development_ids])
    semantic = labeler.transform(paired)
    transition = TempoTransitionModel(active).fit(semantic, development_ids)
    replay = _replay(raw, semantic, development, targets, transition, active)
    return {
        "config": active, "labeler": labeler, "semantic": semantic, "replay": replay,
        "selection": _select(replay[: len(replay) // 2]),
        "predictability": _predictability(replay[: len(replay) // 2]),
    }


def _candidate_rank(candidate: dict[str, Any]) -> tuple[float, float]:
    """Desempata por predictibilidad sin consultar el holdout."""

    metrics = candidate["predictability"]
    gain = metrics["state0_logloss_improvement_vs_league"] + metrics["transition_logloss_improvement_vs_league"]
    return float(candidate["selection"]["loss"]), -float(gain)


def _candidate_summary(candidate: dict[str, Any], development_ids: set[int]) -> dict[str, Any]:
    """Resume sensibilidad sin leer el holdout para seleccionar."""

    labeler = candidate["labeler"]
    assert labeler.thresholds is not None
    return {
        "config": labeler.thresholds.config,
        "thresholds": {"tempo": labeler.thresholds.tempo, "control_margin": labeler.thresholds.control_margin},
        "selection": candidate["selection"],
        "semantic_metrics": _semantic_signal(candidate["semantic"], development_ids),
        "predictability": candidate["predictability"],
    }


def _publish(
    config: SemanticConfig, labeler: SemanticStateLabeler,
    semantic_rows: list[dict[str, Any]], replay: list[dict[str, Any]],
    development_ids: set[int], sensitivity: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calcula gates y publica el contrato completo."""

    evaluation = _fit_and_evaluate(replay)
    semantic = _semantic_signal(semantic_rows, development_ids)
    predictability = _predictability(replay)
    coverage = {"development_matches": len(development_ids), "replay_matches": len(replay), "semantic_rows": len(semantic_rows)}
    audit = _audit(semantic, predictability, evaluation, coverage)
    result = _result(config, labeler, semantic, predictability, evaluation, coverage, audit)
    result["sensitivity"] = sensitivity
    _write_result(result, semantic_rows)
    return result


def _result(
    config: SemanticConfig, labeler: SemanticStateLabeler, semantic: dict[str, Any],
    predictability: dict[str, Any], evaluation: dict[str, Any],
    coverage: dict[str, int], audit: dict[str, Any],
) -> dict[str, Any]:
    """Construye el resultado serializable de la fase."""

    assert labeler.thresholds is not None
    return {
        "config": {
            **labeler.thresholds.config, "thresholds": {
                "tempo": labeler.thresholds.tempo, "control_margin": labeler.thresholds.control_margin,
            }, "selection": evaluation["selection"],
        },
        "coverage": coverage, "semantic_metrics": semantic, "predictability": predictability,
        "metrics": {"holdout": evaluation["holdout_metrics"]},
        "predictions": evaluation["holdout_predictions"], "audit": audit,
    }


def _write_result(result: dict[str, Any], semantic_rows: list[dict[str, Any]]) -> None:
    """Escribe artefactos, reportes y hashes finales."""

    for name in ("config", "coverage", "semantic_metrics", "predictability", "metrics", "predictions", "audit", "sensitivity"):
        _write(f"{name}.json", result[name])
    _write("semantic_labels.json", semantic_rows)
    _write("input_manifest.json", {"windows": str(WINDOWS.relative_to(ROOT)), "windows_hash": _sha256(WINDOWS)})
    _reports(result)
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"
    }
    _write("hashes.json", hashes)
    LOGGER.info("Fase 71 revisión semántica: %s", result["audit"]["classification"])


def main() -> int:
    """Ejecuta la fase con manejo explícito de errores esperables."""

    try:
        classification = run()["audit"]["classification"]
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        LOGGER.error("Fase 71 falló: %s", error, exc_info=True)
        return 2
    return 0 if classification in {"rejected_for_revision", "promising_unconfirmed"} else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-27
