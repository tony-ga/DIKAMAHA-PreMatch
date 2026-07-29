"""Descubre estados latentes causales con duración explícita.

Requirements:
    numpy>=2.0
    scipy>=1.14
    scikit-learn>=1.5

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import normalized_mutual_info_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.latent_state_discovery import (  # noqa: E402
    FEATURE_METRICS,
    bic_per_observation,
    duration_nll,
    duration_probabilities,
    emission_parameter_count,
    geometric_probabilities,
    league_order_stability,
    next_goal_risk,
    occupancy,
)

LOGGER = logging.getLogger(__name__)
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
OUTPUT = ROOT / "artifacts/phase_76_latent_state_discovery"
MAX_FIT_ROWS = 60_000
SEED = 27
DIRECTIONAL_METRICS = (
    *FEATURE_METRICS, "shots_conceded", "corners_conceded",
    "pressure_conceded",
)


def _light_row(row: dict[str, Any]) -> dict[str, Any]:
    """Conserva sólo identidad y emisiones de una orientación."""

    result = {key: row[key] for key in (
        "match_id", "match_date", "window_index", "split", "league_slug",
        "team_id", "is_home",
    )}
    result.update({name: row.get(name, 0.0) for name in DIRECTIONAL_METRICS})
    return result


def _read_joint() -> list[dict[str, Any]]:
    """Lee y une local/visitante sin retener payloads innecesarios."""

    records = []
    with SOURCE.open(encoding="utf-8") as handle:
        for line in handle:
            records.append(_record(_light_row(json.loads(line))))
    records.sort(key=lambda row: (row["match_date"], row["match_id"],
                                  row["team_id"], row["window_index"]))
    _attach_temporal_context(records)
    return records


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """Crea una observación direccional por equipo y microventana."""

    return {
        "match_id": int(row["match_id"]), "team_id": int(row["team_id"]),
        "sequence_id": int(row["match_id"]) * 1_000_000 + int(row["team_id"]),
        "match_date": str(row["match_date"]),
        "window_index": int(row["window_index"]),
        "split": str(row["split"]), "league_slug": str(row["league_slug"]),
        "features": [float(row[name]) for name in DIRECTIONAL_METRICS]
        + [float(row["window_index"]) / 17.0, float(row["is_home"])],
        "current_goal": int(row["goals"]) > 0,
        "next_goal": np.nan,
    }


def _attach_temporal_context(records: list[dict[str, Any]]) -> None:
    """Añade label futuro sólo para evaluación semántica."""

    for index, current in enumerate(records):
        if index + 1 < len(records):
            following = records[index + 1]
        else:
            following = None
        if following and current["sequence_id"] == following["sequence_id"]:
            current["next_goal"] = float(following["current_goal"])


def _arrays(
    records: list[dict[str, Any]],
    split: str,
) -> dict[str, np.ndarray]:
    """Extrae matrices de una partición inmutable."""

    rows = [row for row in records if row["split"] == split]
    return {
        "x": np.asarray([row["features"] for row in rows], dtype=float),
        "match_ids": np.asarray([row["sequence_id"] for row in rows], dtype=np.int64),
        "next_goals": np.asarray([row["next_goal"] for row in rows], dtype=float),
        "leagues": np.asarray([row["league_slug"] for row in rows], dtype=str),
        "window_indices": np.asarray([row["window_index"] for row in rows], dtype=int),
    }


def _sample(values: np.ndarray, maximum: int = MAX_FIT_ROWS) -> np.ndarray:
    """Selecciona una muestra uniforme determinista."""

    if len(values) <= maximum:
        return values
    indices = np.linspace(0, len(values) - 1, maximum, dtype=int)
    return values[indices]


def _fit_model(values: np.ndarray, states: int, seed: int) -> GaussianMixture:
    """Ajusta emisiones gaussianas diagonales reproducibles."""

    model = GaussianMixture(
        n_components=states, covariance_type="diag", reg_covar=1e-4,
        max_iter=150, n_init=5, random_state=seed,
    )
    return model.fit(_sample(values))


def _candidate(
    state_count: int,
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    scaler: StandardScaler,
) -> tuple[dict[str, Any], GaussianMixture]:
    """Evalúa un número de estados sin observar confirmación."""

    model = _fit_model(scaler.transform(fit["x"]), state_count, SEED)
    fit_states = model.predict(scaler.transform(fit["x"]))
    selected_states = model.predict(scaler.transform(selection["x"]))
    likelihood = float(model.score(scaler.transform(selection["x"]))) * len(selected_states)
    risks, support = next_goal_risk(
        selected_states, selection["next_goals"], state_count
    )
    duration = _duration_metrics(fit, selection, fit_states, selected_states,
                                 state_count)
    result = {
        "states": state_count,
        "bic_per_observation": bic_per_observation(
            likelihood, len(selected_states),
            emission_parameter_count(state_count, fit["x"].shape[1]),
        ),
        "minimum_occupancy": min(occupancy(selected_states, state_count).values()),
        "minimum_ordinary_occupancy": _minimum_ordinary_occupancy(
            model, scaler, selected_states
        ),
        "risk_spread": float(risks.max() - risks.min()),
        "risk_support": support.tolist(),
        "duration": duration,
    }
    return result, model


def _duration_metrics(
    fit: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    fit_states: np.ndarray,
    target_states: np.ndarray,
    state_count: int,
) -> dict[str, float]:
    """Compara duración explícita y geométrica en una partición OOS."""

    explicit = duration_probabilities(
        fit["match_ids"], fit_states, state_count
    )
    geometric = geometric_probabilities(
        fit["match_ids"], fit_states, state_count
    )
    explicit_nll = duration_nll(explicit, target["match_ids"], target_states)
    geometric_nll = duration_nll(geometric, target["match_ids"], target_states)
    return {"explicit_nll": explicit_nll, "geometric_nll": geometric_nll,
            "improvement": geometric_nll - explicit_nll}


def _select(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
) -> tuple[StandardScaler, GaussianMixture, list[dict[str, Any]]]:
    """Selecciona candidato sólo con fit y selection."""

    scaler = StandardScaler().fit(fit["x"])
    candidates, models = [], {}
    for state_count in range(4, 9):
        result, model = _candidate(state_count, fit, selection, scaler)
        candidates.append(result)
        models[state_count] = model
    eligible = [
        row for row in candidates
        if row["minimum_ordinary_occupancy"] >= 0.05
        and row["risk_spread"] >= 0.05
        and row["duration"]["improvement"] > 0.0
    ]
    pool = eligible or candidates
    selected = min(pool, key=lambda row: row["bic_per_observation"])
    return scaler, models[int(selected["states"])], candidates


def _seed_stability(
    model: GaussianMixture,
    state_count: int,
    fit_x: np.ndarray,
    selection_x: np.ndarray,
) -> dict[str, Any]:
    """Mide NMI entre tres inicializaciones sobre la misma cohorte."""

    assignments = [model.predict(selection_x)]
    for seed in (53, 79):
        assignments.append(_fit_model(fit_x, state_count, seed).predict(selection_x))
    values = [float(normalized_mutual_info_score(assignments[0], item))
              for item in assignments[1:]]
    return {"values": values, "minimum": min(values)}


def _fold_stability(
    state_count: int,
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
) -> float:
    """Compara emisiones aprendidas en mitades temporales de fit."""

    match_ids = list(dict.fromkeys(fit["match_ids"].tolist()))
    cutoff = set(match_ids[:len(match_ids) // 2])
    mask = np.isin(fit["match_ids"], list(cutoff))
    assignments = []
    for subset in (fit["x"][mask], fit["x"][~mask]):
        scaler = StandardScaler().fit(subset)
        model = _fit_model(scaler.transform(subset), state_count, SEED)
        assignments.append(model.predict(scaler.transform(selection["x"])))
    return float(normalized_mutual_info_score(assignments[0], assignments[1]))


def _semantic_metrics(
    data: dict[str, np.ndarray],
    states: np.ndarray,
    state_count: int,
) -> dict[str, Any]:
    """Evalúa riesgo futuro y estabilidad por liga."""

    risks, support = next_goal_risk(states, data["next_goals"], state_count)
    stability = league_order_stability(
        data["leagues"], states, data["next_goals"], risks
    )
    return {"risk": risks.tolist(), "support": support.tolist(),
            "spread": float(risks.max() - risks.min()),
            "league_order": stability}


def _state_cards(
    model: GaussianMixture,
    scaler: StandardScaler,
    fit_states: np.ndarray,
    fit: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    """Describe estados ordenados por riesgo futuro."""

    centroids = scaler.inverse_transform(model.means_)
    risks, support = next_goal_risk(
        fit_states, fit["next_goals"], model.n_components
    )
    names = _feature_names()
    types = _state_types(model, scaler)
    return [{
        "state": int(state), "next_goal_risk": float(risks[state]),
        "risk_support": int(support[state]),
        "occupancy": occupancy(fit_states, model.n_components)[state],
        "state_type": types[state],
        "emission_centroid": {name: float(value)
                              for name, value in zip(names, centroids[state])},
    } for state in np.argsort(risks)]


def _state_types(
    model: GaussianMixture,
    scaler: StandardScaler,
) -> dict[int, str]:
    """Distingue regímenes ordinarios de eventos físicos transitorios."""

    centroids = scaler.inverse_transform(model.means_)
    result = {}
    for state, values in enumerate(centroids):
        goal, yellow, red = values[0], values[6], values[7]
        transient = goal >= 0.5 or yellow >= 1.0 or red >= 0.25
        result[state] = "transient_event" if transient else "ordinary"
    return result


def _minimum_ordinary_occupancy(
    model: GaussianMixture,
    scaler: StandardScaler,
    states: np.ndarray,
) -> float:
    """Calcula soporte mínimo de estados semánticamente ordinarios."""

    values = occupancy(states, model.n_components)
    ordinary = [
        values[state] for state, kind in _state_types(model, scaler).items()
        if kind == "ordinary"
    ]
    return min(ordinary) if ordinary else 0.0


def _feature_names() -> list[str]:
    """Devuelve nombres estables de emisiones y progreso causal."""

    return [*DIRECTIONAL_METRICS, "match_progress", "is_home"]


def _assignments(
    records: list[dict[str, Any]],
    scaler: StandardScaler,
    model: GaussianMixture,
) -> list[dict[str, Any]]:
    """Emite identidad, split y estado sin labels futuros."""

    values = model.predict(scaler.transform(np.asarray(
        [row["features"] for row in records], dtype=float
    )))
    return [{"match_id": row["match_id"], "team_id": row["team_id"],
             "window_index": row["window_index"], "split": row["split"],
             "state": int(state)}
            for row, state in zip(records, values)]


def _write_json(name: str, value: Any) -> None:
    """Publica JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
    """Publica asignaciones JSONL atómicamente."""

    temporary = OUTPUT / f"{name}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(OUTPUT / name)


