"""Calibra transiciones Markov jerárquicas sobre ventanas multi-liga.

La unidad de entrenamiento es ``estado_t -> estado_t+1`` dentro del mismo
partido y equipo. La partición es temporal por partido; liga y competición
forman parte del contexto para evitar mezclar tasas incompatibles.

Version: 1.0.0
Created: 2026-07-26
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGGER = logging.getLogger(__name__)
WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
LABELS = ROOT / "artifacts/phase_39_multileague_state_labeling_v1/state_labels.json"
OUTPUT = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")


@dataclass(frozen=True, slots=True)
class Config:
    """Parámetros congelados del calibrador global experimental."""

    version: str = "multileague_markov_calibration_v1"
    alpha: float = 32.0
    development_fraction: float = 0.60
    validation_fraction: float = 0.20
    min_support_team: int = 12
    min_support_competition: int = 10
    min_support_window: int = 8


@dataclass(frozen=True, slots=True)
class Transition:
    """Transición causal con contexto de liga y competición."""

    match_id: int
    match_date: str
    team_id: int
    is_home: bool
    window_index: int
    score_bucket: str
    league_slug: str
    competition_id: str
    state: str
    opponent_state: str
    next_state: str


def _hash(value: Any) -> str:
    """Calcula hash estable de estructuras serializables."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _load() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Carga ventanas y etiquetas validadas."""

    return json.loads(WINDOWS.read_text(encoding="utf-8")), json.loads(LABELS.read_text(encoding="utf-8"))


def _bucket(value: int) -> str:
    """Agrupa diferencia de goles al inicio de la ventana."""

    return "behind_2_plus" if value <= -2 else "behind_1" if value == -1 else "level" if value == 0 else "ahead_1" if value == 1 else "ahead_2_plus"


def _index(rows: list[dict[str, Any]]) -> dict[tuple[int, int, int], dict[str, Any]]:
    """Indexa filas por partido, equipo y ventana."""

    return {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in rows}


def _transitions(windows: list[dict[str, Any]], labels: list[dict[str, Any]]) -> tuple[list[Transition], dict[str, int]]:
    """Construye transiciones consecutivas sin usar datos posteriores al target."""

    win, lab = _index(windows), _index(labels)
    output, audit = [], Counter()
    for key, row in sorted(win.items()):
        if key[2] >= 5:
            continue
        next_key = (key[0], key[1], key[2] + 1)
        rival_key = (key[0], int(row["opponent_team_id"]), key[2])
        if next_key not in win or rival_key not in win:
            audit["missing_window"] += 1
            continue
        if any(item not in lab for item in (key, next_key, rival_key)):
            audit["missing_label"] += 1
            continue
        states = [str(lab[item]["state"]) for item in (key, rival_key, next_key)]
        if any(state not in STATES for state in states):
            audit["unknown_excluded"] += 1
            continue
        output.append(Transition(int(row["match_id"]), str(row["match_date"]), int(row["team_id"]), bool(row["is_home"]), key[2], _bucket(int(row["goal_difference_start"])), str(row["league_slug"]), str(row["competition_id"]), states[0], states[1], states[2]))
    return output, dict(audit)


def _split(transitions: list[Transition], config: Config) -> dict[str, set[int]]:
    """Divide partidos completos cronológicamente."""

    ordered = sorted({(row.match_date, row.match_id) for row in transitions})
    first = int(len(ordered) * config.development_fraction)
    second = first + int(len(ordered) * config.validation_fraction)
    return {"development": {item[1] for item in ordered[:first]}, "validation": {item[1] for item in ordered[first:second]}, "confirmation": {item[1] for item in ordered[second:]}}


def _key(tier: str, row: Transition) -> tuple[Any, ...]:
    """Construye contexto jerárquico incluyendo competición."""

    base = (row.state, row.opponent_state)
    if tier == "global":
        return base
    if tier == "window":
        return (row.league_slug, row.window_index, *base)
    context = (row.league_slug, row.is_home, row.window_index, row.score_bucket, *base)
    return context if tier == "competition" else (row.team_id, *context)


