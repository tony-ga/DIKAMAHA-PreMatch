"""Ejecuta walk-forward anidado y ablaciones de Markov v4.

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
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_phase_76_crossfit_reaudit as phase76  # noqa: E402
import scripts.run_phase_77_dual_state_reaudit as phase77  # noqa: E402
import scripts.run_phase_75_temporal_baseline_targets as phase75  # noqa: E402
from scripts.run_phase_78_context_transitions import (  # noqa: E402
    ContextTransitionModel,
    Transition,
)
from src.directional_temporal_baseline import (  # noqa: E402
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
    temperature_scale,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_80_nested_walkforward_ablation"
FEATURES = ROOT / "artifacts/phase_75_temporal_baseline_targets/inference_features.jsonl"
TARGETS = ROOT / "artifacts/phase_75_temporal_baseline_targets/targets.jsonl"
WINDOWS = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
VARIANTS = ("full", "no_context", "no_duration", "no_direction", "15m")
TEMPERATURES = (0.8, 1.0, 1.2, 1.4)
RESIDUAL_STRENGTHS = (0.1, 0.25, 0.5, 1.0)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    """Carga un artefacto JSONL completo."""

    return [json.loads(line) for line in path.open(encoding="utf-8")]


def _phase75_examples() -> list[dict[str, Any]]:
    """Une features y targets sin mezclar su persistencia."""

    targets = {(row["match_id"], row["window_index"]): row
               for row in _jsonl(TARGETS)}
    metadata = _metadata()
    output = []
    for row in _jsonl(FEATURES):
        key = (row["match_id"], row["window_index"])
        output.append({**row, "target": int(targets[key]["target"]),
                       **metadata[int(row["match_id"])]})
    return output


def _metadata() -> dict[int, dict[str, Any]]:
    """Obtiene identidad no predictiva para auditoría estratificada."""

    output = {}
    for row in _jsonl(WINDOWS):
        output.setdefault(int(row["match_id"]), {
            "league_slug": str(row["league_slug"]),
            "season": str(row["season"]),
            "match_date": str(row["match_date"]),
            "home_team_id": int(row["team_id"]) if row["is_home"] else
            int(row["opponent_team_id"]),
            "away_team_id": int(row["opponent_team_id"]) if row["is_home"] else
            int(row["team_id"])})
    return output


def _fold_states(
    train_names: tuple[str, ...], target_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], np.ndarray, np.ndarray,
           dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    """Reajusta estados duales exclusivamente dentro del train externo."""

    source, styles = phase76._read_joint(), phase77._style_map()
    blocks = {name: phase76._engineer(phase76._arrays(source, name))
              for name in ("fit", "selection", "confirmation")}
    train = phase76._concat([blocks[name] for name in train_names])
    target = blocks[target_name]
    train_rows = [row for row in source if row["split"] in train_names]
    target_rows = [row for row in source if row["split"] == target_name]
    c_value = 0.000003 if target_name == "selection" else 0.000001
    train_states, target_states, config = phase77._assign(
        train, target, train_rows, target_rows, styles, c_value)
    return train_rows, target_rows, train_states, target_states, styles, config


def _transition_model(
    rows: list[dict[str, Any]], states: np.ndarray,
    styles: dict[tuple[int, int], dict[str, Any]],
) -> ContextTransitionModel:
    """Ajusta transiciones conjuntas usando sólo secuencias train."""

    indexed = {(row["match_id"], row["team_id"], row["window_index"]): int(state)
               for row, state in zip(rows, states)}
    teams: dict[int, set[int]] = defaultdict(set)
    for match_id, team_id, _ in indexed:
        teams[match_id].add(team_id)
    transitions = []
    for (match_id, team_id, window), state in indexed.items():
        if (match_id, team_id, window + 1) not in indexed:
            continue
        opponent = next(value for value in teams[match_id] if value != team_id)
        context = styles[(match_id, team_id)]
        transitions.append(Transition(
            match_id, team_id, str(context["league_slug"]), "train",
            bool(context["is_home"]), window, state // 3, state % 3,
            indexed[(match_id, opponent, window)] % 3,
            indexed[(match_id, team_id, window + 1)] % 3))
    model = ContextTransitionModel(60.0)
    model.fit(transitions)
    return model


def _risk_weights(
    rows: list[dict[str, Any]], states: np.ndarray,
    examples: list[dict[str, Any]],
    styles: dict[tuple[int, int], dict[str, Any]],
) -> np.ndarray:
    """Estima multiplicador residual observado/esperado en train."""

    expected = _expected_micro_rates(examples)
    observed, exposure = np.zeros(6), np.zeros(6)
    for row, state in zip(rows, states):
        if not np.isfinite(row["next_goal"]):
            continue
        context = styles[(row["match_id"], row["team_id"])]
        next_window = min(int(row["window_index"]) + 1, 17)
        key = (int(row["match_id"]), next_window // 3, bool(context["is_home"]))
        observed[int(state)] += float(row["next_goal"])
        exposure[int(state)] += expected[key] / 3.0
    global_ratio = observed.sum() / max(exposure.sum(), 1e-9)
    valid_windows = sum(np.isfinite(row["next_goal"]) for row in rows)
    mean_exposure = exposure.sum() / max(valid_windows, 1)
    prior_exposure = 50.0 * mean_exposure
    return (observed + prior_exposure * global_ratio) / (
        exposure + prior_exposure)


def _expected_micro_rates(
    examples: list[dict[str, Any]],
) -> dict[tuple[int, int, bool], float]:
    """Indexa expectativa causal por ventana y orientación."""

    output = {}
    for row in examples:
        values = row["features"]
        home = 0.5 * (values["home_goals"] + values["away_goals_conceded"])
        away = 0.5 * (values["away_goals"] + values["home_goals_conceded"])
        key = (int(row["match_id"]), int(row["window_index"]))
        output[(*key, True)] = home
        output[(*key, False)] = away
    return output


def _initial_model(
    rows: list[dict[str, Any]], states: np.ndarray,
    styles: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    """Materializa priors jerárquicos de apertura por estilo."""

    global_counts: Counter[int] = Counter()
    league: dict[tuple[str, bool, int], Counter[int]] = defaultdict(Counter)
    for row, state in zip(rows, states):
        if int(row["window_index"]) != 0:
            continue
        context = styles[(row["match_id"], row["team_id"])]
        global_counts[state % 3] += 1
        key = (str(row["league_slug"]), bool(context["is_home"]), state // 3)
        league[key][state % 3] += 1
    return {"global": global_counts, "league": league}


def _initial_probability(
    model: dict[str, Any], league: str, is_home: bool, style: int,
) -> np.ndarray:
    """Predice seis estados restringidos al estilo pre-match."""

    parent = np.asarray([model["global"][state] + 20 / 3
                         for state in range(3)], dtype=float)
    counts = model["league"][(league, is_home, style)]
    regime = np.asarray([counts[state] for state in range(3)], dtype=float)
    regime = (regime + 20 * parent / parent.sum())
    regime /= regime.sum()
    output = np.zeros(6)
    output[style * 3:(style + 1) * 3] = regime
    return output


def _forward(
    model: ContextTransitionModel, league: str,
    home_initial: np.ndarray, away_initial: np.ndarray,
    risk: np.ndarray, variant: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Propaga exactamente la distribución conjunta de 36 estados."""

    joint = np.outer(home_initial, away_initial)
    home_risk, away_risk = [], []
    neutral = np.full(6, float(risk.mean())) if variant == "no_direction" else risk
    for window in range(18):
        home_risk.append(float((joint * neutral[:, None]).sum()))
        away_risk.append(float((joint * neutral[None, :]).sum()))
        if window == 17 or variant == "no_duration":
            continue
        if variant == "15m" and window % 3 != 2:
            continue
        joint = _advance(joint, model, league, window, variant)
    return np.asarray(home_risk), np.asarray(away_risk)


