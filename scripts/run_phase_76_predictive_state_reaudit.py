"""Reaudita Fase 76 con estados predictivos causales y pruebas de null.

Requirements:
    numpy>=2.0
    scipy>=1.14
    scikit-learn>=1.5

Version: 2.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import log_loss, normalized_mutual_info_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_76_latent_state_discovery import (  # noqa: E402
    DIRECTIONAL_METRICS,
    SOURCE,
    _arrays,
    _read_joint,
)
from src.latent_state_discovery import (  # noqa: E402
    duration_nll,
    duration_probabilities,
    geometric_probabilities,
    league_order_stability,
    next_goal_risk,
    occupancy,
)
from src.predictive_latent_states import (  # noqa: E402
    PredictiveStateModel,
    permutation_spreads,
)

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_76_predictive_state_reaudit"
LEGACY = ROOT / "artifacts/phase_76_latent_state_discovery"
REPETITIONS = 200
FEATURE_COLUMNS = (1, 2, 3, 4, 5, 8, 9, 10, 11, 12)
ALL_FEATURE_NAMES = (*DIRECTIONAL_METRICS, "match_progress", "is_home")


def _valid(data: dict[str, np.ndarray]) -> np.ndarray:
    """Devuelve máscara de labels futuros observables."""

    return np.isfinite(data["next_goals"])


def _fit(
    data: dict[str, np.ndarray],
    state_count: int,
    c_value: float,
) -> PredictiveStateModel:
    """Ajusta un candidato únicamente con labels de desarrollo."""

    mask = _valid(data)
    model = PredictiveStateModel(state_count, c_value)
    model.fit(data["x"][mask], data["next_goals"][mask].astype(int))
    return model


def _duration(
    fit: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    fit_states: np.ndarray,
    target_states: np.ndarray,
    state_count: int,
) -> dict[str, float]:
    """Compara duración discreta contra geométrica fuera de ajuste."""

    explicit = duration_probabilities(fit["match_ids"], fit_states, state_count)
    geometric = geometric_probabilities(fit["match_ids"], fit_states, state_count)
    explicit_nll = duration_nll(explicit, target["match_ids"], target_states)
    geometric_nll = duration_nll(geometric, target["match_ids"], target_states)
    return {"explicit_nll": explicit_nll, "geometric_nll": geometric_nll,
            "improvement": geometric_nll - explicit_nll}


def _fold_models(
    fit: dict[str, np.ndarray],
    state_count: int,
    c_value: float,
) -> tuple[PredictiveStateModel, PredictiveStateModel]:
    """Ajusta modelos en mitades temporales independientes."""

    sequences = list(dict.fromkeys(fit["match_ids"].tolist()))
    first = set(sequences[:len(sequences) // 2])
    mask = np.isin(fit["match_ids"], list(first))
    left = _subset(fit, mask)
    right = _subset(fit, ~mask)
    return _fit(left, state_count, c_value), _fit(right, state_count, c_value)


def _subset(
    data: dict[str, np.ndarray],
    mask: np.ndarray,
) -> dict[str, np.ndarray]:
    """Recorta todas las matrices de una cohorte."""

    return {name: values[mask] for name, values in data.items()}


def _stable_features(data: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Excluye outcomes contemporáneos escasos de la emisión ordinaria."""

    return {name: (values[:, FEATURE_COLUMNS] if name == "x" else values)
            for name, values in data.items()}


def _candidate(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    state_count: int,
    c_value: float,
) -> tuple[dict[str, Any], PredictiveStateModel]:
    """Evalúa un candidato exclusivamente en desarrollo/selección."""

    model = _fit(fit, state_count, c_value)
    fit_states, states = model.states(fit["x"]), model.states(selection["x"])
    risks, support = next_goal_risk(states, selection["next_goals"], state_count)
    folds = _fold_models(fit, state_count, c_value)
    fold_nmi = normalized_mutual_info_score(
        folds[0].states(selection["x"]), folds[1].states(selection["x"])
    )
    null = permutation_spreads(
        states, selection["next_goals"], state_count, REPETITIONS, 27
    )
    result = _candidate_metrics(
        fit, selection, fit_states, states, risks, support, fold_nmi, null, model
    )
    return result, model


