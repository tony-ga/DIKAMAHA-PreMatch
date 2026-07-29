"""Simula trayectorias pre-match de estados para el corpus multi-liga.

Esta fase no inventa intensidades de gol para ligas nuevas. Produce un
artefacto experimental de dinámica de estados que después podrá fusionarse
con Dixon-Coles/Kalman cuando exista un prior estructural por partido.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGGER = logging.getLogger(__name__)
MARKOV = ROOT / "artifacts/phase_40_multileague_markov_calibration_v1"
OUTPUT = ROOT / "artifacts/phase_41_multileague_state_simulation_v1"
STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")
TIERS = ("competition", "window", "global")


@dataclass(frozen=True, slots=True)
class Config:
    """Parámetros congelados de la simulación de estados."""

    version: str = "multileague_state_simulation_v1"
    simulations_per_league: int = 5000
    windows: int = 6
    seed: int = 20260726
    alpha_prior: float = 32.0
    min_support_competition: int = 10
    min_support_window: int = 8


def _load(name: str) -> Any:
    """Carga un artefacto JSON de Fase 40."""

    return json.loads((MARKOV / name).read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    """Calcula un hash estable de una estructura serializable."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _split_development(rows: list[dict[str, Any]], config: Config) -> set[int]:
    """Reproduce la partición temporal de desarrollo de Fase 40."""

    ordered = sorted({(str(row["match_date"]), int(row["match_id"])) for row in rows})
    cutoff = int(len(ordered) * 0.60)
    return {match_id for _, match_id in ordered[:cutoff]}


def _smooth(counts: Counter[str], parent: dict[str, float], alpha: float) -> dict[str, float]:
    """Aplica shrinkage Dirichlet hacia el prior padre."""

    total = sum(counts.values()) + alpha
    return {state: (counts[state] + alpha * parent[state]) / total for state in STATES}


def _priors(rows: list[dict[str, Any]], development: set[int], config: Config) -> tuple[dict[str, dict[str, float]], dict[str, dict[bool, dict[str, float]]]]:
    """Estima priors iniciales por liga y localía sólo con desarrollo."""

    global_counts: dict[bool, Counter[str]] = {True: Counter(), False: Counter()}
    league_counts: dict[str, dict[bool, Counter[str]]] = defaultdict(lambda: {True: Counter(), False: Counter()})
    for row in rows:
        if int(row["match_id"]) not in development or int(row["window_index"]) != 0:
            continue
        venue, state, league = bool(row["is_home"]), str(row["state"]), str(row["league_slug"])
        if state not in STATES:
            continue
        global_counts[venue][state] += 1
        league_counts[league][venue][state] += 1
    uniform = {state: 1.0 / len(STATES) for state in STATES}
    global_prior = {str(venue): _smooth(counts, uniform, config.alpha_prior) for venue, counts in global_counts.items()}
    priors = {league: {venue: _smooth(counts[venue], global_prior[str(venue)], config.alpha_prior) for venue in (True, False)} for league, counts in league_counts.items()}
    return global_prior, priors


def _matrix_index(matrices: dict[str, list[dict[str, Any]]]) -> dict[str, dict[tuple[Any, ...], dict[str, Any]]]:
    """Indexa las matrices jerárquicas publicadas por Fase 40."""

    return {tier: {tuple(item["context"]): item for item in values} for tier, values in matrices.items()}


def _sample(probabilities: dict[str, float], rng: random.Random) -> str:
    """Muestrea un estado con el orden de estados congelado."""

    threshold, cumulative = rng.random(), 0.0
    for state in STATES:
        cumulative += float(probabilities[state])
        if threshold <= cumulative:
            return state
    return STATES[-1]


def _stable_seed(slug: str, seed: int) -> int:
    """Deriva una semilla estable e independiente para cada liga."""

    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()
    return seed + int(digest[:8], 16)


def _transition(index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], league: str, venue: bool, window: int, state: str, opponent: str, config: Config) -> tuple[dict[str, float], str]:
    """Selecciona matriz por liga/ventana y aplica backoff global."""

    competition_key = (league, venue, window, "level", state, opponent)
    window_key = (league, window, state, opponent)
    for tier, key, minimum in (("competition", competition_key, config.min_support_competition), ("window", window_key, config.min_support_window), ("global", (state, opponent), 1)):
        item = index[tier].get(key)
        if item and int(item["support"]) >= minimum:
            return {name: float(value) for name, value in item["probabilities"].items()}, tier
    return {state_name: 1.0 / len(STATES) for state_name in STATES}, "uniform"