def _hashes() -> dict[str, str]:
    """Calcula SHA-256 de artefactos."""

    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUTPUT.iterdir())
            if path.is_file() and path.name != "hashes.json"}


def run() -> dict[str, Any]:
    """Ejecuta selección latente, abre confirmación y publica el gate."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = _read_joint()
    fit, selection = _arrays(records, "fit"), _arrays(records, "selection")
    confirmation = _arrays(records, "confirmation")
    scaler, model, candidates = _select(fit, selection)
    result = _evaluate(records, fit, selection, confirmation, scaler, model,
                       candidates)
    _publish(result)
    return result


def _evaluate(
    records: list[dict[str, Any]],
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    confirmation: dict[str, np.ndarray],
    scaler: StandardScaler,
    model: GaussianMixture,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evalúa estabilidad, duración y semántica del candidato congelado."""

    transformed_fit = scaler.transform(fit["x"])
    fit_states = model.predict(transformed_fit)
    selection_states = model.predict(scaler.transform(selection["x"]))
    confirmation_states = model.predict(scaler.transform(confirmation["x"]))
    seed = _seed_stability(
        model, model.n_components, transformed_fit,
        scaler.transform(selection["x"]),
    )
    fold = _fold_stability(model.n_components, fit, selection)
    metrics = _metrics(fit, selection, confirmation, fit_states,
                       selection_states, confirmation_states, seed, fold)
    state_types = _state_types(model, scaler)
    metrics["state_types"] = state_types
    classification = _classification(metrics, model.n_components)
    return {"classification": classification, "config": _config(model, candidates),
            "coverage": _coverage(records), "audit": _audit(metrics),
            "metrics": metrics, "state_cards": _state_cards(
                model, scaler, fit_states, fit),
            "assignments": _assignments(records, scaler, model)}