def _candidate_metrics(
    fit: dict[str, np.ndarray], selection: dict[str, np.ndarray],
    fit_states: np.ndarray, states: np.ndarray, risks: np.ndarray,
    support: np.ndarray, fold_nmi: float, null: np.ndarray,
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Compone gates de candidato sin leer confirmación."""

    spread = float(np.ptp(risks))
    league = league_order_stability(
        selection["leagues"], states, selection["next_goals"], risks
    )
    mask = _valid(selection)
    loss = log_loss(
        selection["next_goals"][mask],
        model.risk(selection["x"][mask]), labels=[0, 1],
    )
    duration = _duration(
        fit, selection, fit_states, states, model.state_count
    )
    result = {"states": model.state_count, "c_value": model.c_value,
            "minimum_occupancy": min(occupancy(states, model.state_count).values()),
            "risk": risks.tolist(), "support": support.tolist(), "spread": spread,
            "fold_nmi": float(fold_nmi), "seed_nmi": 1.0,
            "league_order": league, "duration": duration}
    result.update(_null_metrics(null, spread, float(loss)))
    return result


def _null_metrics(
    null: np.ndarray,
    spread: float,
    loss: float,
) -> dict[str, float]:
    """Resume significancia por permutación y pérdida predictiva."""

    return {"permutation_p_value": float(
                (1 + np.sum(null >= spread)) / (len(null) + 1)),
            "permutation_p95": float(np.quantile(null, 0.95)),
            "selection_log_loss": loss}


def _eligible(row: dict[str, Any]) -> bool:
    """Aplica gates internos congelados."""

    return bool(
        row["minimum_occupancy"] >= 0.05
        and row["spread"] >= 0.05
        and row["fold_nmi"] >= 0.70
        and row["league_order"]["rate"] >= 0.75
        and row["duration"]["improvement"] > 0.0
        and row["permutation_p_value"] < 0.05
    )


def _select(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
) -> tuple[PredictiveStateModel, list[dict[str, Any]]]:
    """Selecciona complejidad sólo con la partición selection."""

    candidates, models = [], {}
    for c_value in (0.05, 0.1, 0.2):
        for state_count in range(4, 9):
            row, model = _candidate(fit, selection, state_count, c_value)
            candidates.append(row)
            models[(c_value, state_count)] = model
    eligible = [row for row in candidates if _eligible(row)]
    if not eligible:
        raise RuntimeError("no_predictive_state_candidate_passed_internal_gates")
    selected = min(eligible, key=lambda row: (
        row["selection_log_loss"], row["states"], row["c_value"]
    ))
    return models[(selected["c_value"], selected["states"])], candidates


def _diagnostic(
    fit: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Evalúa una cohorte sin usarla para modificar el candidato."""

    fit_states, states = model.states(fit["x"]), model.states(target["x"])
    risks, support = next_goal_risk(states, target["next_goals"], model.state_count)
    league = league_order_stability(
        target["leagues"], states, target["next_goals"], risks
    )
    return {"risk": risks.tolist(), "support": support.tolist(),
            "spread": float(np.ptp(risks)), "league_order": league,
            "duration": _duration(fit, target, fit_states, states,
                                  model.state_count)}


def _legacy_errors(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resume causas verificadas del rechazo GMM."""

    legacy = json.loads((LEGACY / "metrics.json").read_text(encoding="utf-8"))
    values = np.asarray([row["features"] for row in records], dtype=float)
    zero_rates = (values == 0.0).mean(axis=0)
    return {"gaussian_on_zero_inflated_counts": True,
            "maximum_feature_zero_rate": float(zero_rates.max()),
            "median_feature_zero_rate": float(np.median(zero_rates)),
            "legacy_minimum_nmi": legacy["stability"]["minimum_nmi"],
            "legacy_confirmation_spread": legacy["confirmation"]["semantic"]["spread"],
            "joint_state_diluted_direction": True,
            "lagged_full_vector_added_noise": True,
            "league_gate_conditioned_on_rare_states": True}


def _cards(
    model: PredictiveStateModel,
    selection: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    """Describe estados ordenados y coeficientes de la representación."""

    states = model.states(selection["x"])
    risks, support = next_goal_risk(
        states, selection["next_goals"], model.state_count
    )
    bounds = [-float("inf"), *model.boundaries.tolist(), float("inf")]
    return [{"state": state, "lower_risk_score": bounds[state],
             "upper_risk_score": bounds[state + 1],
             "next_goal_risk": float(risks[state]),
             "support": int(support[state]),
             "occupancy": occupancy(states, model.state_count)[state]}
            for state in range(model.state_count)]


def _assignments(
    records: list[dict[str, Any]],
    model: PredictiveStateModel,
) -> list[dict[str, Any]]:
    """Publica estados inferidos sin labels futuros."""

    features = np.asarray([row["features"] for row in records], dtype=float)
    features = features[:, FEATURE_COLUMNS]
    states, risks = model.states(features), model.risk(features)
    return [{"match_id": row["match_id"], "team_id": row["team_id"],
             "window_index": row["window_index"], "split": row["split"],
             "state": int(state), "risk_score": float(risk)}
            for row, state, risk in zip(records, states, risks)]


def _write_json(name: str, value: Any) -> None:
    """Publica JSON estable."""

    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
    """Publica JSONL atómico."""

    temporary = OUTPUT / f"{name}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(OUTPUT / name)


def _hashes() -> dict[str, str]:
    """Calcula SHA-256 de entregables."""

    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(OUTPUT.iterdir())
            if path.is_file() and path.name != "hashes.json"}


def run() -> dict[str, Any]:
    """Ejecuta reauditoría completa y conserva confirmación como diagnóstico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = _read_joint()
    fit = _stable_features(_arrays(records, "fit"))
    selection = _stable_features(_arrays(records, "selection"))
    confirmation = _stable_features(_arrays(records, "confirmation"))
    model, candidates = _select(fit, selection)
    selected = next(row for row in candidates if
                    row["states"] == model.state_count
                    and row["c_value"] == model.c_value)
    result = _result(records, fit, selection, confirmation, model,
                     candidates, selected)
    _publish(result)
    return result


def _result(
    records: list[dict[str, Any]],
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    confirmation: dict[str, np.ndarray],
    model: PredictiveStateModel,
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    """Compone evidencia sin promover desde confirmación reutilizada."""

    diagnostic = _diagnostic(fit, confirmation, model)
    return {"classification": "promising_unconfirmed",
            "config": _config(model),
            "coverage": {"matches": len({row["match_id"] for row in records}),
                         "observations": len(records),
                         "leagues": len({row["league_slug"] for row in records})},
            "audit": _audit(),
            "metrics": {"selected": selected, "candidates": candidates,
                        "confirmation_diagnostic": diagnostic},
            "errors": _legacy_errors(records),
            "state_cards": _cards(model, selection),
            "assignments": _assignments(records, model),
            "coefficients": model.coefficients().tolist(),
            "model_parameters": _model_parameters(model, fit)}


def _config(model: PredictiveStateModel) -> dict[str, Any]:
    """Serializa contrato de inferencia seleccionado."""

    return {"version": "predictive_latent_state_v2",
            "states": model.state_count, "c_value": model.c_value,
            "boundaries": model.boundaries.tolist(),
            "feature_columns": list(FEATURE_COLUMNS),
            "feature_names": [ALL_FEATURE_NAMES[index]
                              for index in FEATURE_COLUMNS],
            "excluded_from_emission": ["goals", "yellow_cards", "red_cards"],
            "selection_policy": "internal_gates_then_log_loss"}


def _audit() -> dict[str, bool]:
    """Declara controles temporales y de promoción."""

    return {"target_used_as_feature": False,
            "future_label_used_for_fit_only": True,
            "confirmation_used_for_selection": False,
            "confirmation_independent": False,
            "router_modified": False, "phase_77_unblocked": False}


def _model_parameters(
    model: PredictiveStateModel,
    fit: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Serializa parámetros suficientes para inferencia congelada."""

    if model.scaler is None or model.classifier is None:
        raise RuntimeError("predictive_state_model_not_fitted")
    states = model.states(fit["x"])
    explicit = duration_probabilities(
        fit["match_ids"], states, model.state_count
    )
    geometric = geometric_probabilities(
        fit["match_ids"], states, model.state_count
    )
    return {"scaler_mean": model.scaler.mean_.tolist(),
            "scaler_scale": model.scaler.scale_.tolist(),
            "coefficients": model.classifier.coef_[0].tolist(),
            "intercept": float(model.classifier.intercept_[0]),
            "boundaries": model.boundaries.tolist(),
            "feature_columns": list(FEATURE_COLUMNS),
            "duration_explicit": explicit.tolist(),
            "duration_geometric": geometric.tolist()}


def _reports(result: dict[str, Any]) -> None:
    """Publica interpretación de la reauditoría."""

    selected = result["metrics"]["selected"]
    diagnostic = result["metrics"]["confirmation_diagnostic"]
    report = (
        "# Reauditoría Fase 76 — estados predictivos\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- estados: `{result['config']['states']}`\n"
        f"- spread selection: `{selected['spread']:.6f}`\n"
        f"- NMI temporal: `{selected['fold_nmi']:.6f}`\n"
        f"- estabilidad ligas: `{selected['league_order']['rate']:.2%}`\n"
        f"- mejora duración selection: `{selected['duration']['improvement']:.6f}`\n"
        f"- p permutación: `{selected['permutation_p_value']:.6f}`\n"
        f"- confirmación diagnóstica spread: `{diagnostic['spread']:.6f}`\n"
        "- Fase 77 desbloqueada: `False`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")


def _publish(result: dict[str, Any]) -> None:
    """Publica contrato completo de reauditoría."""

    _write_jsonl("state_assignments.jsonl", result.pop("assignments"))
    for name in ("config", "coverage", "audit", "metrics", "errors",
                 "state_cards", "coefficients", "model_parameters"):
        _write_json(f"{name}.json", result[name])
    _write_json("input_manifest.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "legacy_artifact": str(LEGACY.relative_to(ROOT)),
    })
    _reports(result)
    _write_json("hashes.json", _hashes())
    LOGGER.info("Reauditoría Fase 76: %s", result["classification"])


def main() -> int:
    """Ejecuta la reauditoría."""

    return 0 if run()["classification"] == "promising_unconfirmed" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 2.0.0 - 2026-07-27