class Calibrator:
    """Calibrador Dirichlet con backoff team→competition→window→global."""

    tiers = ("team", "competition", "window", "global")

    def __init__(self, config: Config) -> None:
        """Inicializa conteos y probabilidades vacías."""

        self.config = config
        self.counts: dict[str, dict[tuple[Any, ...], Counter[str]]] = {}
        self.probabilities: dict[str, dict[tuple[Any, ...], dict[str, float]]] = {}

    def fit(self, rows: list[Transition]) -> None:
        """Ajusta sólo con el bloque de desarrollo."""

        self.counts = {tier: defaultdict(Counter) for tier in self.tiers}
        for row in rows:
            for tier in self.tiers:
                self.counts[tier][_key(tier, row)][row.next_state] += 1
        self.probabilities = {tier: {} for tier in reversed(self.tiers)}
        for tier in reversed(self.tiers):
            self._fit_tier(tier)

    def _fit_tier(self, tier: str) -> None:
        """Calcula smoothing hacia el prior del tier padre."""

        for key, counts in self.counts[tier].items():
            parent = self._parent(tier, key)
            support = sum(counts.values())
            self.probabilities[tier][key] = {state: (counts[state] + self.config.alpha * parent[state]) / (support + self.config.alpha) for state in STATES}

    def _parent(self, tier: str, key: tuple[Any, ...]) -> dict[str, float]:
        """Obtiene prior jerárquico o uniforme."""

        if tier == "global":
            return {state: 1 / len(STATES) for state in STATES}
        parent_tier = self.tiers[self.tiers.index(tier) + 1]
        parent_key = key[1:] if tier == "team" else (key[0], key[2], key[4], key[5]) if tier == "competition" else (key[2], key[3])
        return self.probabilities[parent_tier][parent_key]

    def predict(self, row: Transition) -> tuple[dict[str, float], str, int]:
        """Selecciona el tier más específico con soporte suficiente."""

        minimum = {"team": self.config.min_support_team, "competition": self.config.min_support_competition, "window": self.config.min_support_window, "global": 1}
        for tier in self.tiers:
            key = _key(tier, row)
            support = sum(self.counts[tier].get(key, Counter()).values())
            if support >= minimum[tier]:
                return self.probabilities[tier][key], tier, support
        key = _key("global", row)
        return self.probabilities["global"][key], "global", sum(self.counts["global"][key].values())

    def export(self) -> dict[str, list[dict[str, Any]]]:
        """Serializa matrices y soporte."""

        return {tier: [{"context": list(key), "support": sum(counts.values()), "probabilities": self.probabilities[tier][key]} for key, counts in sorted(self.counts[tier].items(), key=lambda item: json.dumps(item[0], default=str))] for tier in self.tiers}


def _evaluate(rows: list[Transition], model: Calibrator) -> dict[str, Any]:
    """Evalúa log-loss por partido, no por ventana IID."""

    by_match: dict[int, list[tuple[float, str, int]]] = defaultdict(list)
    for row in rows:
        probabilities, tier, support = model.predict(row)
        by_match[row.match_id].append((-math.log(max(probabilities[row.next_state], 1e-12)), tier, support))
    losses = [sum(item[0] for item in values) / len(values) for values in by_match.values()]
    return {"match_count": len(by_match), "transition_count": len(rows), "mean_match_log_loss": sum(losses) / len(losses) if losses else None, "tier_counts": dict(Counter(item[1] for values in by_match.values() for item in values))}


def _write(result: dict[str, Any]) -> None:
    """Publica matrices, transiciones, métricas y auditoría."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config": result["config"], "transitions": [asdict(row) for row in result["transitions"]], "split_ids": result["split_ids"], "transition_matrices": result["matrices"], "coverage": result["coverage"], "metrics": result["metrics"], "audit": result["audit"]}
    for name, value in payloads.items():
        (OUTPUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (OUTPUT / "final_report.md").write_text(result["final_report"] + "\n", encoding="utf-8")
    hashes = {path.name: _hash(path.read_bytes()) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta calibración temporal multi-liga sin publicar predicciones."""

    windows, labels = _load()
    config = Config()
    transitions, build_audit = _transitions(windows, labels)
    split_ids = _split(transitions, config)
    split = {name: [row for row in transitions if row.match_id in ids] for name, ids in split_ids.items()}
    model = Calibrator(config)
    model.fit(split["development"])
    metrics = {name: _evaluate(rows, model) for name, rows in split.items()}
    matrices = model.export()
    overlaps = {f"{a}_{b}": len(split_ids[a] & split_ids[b]) for a, b in (("development", "validation"), ("development", "confirmation"), ("validation", "confirmation"))}
    normalized = all(abs(sum(item["probabilities"].values()) - 1) < 1e-9 for rows in matrices.values() for item in rows)
    audit = {"classification": "ready_for_multileague_simulation" if not any(overlaps.values()) and normalized and metrics["confirmation"]["match_count"] else "rejected_for_revision", "match_overlap": overlaps, "all_matrices_normalized": normalized, "transition_build": build_audit, "targets_used": False, "router_modified": False, "official_model_modified": False}
    result = {"config": asdict(config), "transitions": transitions, "split_ids": split_ids, "matrices": matrices, "metrics": metrics, "coverage": {"matches": len({row.match_id for row in transitions}), "transitions": len(transitions), "leagues": len({row.league_slug for row in transitions}), "states": dict(Counter(row.state for row in transitions))}, "audit": audit, "final_report": f"# Fase 40 — calibración Markov multi-liga\n\n**Clasificación:** `{audit['classification']}`\n\n- partidos: `{len({row.match_id for row in transitions})}`\n- transiciones: `{len(transitions)}`\n- ligas: `{len({row.league_slug for row in transitions})}`\n- desarrollo/validación/confirmación: `{len(split['development'])}/{len(split['validation'])}/{len(split['confirmation'])}`\n- log-loss validación: `{metrics['validation']['mean_match_log_loss']}`\n- log-loss confirmación: `{metrics['confirmation']['mean_match_log_loss']}`\n- predicciones de apuestas: `False`\n"}
    _write(result)
    LOGGER.info("Fase 40 Markov multi-liga: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 40."""

    return 0 if run()["audit"]["classification"] == "ready_for_multileague_simulation" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