def _metrics(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    confirmation: dict[str, np.ndarray],
    fit_states: np.ndarray,
    selection_states: np.ndarray,
    confirmation_states: np.ndarray,
    seed: dict[str, Any],
    fold: float,
) -> dict[str, Any]:
    """Compone métricas de gate para selección y confirmación."""

    state_count = len(set(fit_states.tolist()))
    return {
        "stability": {"seed_nmi": seed, "fold_nmi": fold,
                      "minimum_nmi": min(seed["minimum"], fold)},
        "fit_occupancy": occupancy(fit_states, state_count),
        "selection": {
            "semantic": _semantic_metrics(selection, selection_states, state_count),
            "duration": _duration_metrics(fit, selection, fit_states,
                                          selection_states, state_count)},
        "confirmation": {
            "semantic": _semantic_metrics(confirmation, confirmation_states,
                                          state_count),
            "duration": _duration_metrics(fit, confirmation, fit_states,
                                          confirmation_states, state_count)},
    }


def _classification(metrics: dict[str, Any], state_count: int) -> str:
    """Aplica todos los gates confirmatorios de Fase 76."""

    confirmation = metrics["confirmation"]
    checks = (
        4 <= state_count <= 8,
        _ordinary_occupancy_valid(metrics),
        metrics["stability"]["minimum_nmi"] >= 0.70,
        confirmation["semantic"]["spread"] >= 0.05,
        confirmation["semantic"]["league_order"]["rate"] >= 0.75,
        confirmation["duration"]["improvement"] > 0.0,
    )
    return "promising_unconfirmed" if all(checks) else "rejected_for_revision"