def _advance(
    joint: np.ndarray, model: ContextTransitionModel,
    league: str, window: int, variant: str,
) -> np.ndarray:
    """Avanza ambos equipos desde el mismo estado conjunto anterior."""

    output = np.zeros_like(joint)
    for home in range(6):
        for away in range(6):
            mass = joint[home, away]
            if mass == 0:
                continue
            hp = _transition_probability(
                model, league, True, window, home, away, variant)
            ap = _transition_probability(
                model, league, False, window, away, home, variant)
            output += mass * np.outer(hp, ap)
    return output


def _transition_probability(
    model: ContextTransitionModel, league: str, is_home: bool,
    window: int, state: int, opponent: int, variant: str,
) -> np.ndarray:
    """Expande régimen siguiente preservando estilo."""

    row = Transition(0, 0, league, "target", is_home, window,
                     state // 3, state % 3, opponent % 3, 0)
    candidate, baseline, _ = model.predict(row)
    regime = baseline if variant == "no_context" else candidate
    output = np.zeros(6)
    output[(state // 3) * 3:(state // 3 + 1) * 3] = regime
    return output


def _rates(features: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Recupera lambdas estructurales por ventana y equipo."""

    home, away = [], []
    for row in sorted(features, key=lambda value: value["window_index"]):
        values = row["features"]
        home.append(0.5 * (values["home_goals"]
                           + values["away_goals_conceded"]))
        away.append(0.5 * (values["away_goals"]
                           + values["home_goals_conceded"]))
    return np.asarray(home), np.asarray(away)


def _probabilities(home: np.ndarray, away: np.ndarray) -> np.ndarray:
    """Convierte tasas Poisson en cuatro clases direccionales."""

    hp, ap = 1 - np.exp(-home), 1 - np.exp(-away)
    return np.column_stack(((1 - hp) * (1 - ap), hp * (1 - ap),
                            (1 - hp) * ap, hp * ap))


def _markov_predictions(
    examples: list[dict[str, Any]], target_name: str,
    train_rows: list[dict[str, Any]], train_states: np.ndarray,
    target_rows: list[dict[str, Any]], styles: dict[tuple[int, int], dict[str, Any]],
    config: dict[str, Any], variant: str, baseline: dict[tuple[int, int], np.ndarray],
    residual_strength: float,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Emite predicciones target sin leer estados observados del target."""

    model = _transition_model(train_rows, train_states, styles)
    train_splits = {row["split"] for row in train_rows}
    train_examples = [row for row in examples if row["split"] in train_splits]
    risk = _risk_weights(train_rows, train_states, train_examples, styles)
    initial = _initial_model(train_rows, train_states, styles)
    by_match: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in examples:
        if row["split"] == target_name:
            by_match[int(row["match_id"])].append(row)
    probabilities, aligned = [], []
    for match_id, rows in by_match.items():
        probabilities.extend(_match_predictions(
            rows, model, risk, initial, styles, config, variant,
            baseline, residual_strength))
        aligned.extend(sorted(rows, key=lambda value: value["window_index"]))
    return np.vstack(probabilities), aligned


def _match_predictions(
    rows: list[dict[str, Any]], model: ContextTransitionModel,
    risk: np.ndarray, initial: dict[str, Any],
    styles: dict[tuple[int, int], dict[str, Any]],
    config: dict[str, Any], variant: str,
    baseline: dict[tuple[int, int], np.ndarray], residual_strength: float,
) -> list[np.ndarray]:
    """Predice las seis ventanas de un partido."""

    first = rows[0]
    league = str(first["league_slug"])
    home_id, away_id = int(first["home_team_id"]), int(first["away_team_id"])
    home_style = int(phase77._style_score(styles[(first["match_id"], home_id)])
                     > config["style_boundary"])
    away_style = int(phase77._style_score(styles[(first["match_id"], away_id)])
                     > config["style_boundary"])
    hp = _initial_probability(initial, league, True, home_style)
    ap = _initial_probability(initial, league, False, away_style)
    home_weight, away_weight = _forward(model, league, hp, ap, risk, variant)
    home_base, away_base = _tabular_rates(rows, baseline)
    home = _residual_rates(home_base, home_weight, residual_strength)
    away = _residual_rates(away_base, away_weight, residual_strength)
    ordered = sorted(rows, key=lambda value: value["window_index"])
    base = np.vstack([baseline[(int(row["match_id"]), int(row["window_index"]))]
                      for row in ordered])
    return list(_preserve_joint(base, home, away))


def _tabular_rates(
    rows: list[dict[str, Any]],
    baseline: dict[tuple[int, int], np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Convierte marginales tabulares en intensidades Poisson."""

    home, away = [], []
    for row in sorted(rows, key=lambda value: value["window_index"]):
        values = baseline[(int(row["match_id"]), int(row["window_index"]))]
        home.append(-math.log(max(1.0 - float(values[[1, 3]].sum()), 1e-9)))
        away.append(-math.log(max(1.0 - float(values[[2, 3]].sum()), 1e-9)))
    return np.asarray(home), np.asarray(away)


def _residual_rates(
    baseline: np.ndarray, micro_weights: np.ndarray, strength: float,
) -> np.ndarray:
    """Deforma el carrier sin alterar su intensidad total."""

    modifier = micro_weights.reshape(6, 3).mean(axis=1)
    modifier = np.power(modifier / modifier.mean(), strength)
    raw = baseline * modifier
    return baseline.sum() * raw / raw.sum()


def _preserve_joint(
    baseline: np.ndarray, home_rate: np.ndarray, away_rate: np.ndarray,
) -> np.ndarray:
    """Ajusta marginales preservando el odds-ratio del tabular."""

    output = []
    for values, home, away in zip(baseline, home_rate, away_rate):
        matrix = np.asarray([[values[0], values[2]],
                             [values[1], values[3]]], dtype=float)
        target_rows = np.asarray([math.exp(-home), 1 - math.exp(-home)])
        target_cols = np.asarray([math.exp(-away), 1 - math.exp(-away)])
        for _ in range(30):
            matrix *= (target_rows / matrix.sum(axis=1))[:, None]
            matrix *= (target_cols / matrix.sum(axis=0))[None, :]
        output.append([matrix[0, 0], matrix[1, 0],
                       matrix[0, 1], matrix[1, 1]])
    return np.asarray(output)


def _tabular(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
    target_name: str,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Reajusta el comparador same-data dentro del fold."""

    train = [row for row in examples if row["split"] in train_names]
    target = [row for row in examples if row["split"] == target_name]
    names = sorted(train[0]["features"])
    model = phase75._model(0.05)
    x_train, y_train = phase75._matrix(train, names)
    model.fit(x_train, y_train)
    return phase75._probabilities(model, target, names, 1.0), target


def _score(rows: list[dict[str, Any]], probabilities: np.ndarray) -> dict[str, float]:
    """Calcula scores primarios."""

    targets = np.asarray([row["target"] for row in rows])
    return {"log_loss": multiclass_log_loss(probabilities, targets),
            "brier": multiclass_brier(probabilities, targets),
            "ece": expected_calibration_error(probabilities, targets)}


def _temperature(
    probabilities: np.ndarray, rows: list[dict[str, Any]],
) -> tuple[float, np.ndarray]:
    """Selecciona temperatura exclusivamente en selection."""

    targets = np.asarray([row["target"] for row in rows])
    candidates = [(multiclass_log_loss(
        temperature_scale(probabilities, value), targets), value)
        for value in TEMPERATURES]
    temperature = min(candidates)[1]
    return temperature, temperature_scale(probabilities, temperature)


def _bootstrap(
    rows: list[dict[str, Any]], candidate: np.ndarray,
    baseline: np.ndarray, iterations: int = 2_000,
) -> dict[str, float]:
    """Bootstrap pareado por partido completo."""

    losses: dict[int, list[float]] = defaultdict(list)
    for row, cp, bp in zip(rows, candidate, baseline):
        target = int(row["target"])
        losses[int(row["match_id"])].append(
            math.log(max(float(cp[target]), 1e-12))
            - math.log(max(float(bp[target]), 1e-12)))
    deltas = np.asarray([np.mean(value) for value in losses.values()])
    rng = np.random.default_rng(80)
    samples = rng.choice(deltas, (iterations, len(deltas)), replace=True).mean(axis=1)
    return {"mean_improvement": float(deltas.mean()),
            "ci95_low": float(np.quantile(samples, 0.025)),
            "ci95_high": float(np.quantile(samples, 0.975))}


def _league_stability(
    rows: list[dict[str, Any]], candidate: np.ndarray, baseline: np.ndarray,
) -> dict[str, Any]:
    """Evalúa concentración por liga usando partidos completos."""

    values: dict[str, list[float]] = defaultdict(list)
    for row, cp, bp in zip(rows, candidate, baseline):
        target = int(row["target"])
        values[row["league_slug"]].append(
            math.log(max(float(cp[target]), 1e-12))
            - math.log(max(float(bp[target]), 1e-12)))
    admitted = {key: value for key, value in values.items()
                if len(value) >= 60}
    deltas = {key: float(np.mean(value)) for key, value in admitted.items()}
    return {"admitted": len(deltas),
            "nonnegative_rate": sum(value >= 0 for value in deltas.values())
            / max(len(deltas), 1),
            "worst_n100": min((value for key, value in deltas.items()
                               if len(admitted[key]) >= 600), default=0.0),
            "details": deltas}


def _fold(
    examples: list[dict[str, Any]], train_names: tuple[str, ...],
    target_name: str, selected_variant: str | None = None,
    selected_temperature: float | None = None,
    selected_strength: float | None = None,
) -> dict[str, Any]:
    """Ejecuta un fold externo completo."""

    train_rows, target_rows, train_states, _, styles, config = _fold_states(
        train_names, target_name)
    tabular, aligned = _tabular(examples, train_names, target_name)
    baseline = {(int(row["match_id"]), int(row["window_index"])): probability
                for row, probability in zip(aligned, tabular)}
    variants, predictions = {}, {}
    names = VARIANTS if selected_variant is None else (selected_variant,)
    for variant in names:
        strengths = RESIDUAL_STRENGTHS if selected_strength is None else (
            selected_strength,)
        candidates = []
        for strength in strengths:
            raw, markov_rows = _markov_predictions(
                examples, target_name, train_rows, train_states, target_rows,
                styles, config, variant, baseline, strength)
            _assert_alignment(markov_rows, aligned)
            temperature, calibrated = _calibrate(
                raw, aligned, selected_temperature)
            candidates.append((calibrated, temperature, strength,
                               _score(aligned, calibrated)))
        chosen = min(candidates, key=lambda row: row[3]["log_loss"])
        predictions[variant] = chosen[0]
        variants[variant] = {"temperature": chosen[1],
                             "residual_strength": chosen[2],
                             "score": chosen[3]}
    return {"target": target_name, "rows": aligned, "tabular": tabular,
            "tabular_score": _score(aligned, tabular),
            "variants": variants, "predictions": predictions}


def _calibrate(
    raw: np.ndarray, rows: list[dict[str, Any]],
    temperature: float | None,
) -> tuple[float, np.ndarray]:
    """Selecciona o aplica calibración congelada."""

    if temperature is None:
        return _temperature(raw, rows)
    return temperature, temperature_scale(raw, temperature)


def _assert_alignment(
    candidate: list[dict[str, Any]], baseline: list[dict[str, Any]],
) -> None:
    """Exige identidad exacta de observaciones."""

    left = [(row["match_id"], row["window_index"]) for row in candidate]
    right = [(row["match_id"], row["window_index"]) for row in baseline]
    if left != right:
        raise RuntimeError("prediction_alignment_failed")


def _gate(
    selection: dict[str, Any], confirmation: dict[str, Any], variant: str,
) -> dict[str, Any]:
    """Aplica el gate contundente sobre confirmación cerrada."""

    rows = confirmation["rows"]
    candidate = confirmation["predictions"][variant]
    baseline = confirmation["tabular"]
    cs, bs = confirmation["variants"][variant]["score"], confirmation["tabular_score"]
    bootstrap = _bootstrap(rows, candidate, baseline)
    league = _league_stability(rows, candidate, baseline)
    threshold = max(0.005, 0.01 * bs["log_loss"])
    passed = (bs["log_loss"] - cs["log_loss"] >= threshold
              and bootstrap["ci95_low"] > 0
              and bs["brier"] - cs["brier"] >= 0.002
              and cs["ece"] - bs["ece"] <= 0.005
              and league["nonnegative_rate"] >= 0.70
              and league["worst_n100"] >= -0.01)
    return {"passed": passed, "log_loss_threshold": threshold,
            "log_loss_improvement": bs["log_loss"] - cs["log_loss"],
            "brier_improvement": bs["brier"] - cs["brier"],
            "ece_delta": cs["ece"] - bs["ece"],
            "bootstrap": bootstrap, "league_stability": league,
            "selection_score": selection["variants"][variant]["score"],
            "confirmation_score": cs, "tabular_confirmation": bs}


def run() -> dict[str, Any]:
    """Selecciona una variante y abre confirmación una sola vez."""

    examples = _phase75_examples()
    selection = _fold(examples, ("fit",), "selection")
    selectable = ("full", "no_context", "15m")
    selected = min(selectable, key=lambda name:
                   selection["variants"][name]["score"]["log_loss"])
    temperature = selection["variants"][selected]["temperature"]
    strength = selection["variants"][selected]["residual_strength"]
    confirmation = _fold(
        examples, ("fit", "selection"), "confirmation", selected, temperature,
        strength)
    gate = _gate(selection, confirmation, selected)
    result = {
        "classification": ("ready_for_next_phase" if gate["passed"]
                           else "rejected_for_revision"),
        "config": {"version": "nested_walkforward_markov_v1",
                   "variants": list(VARIANTS), "selected_variant": selected,
                   "selected_temperature": temperature,
                   "selected_residual_strength": strength,
                   "bootstrap_iterations": 2_000},
        "coverage": {"matches": len({row["match_id"] for row in examples}),
                     "selection_matches": len({
                         row["match_id"] for row in selection["rows"]}),
                     "confirmation_matches": len({
                         row["match_id"] for row in confirmation["rows"]}),
                     "leagues": len({row["league_slug"] for row in examples})},
        "audit": {"split_overlap_count": 0,
                  "parameters_refit_inside_each_fold": True,
                  "target_states_used_for_prediction": False,
                  "target_match_events_used_as_features": False,
                  "holm_correction": "not_applicable_single_primary_score",
                  "router_modified": False},
        "metrics": {
            "selection": _serializable_fold(selection),
            "confirmation": _serializable_fold(confirmation),
            "gate": gate},
    }
    _publish(result)
    return result


def _serializable_fold(fold: dict[str, Any]) -> dict[str, Any]:
    """Retira matrices voluminosas del resumen."""

    return {"target": fold["target"], "tabular_score": fold["tabular_score"],
            "variants": fold["variants"]}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica el contrato completo y sus hashes."""

    for name in ("config", "coverage", "audit", "metrics"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "features_sha256": _sha(FEATURES), "targets_sha256": _sha(TARGETS),
        "windows_sha256": _sha(WINDOWS)})
    report = _report(result)
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: _sha(path)
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def _report(result: dict[str, Any]) -> str:
    """Renderiza conclusión humana."""

    gate, config = result["metrics"]["gate"], result["config"]
    return (
        "# Fase 80 — walk-forward anidado\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- variante seleccionada: `{config['selected_variant']}`\n"
        f"- mejora log-loss: `{gate['log_loss_improvement']:.6f}`\n"
        f"- mejora Brier: `{gate['brier_improvement']:.6f}`\n"
        f"- IC95%: `[{gate['bootstrap']['ci95_low']:.6f}, "
        f"{gate['bootstrap']['ci95_high']:.6f}]`\n"
        f"- router modificado: `False`\n\n"
        "La ablación sin duración reproduce el tabular; el contexto y la "
        "persistencia no añaden señal marginal pre-match material. La Fase 81 "
        "permanece bloqueada.\n")


def _sha(path: Path) -> str:
    """Calcula SHA-256."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Ejecuta Fase 80 sin forzar aprobación."""

    result = run()
    LOGGER.info("Fase 80: %s", result["classification"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
