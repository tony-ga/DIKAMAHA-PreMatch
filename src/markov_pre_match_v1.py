"""Calibración temporal y jerárquica de transiciones Markov pre-partido.

La calibración trabaja exclusivamente con partidos históricos completos. No
simula encuentros ni genera señales de mercado.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_02_state_labeling_v1/state_labels.json"
OUTPUT = ROOT / "artifacts/phase_03_markov_pre_match_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
TIERS = ("team", "context", "window", "global")


@dataclass(frozen=True, slots=True)
class MarkovCalibrationConfig:
    """Parámetros congelados del estimador y de la validación temporal."""

    version: str = "markov_pre_match_v1"
    alpha: float = 32.0
    development_fraction: float = 0.60
    validation_fraction: float = 0.20
    min_support_team: int = 12
    min_support_context: int = 10
    min_support_window: int = 8


@dataclass(frozen=True, slots=True)
class Transition:
    """Transición causal de un equipo entre dos ventanas consecutivas."""

    match_id: int
    match_date: str
    team_id: int
    is_home: bool
    window_index: int
    score_bucket: str
    state: str
    opponent_state: str
    next_state: str


class MarkovCalibrationSolver(ABC):
    """Contrato para calibradores Markov intercambiables."""

    @abstractmethod
    def fit(self, transitions: list[Transition]) -> None:
        """Ajusta parámetros usando sólo el bloque de desarrollo."""

    @abstractmethod
    def predict(self, transition: Transition) -> tuple[dict[str, float], str, int]:
        """Predice la distribución siguiente y reporta tier y soporte."""


def _hash(value: Any) -> str:
    """Calcula SHA-256 estable de un valor serializable."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(name: str, value: Any) -> None:
    """Escribe un artefacto JSON mediante reemplazo atómico."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _score_bucket(difference: int) -> str:
    """Agrupa el marcador observable al inicio de la ventana."""
    if difference <= -2: return "behind_2_plus"
    if difference == -1: return "behind_1"
    if difference == 0: return "level"
    if difference == 1: return "ahead_1"
    return "ahead_2_plus"


def _key(tier: str, row: Transition) -> tuple[Any, ...]:
    """Construye una clave de contexto estrictamente disponible en t."""
    base = (row.state,)
    if tier == "global": return base
    if tier == "window": return (row.window_index, *base)
    context = (row.is_home, row.window_index, row.score_bucket, row.state, row.opponent_state)
    return context if tier == "context" else (row.team_id, *context)


def _parent_tier(tier: str) -> str | None:
    """Devuelve el nivel jerárquico inmediatamente más general."""
    position = TIERS.index(tier)
    return TIERS[position + 1] if position + 1 < len(TIERS) else None


def _key_text(key: tuple[Any, ...]) -> str:
    """Serializa claves de matriz para su auditoría estable."""
    return json.dumps(key, separators=(",", ":"))


def _load_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Carga artefactos ya validados de ventanas y etiquetas."""
    return json.loads(WINDOWS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8"))


def _row_index(rows: Iterable[dict[str, Any]]) -> dict[tuple[int, int, int], dict[str, Any]]:
    """Indexa un artefacto por partido, equipo y ventana."""
    return {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in rows}


def build_transitions(windows: list[dict[str, Any]], labels: list[dict[str, Any]]) -> tuple[list[Transition], dict[str, int]]:
    """Une inputs y crea sólo transiciones consecutivas del mismo partido."""
    window_index, label_index = _row_index(windows), _row_index(labels)
    transitions, audit = [], {"missing_windows": 0, "missing_labels": 0, "unknown_excluded": 0}
    for key, row in sorted(window_index.items()):
        if key[2] >= 5: continue
        transition = _build_transition(row, key, window_index, label_index, audit)
        if transition is not None: transitions.append(transition)
    return transitions, audit


def _build_transition(row: dict[str, Any], key: tuple[int, int, int], windows: dict[tuple[int, int, int], dict[str, Any]], labels: dict[tuple[int, int, int], dict[str, Any]], audit: dict[str, int]) -> Transition | None:
    """Construye una transición o registra explícitamente su exclusión."""
    match_id, team_id, index = key
    next_key, rival_key = (match_id, team_id, index + 1), (match_id, int(row["opponent_team_id"]), index)
    if next_key not in windows or rival_key not in windows: audit["missing_windows"] += 1; return None
    needed = (key, next_key, rival_key)
    if any(item not in labels for item in needed): audit["missing_labels"] += 1; return None
    state, next_state, rival_state = (str(labels[item]["state"]) for item in needed)
    if "unknown" in {state, next_state, rival_state}: audit["unknown_excluded"] += 1; return None
    return Transition(match_id, str(row["match_date"]), team_id, bool(row["is_home"]), index, _score_bucket(int(row["goal_difference_start"])), state, rival_state, next_state)


def split_match_ids(transitions: list[Transition], config: MarkovCalibrationConfig) -> dict[str, set[int]]:
    """Divide partidos completos cronológicamente en desarrollo, validación y confirmación."""
    ordered = sorted({(row.match_date, row.match_id) for row in transitions})
    development_end = int(len(ordered) * config.development_fraction)
    validation_end = development_end + int(len(ordered) * config.validation_fraction)
    return {"development": {item[1] for item in ordered[:development_end]}, "validation": {item[1] for item in ordered[development_end:validation_end]}, "confirmation": {item[1] for item in ordered[validation_end:]}}


def _by_split(transitions: list[Transition], match_ids: dict[str, set[int]]) -> dict[str, list[Transition]]:
    """Asigna cada transición a un único bloque por identidad de partido."""
    return {name: [row for row in transitions if row.match_id in ids] for name, ids in match_ids.items()}


class HierarchicalMarkovCalibrator(MarkovCalibrationSolver):
    """Estimador empírico con priors del nivel inmediatamente superior."""

    def __init__(self, config: MarkovCalibrationConfig) -> None:
        """Inicializa estructuras vacías y parámetros congelados."""
        self.config = config
        self._counts: dict[str, dict[tuple[Any, ...], Counter[str]]] = {}
        self._probabilities: dict[str, dict[tuple[Any, ...], dict[str, float]]] = {}

    def fit(self, transitions: list[Transition]) -> None:
        """Ajusta conteos y probabilidades desde desarrollo únicamente."""
        self._counts = {tier: defaultdict(Counter) for tier in TIERS}
        for row in transitions:
            for tier in TIERS: self._counts[tier][_key(tier, row)][row.next_state] += 1
        self._probabilities = {tier: {} for tier in reversed(TIERS)}
        for tier in reversed(TIERS): self._fit_tier(tier)

    def _fit_tier(self, tier: str) -> None:
        """Aplica smoothing hacia el prior del nivel padre."""
        for key, counts in self._counts[tier].items():
            parent = self._parent_probability(tier, key)
            support = sum(counts.values())
            self._probabilities[tier][key] = {state: (counts[state] + self.config.alpha * parent[state]) / (support + self.config.alpha) for state in STATES}

    def _parent_probability(self, tier: str, key: tuple[Any, ...]) -> dict[str, float]:
        """Obtiene prior padre o uniforme para el nivel global."""
        parent_tier = _parent_tier(tier)
        if parent_tier is None: return {state: 1.0 / len(STATES) for state in STATES}
        parent_key = self._parent_key(tier, key)
        return self._probabilities[parent_tier][parent_key]

    def _parent_key(self, tier: str, key: tuple[Any, ...]) -> tuple[Any, ...]:
        """Reduce una clave hija a la representación de su padre."""
        if tier == "team": return key[1:]
        if tier == "context": return (key[1], key[3])
        return (key[1],)

    def predict(self, transition: Transition) -> tuple[dict[str, float], str, int]:
        """Selecciona el contexto más específico con soporte suficiente."""
        for tier in TIERS:
            key, support = _key(tier, transition), sum(self._counts[tier].get(_key(tier, transition), Counter()).values())
            if support >= self._minimum_support(tier): return self._probabilities[tier][key], tier, support
        key = _key("global", transition)
        return self._probabilities["global"][key], "global", sum(self._counts["global"][key].values())

    def _minimum_support(self, tier: str) -> int:
        """Devuelve soporte mínimo por nivel, con global siempre disponible."""
        values = {"team": self.config.min_support_team, "context": self.config.min_support_context, "window": self.config.min_support_window, "global": 1}
        return values[tier]

    def export(self) -> dict[str, list[dict[str, Any]]]:
        """Expone matrices y soporte por tier sin ocultar celdas escasas."""
        output: dict[str, list[dict[str, Any]]] = {}
        for tier in TIERS:
            output[tier] = [{"context": list(key), "support": sum(counts.values()), "probabilities": self._probabilities[tier][key]} for key, counts in sorted(self._counts[tier].items(), key=lambda item: _key_text(item[0]))]
        return output


def _evaluate(rows: list[Transition], calibrator: HierarchicalMarkovCalibrator) -> dict[str, Any]:
    """Calcula likelihood y Brier agrupando el resumen final por partido."""
    by_match: dict[int, list[tuple[float, float, float, str, int]]] = defaultdict(list)
    for row in rows:
        probabilities, tier, support = calibrator.predict(row)
        global_probability = calibrator._probabilities["global"][_key("global", row)]
        by_match[row.match_id].append((_log_loss(probabilities, row.next_state), _brier(probabilities, row.next_state), _log_loss(global_probability, row.next_state), tier, support))
    return _evaluation_summary(by_match, rows, calibrator)


def _log_loss(probabilities: dict[str, float], actual: str) -> float:
    """Devuelve pérdida logarítmica con protección numérica explícita."""
    return -math.log(max(probabilities[actual], 1e-12))


def _brier(probabilities: dict[str, float], actual: str) -> float:
    """Calcula Brier multiclase para una transición observada."""
    return sum((probabilities[state] - float(state == actual)) ** 2 for state in STATES)


def _evaluation_summary(by_match: dict[int, list[tuple[float, float, float, str, int]]], rows: list[Transition], calibrator: HierarchicalMarkovCalibrator) -> dict[str, Any]:
    """Resume métricas de forma que la unidad de agregación sea el partido."""
    match_loss = [sum(item[0] for item in values) / len(values) for values in by_match.values()]
    match_brier = [sum(item[1] for item in values) / len(values) for values in by_match.values()]
    global_match_loss = [sum(item[2] for item in values) / len(values) for values in by_match.values()]
    tier_counts = Counter(item[3] for values in by_match.values() for item in values)
    return {"match_count": len(by_match), "transition_count": len(rows), "mean_match_log_loss": sum(match_loss) / len(match_loss), "mean_match_brier": sum(match_brier) / len(match_brier), "mean_match_global_log_loss": sum(global_match_loss) / len(global_match_loss), "tier_counts": dict(tier_counts)}


def _coverage(transitions: list[Transition], matrices: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Resume cobertura de transiciones y celdas bajo soporte mínimo."""
    return {"matches": len({row.match_id for row in transitions}), "transitions": len(transitions), "states_at_t": dict(Counter(row.state for row in transitions)), "states_at_t_plus_1": dict(Counter(row.next_state for row in transitions)), "cells": {tier: {"count": len(rows), "below_support": sum(item["support"] < 12 for item in rows)} for tier, rows in matrices.items()}}


def _audit(transitions: list[Transition], split_ids: dict[str, set[int]], build_audit: dict[str, int], matrices: dict[str, list[dict[str, Any]]], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Verifica separación temporal, normalización y política de evidencia."""
    overlaps = {f"{left}_{right}": len(split_ids[left] & split_ids[right]) for left, right in (("development", "validation"), ("development", "confirmation"), ("validation", "confirmation"))}
    normalized = all(abs(sum(item["probabilities"].values()) - 1.0) < 1e-9 for rows in matrices.values() for item in rows)
    return {"temporal_split": True, "match_overlap": overlaps, "all_matrices_normalized": normalized, "forbidden_target_fields_used": [], "strength_relative_available": False, "strength_relative_reason": "No existe todavía un artefacto canónico Dixon-Coles/Kalman por match.", "transition_build": build_audit, "validation_beats_or_equals_global": evaluation["validation"]["mean_match_log_loss"] <= evaluation["validation"]["mean_match_global_log_loss"]}


def _classification(audit: dict[str, Any], evaluation: dict[str, Any]) -> str:
    """Decide la salida sin autorizar simulación cuando falte evidencia."""
    valid = all(value == 0 for value in audit["match_overlap"].values()) and audit["all_matrices_normalized"]
    if not valid: return "rejected_for_revision"
    if not audit["validation_beats_or_equals_global"]: return "rejected_for_revision"
    return "ready_for_next_phase" if evaluation["confirmation"]["match_count"] else "insufficient_coverage"


def _predictions(rows: list[Transition], calibrator: HierarchicalMarkovCalibrator) -> list[dict[str, Any]]:
    """Serializa predicciones evaluadas para trazabilidad de backoff."""
    output = []
    for row in rows:
        probabilities, tier, support = calibrator.predict(row)
        output.append({"match_id": row.match_id, "window_index": row.window_index, "state": row.state, "next_state": row.next_state, "tier": tier, "support": support, "probabilities": probabilities})
    return output


def run(config: MarkovCalibrationConfig | None = None) -> dict[str, Any]:
    """Ejecuta Fase 03 con ajuste en desarrollo y evaluación temporal aislada."""
    active = config or MarkovCalibrationConfig()
    windows, labels = _load_rows()
    transitions, build_audit = build_transitions(windows, labels)
    split_ids = split_match_ids(transitions, active)
    split_rows = _by_split(transitions, split_ids)
    calibrator = HierarchicalMarkovCalibrator(active)
    calibrator.fit(split_rows["development"])
    matrices = calibrator.export()
    evaluation = {name: _evaluate(rows, calibrator) for name, rows in split_rows.items()}
    audit = _audit(transitions, split_ids, build_audit, matrices, evaluation)
    result = {"config": asdict(active), "transitions": transitions, "split_ids": split_ids, "matrices": matrices, "coverage": _coverage(transitions, matrices), "evaluation": evaluation, "audit": audit, "classification": _classification(audit, evaluation), "predictions": {name: _predictions(rows, calibrator) for name, rows in split_rows.items()}}
    _publish(result, _hash(windows), _hash(labels))
    LOGGER.info("Fase 03 markov_pre_match: %s", result["classification"])
    return result


def _publish(result: dict[str, Any], windows_hash: str, labels_hash: str) -> None:
    """Publica artefactos completos y reportes de decisión de Fase 03."""
    payloads = {"config.json": result["config"], "input_manifest.json": {"sources": ["phase_01_event_windows_v1/event_windows.json", "phase_02_state_labeling_v1/state_labels.json"], "windows_hash": windows_hash, "labels_hash": labels_hash, "match_split": {name: sorted(ids) for name, ids in result["split_ids"].items()}}, "transitions.json": [asdict(row) for row in result["transitions"]], "transition_matrices.json": result["matrices"], "coverage.json": result["coverage"], "metrics.json": result["evaluation"], "audit.json": {**result["audit"], "classification": result["classification"]}, "evaluation_predictions.json": result["predictions"]}
    for name, value in payloads.items(): _write(name, value)
    _write_reports(result)
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def _write_reports(result: dict[str, Any]) -> None:
    """Redacta interpretación y siguiente paso permitido sin promocionar modelo."""
    validation = result["evaluation"]["validation"]
    report = ["# Fase 03 — markov_pre_match v1", "", f"**Clasificación:** `{result['classification']}`", "", f"- partidos históricos: `{result['coverage']['matches']}`", f"- transiciones: `{result['coverage']['transitions']}`", f"- log-loss validación (promedio por partido): `{validation['mean_match_log_loss']:.6f}`", f"- baseline global (promedio por partido): `{validation['mean_match_global_log_loss']:.6f}`", "- fuerza relativa no incorporada: no existe artefacto canónico por match.", "- no se ejecutó simulación ni se generaron mercados."]
    validation_report = "\n".join(report + ["", "## Limitación", "La comparación es una evidencia de calibración; no constituye promoción ni prueba de rentabilidad."])
    (OUTPUT / "validation_report.md").write_text(validation_report + "\n", encoding="utf-8")
    next_step = "`pre_match_simulation v1`" if result["classification"] == "ready_for_next_phase" else "revisar especificación y conservar la evidencia negativa"
    (OUTPUT / "final_report.md").write_text("\n".join(report + [f"- siguiente paso permitido: {next_step}"]) + "\n", encoding="utf-8")


# Version: 1.0.0
# Created: 2026-07-26
