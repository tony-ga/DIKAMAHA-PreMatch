"""Reaudita Fase 76 con estados de cola y validación temporal anidada.

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
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import log_loss, normalized_mutual_info_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_76_domain_robust_reaudit import _engineer  # noqa: E402
from scripts.run_phase_76_latent_state_discovery import (  # noqa: E402
    _arrays,
    _read_joint,
)
from scripts.run_phase_76_predictive_state_reaudit import _duration  # noqa: E402
from src.domain_robust_states import feature_names  # noqa: E402
from src.latent_state_discovery import (  # noqa: E402
    duration_probabilities,
    geometric_probabilities,
    league_order_stability,
    next_goal_risk,
    occupancy,
)
from src.predictive_latent_states import PredictiveStateModel  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_76_crossfit_reaudit"
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
C_VALUES = (0.0000001, 0.0000003, 0.000001, 0.000003,
            0.00001, 0.00003, 0.0001)
PROFILES = {
    "tail_4": (0.10, 0.50, 0.90),
    "tail_5": (0.08, 0.25, 0.75, 0.92),
    "tail_6": (0.06, 0.18, 0.50, 0.82, 0.94),
}


def _concat(parts: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Concatena bloques cronológicos con el mismo contrato."""

    return {
        name: np.concatenate([part[name] for part in parts])
        for name in parts[0]
    }


def _subset(
    data: dict[str, np.ndarray],
    mask: np.ndarray,
) -> dict[str, np.ndarray]:
    """Recorta todas las matrices sin perder alineación."""

    return {name: values[mask] for name, values in data.items()}