def _simulate_league(league: str, global_prior: dict[str, dict[str, float]], priors: dict[str, dict[bool, dict[str, float]]], index: dict[str, dict[tuple[Any, ...], dict[str, Any]]], config: Config) -> dict[str, Any]:
    """Simula estados de ambos equipos para una liga arquetípica."""

    rng = random.Random(_stable_seed(league, config.seed))
    prior = priors.get(league, {True: global_prior["True"], False: global_prior["False"]})
    state_counts = {window: {True: Counter(), False: Counter()} for window in range(config.windows)}
    tier_counts: Counter[str] = Counter()
    for _ in range(config.simulations_per_league):
        states = {venue: _sample(prior[venue], rng) for venue in (True, False)}
        for window in range(config.windows):
            for venue in (True, False):
                state_counts[window][venue][states[venue]] += 1
            if window == config.windows - 1:
                continue
            next_states = {}
            for venue in (True, False):
                probabilities, tier = _transition(index, league, venue, window, states[venue], states[not venue], config)
                next_states[venue] = _sample(probabilities, rng)
                tier_counts[tier] += 1
            states = next_states
    return {"league_slug": league, "simulation_count": config.simulations_per_league, "state_distribution": _distributions(state_counts, config), "transition_tier_counts": dict(tier_counts)}


def _distributions(counts: dict[int, dict[bool, Counter[str]]], config: Config) -> dict[str, dict[str, dict[str, float]]]:
    """Convierte conteos de simulación en probabilidades por ventana."""

    denominator = float(config.simulations_per_league)
    return {str(window): {"home": {state: counts[window][True][state] / denominator for state in STATES}, "away": {state: counts[window][False][state] / denominator for state in STATES}} for window in counts}


def _audit(results: list[dict[str, Any]], matrices: dict[str, list[dict[str, Any]]], development: set[int], source_leagues: set[str], config: Config) -> dict[str, Any]:
    """Verifica determinismo lógico, normalización y ausencia de targets."""

    normalized = all(abs(sum(values) - 1.0) < 1e-9 for result in results for window in result["state_distribution"].values() for venue in ("home", "away") for values in [window[venue].values()])
    simulated = {str(result["league_slug"]) for result in results}
    return {"classification": "ready_for_multileague_structural_fusion" if results and normalized else "rejected_for_revision", "deterministic_seed": config.seed, "development_match_count": len(development), "source_leagues": len(source_leagues), "leagues_simulated": len(results), "leagues_excluded_without_development": sorted(source_leagues - simulated), "probabilities_normalized": normalized, "matrices_loaded": sorted(matrices), "target_events_used": False, "target_scores_used": False, "target_match_ids_used": [], "official_router_modified": False, "markets_promoted": False}


def _publish(result: dict[str, Any], source: dict[str, Any], development: set[int]) -> None:
    """Publica resultados, provenance, auditoría y hashes."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {"config.json": result["config"], "league_simulations.json": result["league_simulations"], "coverage.json": result["coverage"], "audit.json": result["audit"], "input_manifest.json": {"phase_40_config_hash": _hash(source["config"]), "phase_40_matrices_hash": _hash(source["matrices"]), "phase_40_transitions_hash": _hash(source["transitions"]), "development_split_hash": _hash(sorted(development))}}
    for name, value in payloads.items():
        target = OUTPUT / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(target)
    report = ["# Fase 41 — simulación de estados multi-liga", "", f"**Clasificación:** `{result['audit']['classification']}`", "", f"- ligas fuente: `{result['coverage']['source_leagues']}`", f"- ligas simuladas: `{result['coverage']['leagues']}`", f"- ligas retenidas: `{', '.join(result['coverage']['excluded_leagues']) or 'ninguna'}`", f"- simulaciones por liga: `{result['config']['simulations_per_league']}`", f"- ventanas por trayectoria: `{result['config']['windows']}`", "- goles y mercados: `no calculados en esta fase`", "- eventos/targets del partido simulado: `no utilizados`", "- siguiente paso: `fusión estructural Dixon-Coles/Kalman y evaluación OOS`."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    (OUTPUT / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


def run(config: Config | None = None) -> dict[str, Any]:
    """Ejecuta la simulación experimental usando sólo el bloque de desarrollo."""

    active = config or Config()
    source = {"config": _load("config.json"), "matrices": _load("transition_matrices.json"), "transitions": _load("transitions.json")}
    development = _split_development(source["transitions"], active)
    global_prior, priors = _priors(source["transitions"], development, active)
    index = _matrix_index(source["matrices"])
    leagues = sorted(priors)
    simulations = [_simulate_league(league, global_prior, priors, index, active) for league in leagues]
    source_leagues = {str(row["league_slug"]) for row in source["transitions"]}
    audit = _audit(simulations, source["matrices"], development, source_leagues, active)
    result = {"config": asdict(active), "league_simulations": simulations, "coverage": {"source_leagues": len(source_leagues), "leagues": len(simulations), "excluded_leagues": audit["leagues_excluded_without_development"], "states": list(STATES), "development_matches": len(development), "target_match_ids": 0}, "audit": audit}
    _publish(result, source, development)
    LOGGER.info("Fase 41 simulación multi-liga: %s", audit["classification"])
    return result


def main() -> int:
    """Ejecuta Fase 41 desde línea de comandos."""

    return 0 if run()["audit"]["classification"] == "ready_for_multileague_structural_fusion" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-26
