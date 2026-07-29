"""Entrena y congela estados Markov robustos entre dominios.

Requirements:
    numpy>=2.0
    scikit-learn>=1.5

Version: 3.0.0
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

from scripts.run_phase_76_latent_state_discovery import _arrays, _read_joint  # noqa: E402
from scripts.run_phase_76_predictive_state_reaudit import _duration  # noqa: E402
from src.domain_robust_states import feature_names, rolling_domain_features  # noqa: E402
from src.latent_state_discovery import (  # noqa: E402
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

OUTPUT = ROOT / "artifacts/phase_76_domain_robust_reaudit"
SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_5m.jsonl"
LOGGER = logging.getLogger(__name__)
STATE_COUNT = 4
C_VALUE = 0.0001


def _engineer(data: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Aplica emisiones invariantes y memoria causal."""

    return {
        name: (rolling_domain_features(data["x"], data["match_ids"])
               if name == "x" else values)
        for name, values in data.items()
    }


def _fit(data: dict[str, np.ndarray]) -> PredictiveStateModel:
    """Ajusta el candidato únicamente sobre fit."""

    valid = np.isfinite(data["next_goals"])
    model = PredictiveStateModel(STATE_COUNT, C_VALUE)
    model.fit(data["x"][valid], data["next_goals"][valid].astype(int))
    return model


def _fold_nmi(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
) -> float:
    """Mide identidad entre mitades temporales de fit."""

    identities = list(dict.fromkeys(fit["match_ids"].tolist()))
    left = set(identities[:len(identities) // 2])
    mask = np.isin(fit["match_ids"], list(left))
    models = [_fit(_subset(fit, part)) for part in (mask, ~mask)]
    states = [model.states(selection["x"]) for model in models]
    return float(normalized_mutual_info_score(states[0], states[1]))


def _subset(
    data: dict[str, np.ndarray],
    mask: np.ndarray,
) -> dict[str, np.ndarray]:
    """Recorta todas las matrices de una cohorte."""

    return {name: values[mask] for name, values in data.items()}


def _metrics(
    fit: dict[str, np.ndarray],
    selection: dict[str, np.ndarray],
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Calcula todos los gates internos sin abrir el holdout."""

    fit_states = model.states(fit["x"])
    states = model.states(selection["x"])
    risks, support = next_goal_risk(
        states, selection["next_goals"], STATE_COUNT
    )
    valid = np.isfinite(selection["next_goals"])
    null = permutation_spreads(
        states, selection["next_goals"], STATE_COUNT, 200, 27
    )
    spread = float(np.ptp(risks))
    return {
        "risk": risks.tolist(), "support": support.tolist(), "spread": spread,
        "minimum_occupancy": min(occupancy(states, STATE_COUNT).values()),
        "fold_nmi": _fold_nmi(fit, selection),
        "league_order": league_order_stability(
            selection["leagues"], states, selection["next_goals"], risks),
        "duration": _duration(
            fit, selection, fit_states, states, STATE_COUNT),
        "selection_log_loss": float(log_loss(
            selection["next_goals"][valid],
            model.risk(selection["x"][valid]), labels=[0, 1])),
        "permutation_p_value": float(
            (1 + np.sum(null >= spread)) / (len(null) + 1)),
        "permutation_p95": float(np.quantile(null, 0.95)),
    }


def _eligible(metrics: dict[str, Any]) -> bool:
    """Aplica los criterios congelados de Fase 76."""

    return bool(
        metrics["spread"] >= 0.05
        and metrics["minimum_occupancy"] >= 0.05
        and metrics["fold_nmi"] >= 0.70
        and metrics["league_order"]["rate"] >= 0.75
        and metrics["duration"]["improvement"] > 0.0
        and metrics["permutation_p_value"] < 0.05
    )


def _parameters(
    fit: dict[str, np.ndarray],
    model: PredictiveStateModel,
) -> dict[str, Any]:
    """Serializa inferencia y duración sin dependencias entrenadas."""

    if model.scaler is None or model.classifier is None:
        raise RuntimeError("predictive_state_model_not_fitted")
    states = model.states(fit["x"])
    return {
        "feature_transform": "rolling_domain_features_v1",
        "feature_names": feature_names(),
        "scaler_mean": model.scaler.mean_.tolist(),
        "scaler_scale": model.scaler.scale_.tolist(),
        "coefficients": model.classifier.coef_[0].tolist(),
        "intercept": float(model.classifier.intercept_[0]),
        "boundaries": model.boundaries.tolist(),
        "duration_explicit": duration_probabilities(
            fit["match_ids"], states, STATE_COUNT).tolist(),
        "duration_geometric": geometric_probabilities(
            fit["match_ids"], states, STATE_COUNT).tolist(),
    }


def run() -> dict[str, Any]:
    """Entrena, audita y congela el candidato robusto."""

    records = _read_joint()
    fit = _engineer(_arrays(records, "fit"))
    selection = _engineer(_arrays(records, "selection"))
    model = _fit(fit)
    metrics = _metrics(fit, selection, model)
    result = {
        "classification": ("promising_unconfirmed"
                           if _eligible(metrics)
                           else "rejected_for_revision"),
        "config": {"version": "predictive_latent_state_v3",
                   "states": STATE_COUNT, "c_value": C_VALUE,
                   "feature_transform": "rolling_domain_features_v1",
                   "selection_policy":
                       "best_continuous_log_loss_then_simplest_eligible"},
        "coverage": {"fit_rows": len(fit["x"]),
                     "selection_rows": len(selection["x"])},
        "audit": {"holdout_used_for_fit": False,
                  "holdout_used_for_selection": False,
                  "target_used_as_feature": False,
                  "router_modified": False},
        "metrics": metrics,
        "model_parameters": _parameters(fit, model),
    }
    _publish(result)
    return result


def _write(name: str, value: Any) -> None:
    """Publica un JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _publish(result: dict[str, Any]) -> None:
    """Publica el contrato normativo completo."""

    for name in ("config", "coverage", "audit", "metrics",
                 "model_parameters"):
        _write(f"{name}.json", result[name])
    _write("input_manifest.json", {
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    })
    report = (
        "# Fase 76 — estados robustos entre dominios\n\n"
        f"**Clasificación:** `{result['classification']}`\n\n"
        f"- spread selection: `{result['metrics']['spread']:.6f}`\n"
        f"- NMI temporal: `{result['metrics']['fold_nmi']:.6f}`\n"
        f"- estabilidad: `{result['metrics']['league_order']['rate']:.2%}`\n"
        f"- duración: `{result['metrics']['duration']['improvement']:.6f}`\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write("hashes.json", {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"
    })
    LOGGER.info("Estados robustos: %s", result["classification"])


def main() -> int:
    """Ejecuta la reauditoría y exige gates internos completos."""

    return 0 if run()["classification"] == "promising_unconfirmed" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 3.0.0 - 2026-07-28