def _temporal_inner(
    data: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Reserva el último 20% de partidos del train externo."""

    match_ids = data["match_ids"] // 1_000_000
    ordered = list(dict.fromkeys(match_ids.tolist()))
    cutoff = set(ordered[:max(1, int(len(ordered) * 0.8))])
    mask = np.isin(match_ids, list(cutoff))
    return _subset(data, mask), _subset(data, ~mask)


def _temporal_halves(
    data: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Divide partidos en dos folds temporales de tamaño comparable."""

    match_ids = data["match_ids"] // 1_000_000
    ordered = list(dict.fromkeys(match_ids.tolist()))
    left = set(ordered[:len(ordered) // 2])
    mask = np.isin(match_ids, list(left))
    return _subset(data, mask), _subset(data, ~mask)


def _fit(
    data: dict[str, np.ndarray],
    c_value: float,
    quantiles: tuple[float, ...],
) -> PredictiveStateModel:
    """Ajusta riesgo y límites exclusivamente en el bloque recibido."""

    valid = np.isfinite(data["next_goals"])
    model = PredictiveStateModel(
        len(quantiles) + 1, c_value, quantiles=quantiles
    )
    model.fit(data["x"][valid], data["next_goals"][valid].astype(int))
    return model


def _fold_nmi(
    train: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    c_value: float,
    quantiles: tuple[float, ...],
) -> float:
    """Compara asignaciones aprendidas en mitades temporales."""

    left, right = _temporal_halves(train)
    models = [_fit(part, c_value, quantiles) for part in (left, right)]
    states = [model.states(target["x"]) for model in models]
    return float(normalized_mutual_info_score(states[0], states[1]))


def _metrics(
    train: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Calcula todos los gates sobre un bloque estrictamente OOS."""

    states = model.states(target["x"])
    fit_states = model.states(train["x"])
    risks, support = next_goal_risk(
        states, target["next_goals"], model.state_count
    )
    valid = np.isfinite(target["next_goals"])
    return {
        "spread": float(np.ptp(risks)), "risk": risks.tolist(),
        "support": support.tolist(),
        "minimum_occupancy": min(occupancy(states, model.state_count).values()),
        "fold_nmi": _fold_nmi(
            train, target, model.c_value, tuple(model.quantiles or ())),
        "league_order": league_order_stability(
            target["leagues"], states, target["next_goals"], risks),
        "duration": _duration(
            train, target, fit_states, states, model.state_count),
        "log_loss": float(log_loss(
            target["next_goals"][valid], model.risk(target["x"][valid]),
            labels=[0, 1])),
    }


def _eligible(metrics: dict[str, Any]) -> bool:
    """Aplica sin compensaciones los gates de semántica de Fase 76."""

    return bool(
        metrics["spread"] >= 0.05
        and metrics["minimum_occupancy"] >= 0.05
        and metrics["fold_nmi"] >= 0.70
        and metrics["league_order"]["rate"] >= 0.75
        and metrics["duration"]["improvement"] > 0.0
    )


def _select(
    train: dict[str, np.ndarray],
) -> tuple[float, str, list[dict[str, Any]]]:
    """Selecciona hiperparámetros sólo en validación interna."""

    inner_fit, inner_validation = _temporal_inner(train)
    candidates = []
    for c_value in C_VALUES:
        for name, quantiles in PROFILES.items():
            model = _fit(inner_fit, c_value, quantiles)
            metrics = _metrics(inner_fit, inner_validation, model)
            candidates.append({"c_value": c_value, "profile": name,
                               "metrics": metrics, "eligible": _eligible(metrics)})
    eligible = [row for row in candidates if row["eligible"]]
    if not eligible:
        raise RuntimeError("no_crossfit_candidate_passed_inner_gates")
    selected = min(eligible, key=lambda row: (
        len(PROFILES[row["profile"]]), -row["metrics"]["fold_nmi"],
        row["metrics"]["log_loss"],
        row["c_value"],
    ))
    return float(selected["c_value"]), str(selected["profile"]), candidates


def _outer_fold(
    name: str,
    train: dict[str, np.ndarray],
    target: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Selecciona internamente y evalúa una vez el bloque externo."""

    c_value, profile, candidates = _select(train)
    model = _fit(train, c_value, PROFILES[profile])
    metrics = _metrics(train, target, model)
    return {"name": name, "selected_c": c_value,
            "selected_profile": profile, "metrics": metrics,
            "eligible": _eligible(metrics), "inner_candidates": candidates}


def _final_model(
    data: dict[str, np.ndarray],
    folds: list[dict[str, Any]],
) -> tuple[PredictiveStateModel, dict[str, Any]]:
    """Congela la configuración modal y ajusta todo el histórico."""

    choices = [(row["selected_profile"], row["selected_c"]) for row in folds]
    selected = max(set(choices), key=lambda value: (choices.count(value), value))
    model = _fit(data, selected[1], PROFILES[selected[0]])
    return model, {"profile": selected[0], "c_value": selected[1],
                   "states": model.state_count}


def _parameters(
    data: dict[str, np.ndarray],
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Serializa el candidato final sin objetos de sklearn."""

    if model.scaler is None or model.classifier is None:
        raise RuntimeError("crossfit_model_not_fitted")
    states = model.states(data["x"])
    return {"feature_transform": "rolling_domain_features_v1",
            "feature_names": feature_names(),
            "quantiles": list(model.quantiles or ()),
            "boundaries": model.boundaries.tolist(),
            "scaler_mean": model.scaler.mean_.tolist(),
            "scaler_scale": model.scaler.scale_.tolist(),
            "coefficients": model.classifier.coef_[0].tolist(),
            "intercept": float(model.classifier.intercept_[0]),
            "duration_explicit": duration_probabilities(
                data["match_ids"], states, model.state_count).tolist(),
            "duration_geometric": geometric_probabilities(
                data["match_ids"], states, model.state_count).tolist()}


def run() -> dict[str, Any]:
    """Ejecuta dos folds externos y congela sólo si ambos pasan."""

    records = _read_joint()
    blocks = {name: _engineer(_arrays(records, name))
              for name in ("fit", "selection", "confirmation")}
    folds = [_outer_fold("selection_oos", blocks["fit"], blocks["selection"]),
             _outer_fold("confirmation_oos",
                         _concat([blocks["fit"], blocks["selection"]]),
                         blocks["confirmation"])]
    all_data = _concat(list(blocks.values()))
    model, final = _final_model(all_data, folds)
    passed = all(row["eligible"] for row in folds)
    result = _result(records, folds, final, all_data, model, passed)
    _publish(result)
    return result


def _result(
    records: list[dict[str, Any]],
    folds: list[dict[str, Any]],
    final: dict[str, Any],
    data: dict[str, np.ndarray],
    model: PredictiveStateModel,
    passed: bool,
) -> dict[str, Any]:
    """Compone evidencia y política de promoción."""

    return {"classification": ("ready_for_next_phase" if passed
                                else "rejected_for_revision"),
            "config": {"version": "predictive_latent_state_v4_tail_crossfit",
                       "profiles": {key: list(value)
                                    for key, value in PROFILES.items()},
                       "c_values": list(C_VALUES), "final": final},
            "coverage": {"matches": len({row["match_id"] for row in records}),
                         "leagues": len({row["league_slug"] for row in records}),
                         "directional_rows": len(records), "outer_folds": 2},
            "audit": {"nested_temporal_selection": True,
                      "outer_blocks_used_for_inner_selection": False,
                      "target_used_as_feature": False,
                      "prospective_confirmation_claimed": False,
                      "router_modified": False},
            "metrics": {"outer_folds": folds},
            "model_parameters": _parameters(data, model)}


def _write(name: str, value: Any) -> None:
    """Publica JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _publish(result: dict[str, Any]) -> None:
    """Publica el contrato completo y hashes reproducibles."""

    for name in ("config", "coverage", "audit", "metrics", "model_parameters"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    })
    folds = result["metrics"]["outer_folds"]
    report = "# Fase 76R — estados de cola cross-fitted\n\n"
    report += f"**Clasificación:** `{result['classification']}`\n\n"
    for row in folds:
        metrics = row["metrics"]
        report += (f"- {row['name']}: spread `{metrics['spread']:.6f}`, "
                   f"NMI `{metrics['fold_nmi']:.6f}`, estabilidad "
                   f"`{metrics['league_order']['rate']:.2%}`, duración "
                   f"`{metrics['duration']['improvement']:.6f}`\n")
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(OUTPUT.iterdir())
                           if path.is_file() and path.name != "hashes.json"})


def main() -> int:
    """Ejecuta la reauditoría y exige aprobación en ambos folds."""

    result = run()
    LOGGER.info("Fase 76R: %s", result["classification"])
    return 0 if result["classification"] == "ready_for_next_phase" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
