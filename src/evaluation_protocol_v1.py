"""Auditoría de disponibilidad para la evaluación pre-match fuera de muestra.

No reemplaza predicciones faltantes con resultados retrospectivos.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
OUTPUT = ROOT / "artifacts/phase_05_evaluation_protocol_v1"
SOURCES = {
    "dixon_coles": ROOT / "artifacts/phase_3_4_dixon_coles_v1_dry_run_replay/dixon_coles_v1_predictions.json",
    "kalman": ROOT / "artifacts/phase_3_8_kalman_v1_dry_run_replay/kalman_v1_predictions.json",
    "markov_dependent": ROOT / "artifacts/phase_04_pre_match_simulation_v1/simulation_result.json",
}


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Configuración congelada de auditoría de cobertura OOS."""

    version: str = "evaluation_protocol_v1"
    min_confirmation_matches: int = 50
    required_comparators: tuple[str, ...] = ("baseline_simple", "dixon_coles", "kalman", "markov_global", "markov_dependent")


class PredictionEvaluator(ABC):
    """Contrato para evaluadores de predicciones pre-match serializadas."""

    @abstractmethod
    def evaluate(self) -> dict[str, Any]:
        """Devuelve cobertura, violaciones y decisión de promoción."""


def _hash(value: Any) -> str:
    """Calcula un hash SHA-256 estable para artefactos de auditoría."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON por reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _load(path: Path) -> Any:
    """Carga un JSON de evidencia existente sin modificarlo."""
    return json.loads(path.read_text(encoding="utf-8"))


def _target_ids() -> set[int]:
    """Obtiene identidad de partidos cerrados sólo para medir cobertura."""
    return {int(row["match_id"]) for row in _load(WINDOWS)}


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    """Devuelve filas por partido si el artefacto usa el contrato requerido."""
    payload = _load(path)
    return payload if isinstance(payload, list) and all("match_id" in row for row in payload) else []


def _availability(name: str, path: Path, targets: set[int], minimum: int) -> dict[str, Any]:
    """Mide cobertura y detecta si un artefacto es evaluable por partido."""
    rows = _prediction_rows(path) if path.exists() else []
    ids = {int(row["match_id"]) for row in rows if str(row["match_id"]).isdigit()}
    overlap = ids & targets
    eligible = bool(rows) and len(overlap) >= minimum and not _missing_fields(rows)
    return {"source": str(path.relative_to(ROOT)), "exists": path.exists(), "row_count": len(rows), "match_coverage": len(overlap), "coverage_rate": len(overlap) / len(targets), "oos_contract": bool(rows), "minimum_confirmation_matches": minimum, "missing_probability_fields": _missing_fields(rows), "eligible": eligible}


def _missing_fields(rows: list[dict[str, Any]]) -> list[str]:
    """Comprueba las probabilidades 1X2 mínimas del protocolo."""
    if not rows: return ["match_id", "prob_1", "prob_x", "prob_2"]
    first = rows[0]
    aliases = (("prob_1", "prob_1_dc", "prob_1_kalman"), ("prob_x", "prob_x_dc", "prob_x_kalman"), ("prob_2", "prob_2_dc", "prob_2_kalman"))
    return [canonical for canonical, *options in aliases if canonical not in first and not any(option in first for option in options)]


class AvailabilityEvaluator(PredictionEvaluator):
    """Evalúa si existe evidencia suficiente para iniciar métricas OOS."""

    def __init__(self, config: EvaluationConfig) -> None:
        """Guarda configuración sin leer ni alterar fuentes."""
        self.config = config

    def evaluate(self) -> dict[str, Any]:
        """Audita comparadores obligatorios y bloquea evidencia incompleta."""
        targets = _target_ids()
        available = {name: _availability(name, path, targets, self.config.min_confirmation_matches) for name, path in SOURCES.items()}
        synthetic = {"baseline_simple": "missing", "markov_global": "missing"}
        missing = [name for name in self.config.required_comparators if name in synthetic or not available.get(name, {}).get("eligible", False)]
        audit = {"target_match_count": len(targets), "comparators": available, "missing_required": missing, "target_outcomes_used_as_features": False, "bootstrap_executed": False, "promotion_allowed": False}
        return {"config": asdict(self.config), "audit": audit, "classification": "blocked_by_data"}


def run(config: EvaluationConfig | None = None) -> dict[str, Any]:
    """Ejecuta la Fase 05 y publica un bloqueo reproducible si corresponde."""
    result = AvailabilityEvaluator(config or EvaluationConfig()).evaluate()
    _publish(result)
    LOGGER.info("Fase 05 evaluation_protocol: %s", result["classification"])
    return result


def _publish(result: dict[str, Any]) -> None:
    """Publica cobertura, auditoría y decisión sin inventar métricas faltantes."""
    manifest = {name: _hash(_load(path)) for name, path in SOURCES.items() if path.exists()}
    _write("config.json", result["config"])
    _write("input_manifest.json", manifest)
    _write("coverage.json", result["audit"]["comparators"])
    _write("audit.json", {**result["audit"], "classification": result["classification"]})
    report = ["# Fase 05 — evaluation_protocol v1", "", "**Clasificación:** `blocked_by_data`", "", "- no se ejecutaron métricas ni bootstrap: faltan comparadores OOS compatibles por partido.", f"- comparadores faltantes o no evaluables: `{result['audit']['missing_required']}`", "- promoción bloqueada; no se infieren métricas desde artefactos parciales.", "- siguiente paso: generar predicciones OOS canónicas por partido para cada comparador."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


# Version: 1.0.0
# Created: 2026-07-26