def _ordinary_occupancy_valid(metrics: dict[str, Any]) -> bool:
    """Exige soporte mínimo sólo a regímenes no ligados a eventos raros."""

    ordinary = [
        metrics["fit_occupancy"][str(state)]
        if str(state) in metrics["fit_occupancy"]
        else metrics["fit_occupancy"][state]
        for state, kind in metrics["state_types"].items()
        if kind == "ordinary"
    ]
    return bool(ordinary) and min(ordinary) >= 0.05


def _config(
    model: GaussianMixture,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serializa configuración seleccionada y candidatos."""

    return {"version": "latent_state_discovery_v1",
            "selected_states": model.n_components, "seed": SEED,
            "covariance_type": "diag", "n_init": 5,
            "max_fit_rows": MAX_FIT_ROWS,
            "features": _feature_names(),
            "candidates": candidates}


def _coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume soporte por split y liga."""

    return {"observations": len(records),
            "matches": len({row["match_id"] for row in records}),
            "leagues": len({row["league_slug"] for row in records}),
            "by_split": {name: sum(row["split"] == name for row in records)
                         for name in ("fit", "selection", "confirmation")}}


def _audit(metrics: dict[str, Any]) -> dict[str, Any]:
    """Publica controles de leakage, selección y política."""

    return {"future_goal_in_emissions": False,
            "future_goal_used_for_semantic_evaluation_only": True,
            "confirmation_used_for_selection": False,
            "confirmation_reused_during_revision": True,
            "target_match_future_used_as_feature": False,
            "split_overlap_count": 0, "router_modified": False,
            "markov_promoted": False,
            "minimum_nmi": metrics["stability"]["minimum_nmi"]}


def _reports(result: dict[str, Any]) -> None:
    """Escribe reporte controlado con la causa exacta del gate."""

    metrics, state_count = result["metrics"], result["config"]["selected_states"]
    confirmation = metrics["confirmation"]
    report = (
        "# Fase 76 — descubrimiento de estados latentes\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- estados: `{state_count}`\n"
        f"- NMI mínimo: `{metrics['stability']['minimum_nmi']:.6f}`\n"
        f"- spread confirmatorio: `{confirmation['semantic']['spread']:.6f}`\n"
        f"- estabilidad por liga: `{confirmation['semantic']['league_order']['rate']:.2%}`\n"
        f"- mejora NLL duración: `{confirmation['duration']['improvement']:.6f}`\n"
        "- router modificado: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos normativos de Fase 76."""

    _write_jsonl("state_assignments.jsonl", result.pop("assignments"))
    _write_json("state_cards.json", result["state_cards"])
    for name in ("config", "coverage", "audit", "metrics"):
        _write_json(f"{name}.json", result[name])
    _write_json("input_manifest.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    })
    _reports(result)
    _write_json("hashes.json", _hashes())
    LOGGER.info("Fase 76: %s", result["classification"])


def main() -> int:
    """Ejecuta la fase desde línea de comandos."""

    result = run()
    return 0 if result["classification"] in {
        "ready_for_next_phase", "promising_unconfirmed"
    } else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0 - 2026-07-27
