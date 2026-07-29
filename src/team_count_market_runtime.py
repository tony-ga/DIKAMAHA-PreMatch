"""Runtime causal para mercados agregados pre-match en modo shadow.

Requirements:
    joblib>=1.4
    numpy>=2.0

Version: 1.4.0
Created: 2026-07-28
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from src.team_count_markets import (
    negative_binomial_distribution,
    negative_binomial_over_probability,
)
from src.team_market_markov import TeamMarketMarkov

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = ROOT / "artifacts/phase_84a_team_count_markets"
DEFAULT_MARKOV_ARTIFACT = ROOT / "artifacts/phase_88_team_market_markov"
APPROVED_MARKETS = frozenset({
    "home_corners_over_4_5", "away_corners_over_4_5",
    "home_shots_over_10_5", "away_shots_over_10_5",
    "shots_on_target_total_over_7_5",
})
MARKOV_APPROVED_MARKETS = frozenset({
    "away_shots_second_half_over_5_5",
    "home_corners_second_half_over_2_5",
    "home_shots_first_half_over_5_5",
    "home_shots_second_half_over_5_5",
})
MARKOV_BASELINE_FALLBACKS = frozenset({
    "home_corners_second_half_over_2_5",
})
MARKET_METADATA: dict[str, tuple[str, str, str, float, str]] = {
    "home_corners_over_4_5": ("corners", "home", "full_match", 4.5, "phase84a"),
    "away_corners_over_4_5": ("corners", "away", "full_match", 4.5, "phase84a"),
    "home_shots_over_10_5": ("shots", "home", "full_match", 10.5, "phase84a"),
    "away_shots_over_10_5": ("shots", "away", "full_match", 10.5, "phase84a"),
    "shots_on_target_total_over_7_5": (
        "shots_on_target", "total", "full_match", 7.5, "phase84a"),
    "away_shots_second_half_over_5_5": (
        "shots", "away", "second_half", 5.5, "phase88_markov"),
    "home_corners_second_half_over_2_5": (
        "corners", "home", "second_half", 2.5, "phase88_markov"),
    "home_shots_first_half_over_5_5": (
        "shots", "home", "first_half", 5.5, "phase88_markov"),
    "home_shots_second_half_over_5_5": (
        "shots", "home", "second_half", 5.5, "phase88_markov"),
}
LADDER_MAXIMUMS = {
    "corners": {"half": 7, "full_match": 13},
    "shots": {"half": 15, "full_match": 29},
    "yellow_cards": {"half": 6, "full_match": 11},
    "shots_on_target": {"full_match": 17},
}
RECOMMENDATION_MIN = 0.55
RECOMMENDATION_MAX = 0.80
RECOMMENDATION_MIN_TEAM_EDGE = 0.02
RECOMMENDATION_LIMIT = 6
VISIBLE_LINE_MIN = 1.5
VISIBLE_LINE_MAX = 9.5
VISIBLE_GRID_SIZE = 3


class TeamCountMarketProvider(ABC):
    """Puerto para sidecars pre-match de mercados agregados."""

    @abstractmethod
    def predict(
        self, rows: tuple[dict[str, Any], ...], request: Any, source: Path,
    ) -> dict[str, Any]:
        """Calcula mercados shadow sin alterar la salida oficial."""


def _sha(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_ts(value: str) -> Any:
    """Delega la normalización temporal al contrato universal."""

    from src.universal_prematch import _parse_ts

    return _parse_ts(value)


def _metric_target(rows: list[dict[str, Any]], spec: dict[str, Any]) -> float:
    """Agrega un conteo de equipo completo o de primera mitad."""

    selected = rows
    if bool(spec["first_half_only"]):
        selected = [row for row in rows if int(row["window_index"]) < 3]
    return float(sum(_runtime_count(row, spec) for row in selected))


def _runtime_count(
    row: dict[str, Any], spec: dict[str, Any],
) -> float:
    """Replica la semántica comercial congelada de tiros."""

    value = float(row.get(spec["source_field"], 0.0) or 0.0)
    if spec["name"] in {"shots", "shots_on_target"}:
        value += float(row.get("goals", 0.0) or 0.0)
    return value


def _aggregate_match(
    rows: list[dict[str, Any]], metrics: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Agrega ventanas por equipo para un partido completo."""

    home = [row for row in rows if bool(row["is_home"])]
    away = [row for row in rows if not bool(row["is_home"])]
    if not home or not away:
        return None
    first = rows[0]
    return {
        "match_id": int(first["match_id"]),
        "match_date": str(first["match_date"]),
        "league_slug": str(first["league_slug"]),
        "home_team_id": int(home[0]["team_id"]),
        "away_team_id": int(away[0]["team_id"]),
        "home": {spec["name"]: _metric_target(home, spec) for spec in metrics},
        "away": {spec["name"]: _metric_target(away, spec) for spec in metrics},
    }


def _historical_matches(
    rows: tuple[dict[str, Any], ...], request: Any,
    metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Selecciona y agrega exclusivamente historia causal de la liga."""

    cutoff = _parse_ts(str(request.kickoff_ts))
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("league_slug")) != str(request.league_slug):
            continue
        if int(row["match_id"]) == request.match_id:
            continue
        if _parse_ts(str(row["match_date"])) < cutoff:
            grouped[int(row["match_id"])].append(row)
    matches = [_aggregate_match(values, metrics) for values in grouped.values()]
    return sorted((row for row in matches if row), key=lambda row: (
        str(row["match_date"]), int(row["match_id"])))


def _add(values: dict[Any, list[float]], key: Any, *increments: float) -> None:
    """Acumula un vector histórico."""

    current = values.setdefault(key, [0.0] * len(increments))
    for index, increment in enumerate(increments):
        current[index] += increment


def _histories(
    matches: list[dict[str, Any]], metrics: list[dict[str, Any]],
) -> tuple[dict[Any, list[float]], dict[Any, list[float]]]:
    """Construye perfiles de equipo y liga previos al partido solicitado."""

    team: dict[Any, list[float]] = {}
    league: dict[Any, list[float]] = {}
    for match in matches:
        for home in (True, False):
            side, rival = ("home", "away") if home else ("away", "home")
            own_id = int(match[f"{side}_team_id"])
            rival_id = int(match[f"{rival}_team_id"])
            for spec in metrics:
                name = str(spec["name"])
                own, conceded = match[side][name], match[rival][name]
                _add(team, (match["league_slug"], own_id, name),
                     own, conceded, 1.0)
                _add(league, (match["league_slug"], home, name), own, 1.0)
    return team, league


def _profile(
    own: list[float], rival: list[float], league: list[float], default: float,
) -> list[float]:
    """Replica el smoothing causal congelado en Fase 84A."""

    own_n, rival_n, league_n = own[2], rival[2], league[1]
    return [
        (own[0] + 5.0 * default) / (own_n + 5.0),
        (own[1] + 5.0 * default) / (own_n + 5.0),
        (rival[0] + 5.0 * default) / (rival_n + 5.0),
        (rival[1] + 5.0 * default) / (rival_n + 5.0),
        (league[0] + 20.0 * default) / (league_n + 20.0),
        math.log1p(own_n), math.log1p(rival_n),
    ]


def _features(
    request: Any, home: bool, metrics: list[dict[str, Any]],
    team: dict[Any, list[float]], league: dict[Any, list[float]],
) -> tuple[np.ndarray, dict[str, float]]:
    """Materializa features y baselines de una orientación."""

    own = request.home_team_id if home else request.away_team_id
    rival = request.away_team_id if home else request.home_team_id
    values, baselines = [float(home)], {}
    for spec in metrics:
        name, default = str(spec["name"]), float(spec["safe_default"])
        own_stats = team.get((request.league_slug, own, name), [0.0] * 3)
        rival_stats = team.get((request.league_slug, rival, name), [0.0] * 3)
        league_stats = league.get(
            (request.league_slug, home, name), [0.0] * 2)
        values.extend(_profile(own_stats, rival_stats, league_stats, default))
        baselines[name] = (league_stats[0] + 20.0 * default) / (
            league_stats[1] + 20.0)
    return np.asarray([values], dtype=float), baselines


def _expected(
    models: dict[str, Any], weights: dict[str, float],
    features: np.ndarray, baselines: dict[str, float],
) -> dict[str, float]:
    """Aplica modelos congelados y mezcla seleccionada."""

    output = {}
    for name, model in models.items():
        raw = float(model.predict(features)[0])
        weight = float(weights[name])
        output[name] = weight * raw + (1.0 - weight) * baselines[name]
    return output


def _combined_phi(
    metric: str, side: str, expected: dict[str, dict[str, float]],
    dispersions: dict[str, float],
) -> float:
    """Conserva varianza al sumar conteos home y away."""

    phi = float(dispersions[metric])
    if side != "total":
        return phi
    home, away = expected["home"][metric], expected["away"][metric]
    total = max(home + away, 1e-9)
    return max(phi * (home * home + away * away) / (total * total), 1e-8)


def _market_probability(
    definition: list[Any], expected: dict[str, dict[str, float]],
    dispersions: dict[str, float],
) -> float:
    """Convierte intensidades de conteo en probabilidad comercial."""

    metric, side, line = str(definition[0]), str(definition[1]), int(definition[2])
    if side == "total":
        rate = expected["home"][metric] + expected["away"][metric]
    else:
        rate = expected[side][metric]
    phi = _combined_phi(metric, side, expected, dispersions)
    return negative_binomial_over_probability(rate, phi, line)


def _probabilities(
    enabled: list[str], config: dict[str, Any],
    expected: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Calcula las líneas aprobadas para un bloque de tasas."""

    return {
        name: _market_probability(
            config["market_lines"][name], expected, config["dispersions"])
        for name in enabled
    }


class ArtifactTeamCountMarketProvider(TeamCountMarketProvider):
    """Adaptador del artefacto congelado de Fase 84A."""

    def __init__(
        self, artifact_path: Path | None = None,
        markov_artifact_path: Path | None = None,
    ) -> None:
        """Carga y valida configuración y modelos locales."""

        self._path = artifact_path or DEFAULT_ARTIFACT
        self._markov_path = markov_artifact_path or DEFAULT_MARKOV_ARTIFACT
        self._loaded: tuple[dict[str, Any], dict[str, Any], list[str]] | None = None
        self._markov_loaded: tuple[
            dict[str, Any], TeamMarketMarkov, list[str],
        ] | None = None
        self._matches_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def _load(self) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Carga artefactos únicamente tras verificar su integridad."""

        if self._loaded is not None:
            return self._loaded
        config_path, audit_path = self._path / "config.json", self._path / "audit.json"
        hashes = json.loads((self._path / "hashes.json").read_text(encoding="utf-8"))
        models_path = self._path / "models.joblib"
        if _sha(models_path) != hashes.get("models.joblib"):
            raise ValueError("team_market_model_hash_mismatch")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        enabled = sorted(audit["enabled_shadow_markets"])
        if set(enabled) != APPROVED_MARKETS:
            raise ValueError("team_market_approval_contract_mismatch")
        self._loaded = config, joblib.load(models_path), enabled
        return self._loaded

    def _matches(
        self, rows: tuple[dict[str, Any], ...], request: Any, source: Path,
        metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Cachea agregados y aplica cutoff y exclusión por solicitud."""

        key = (str(source), str(request.league_slug))
        if key not in self._matches_cache:
            neutral = type("Request", (), {
                "league_slug": request.league_slug,
                "kickoff_ts": "2999-01-01T00:00:00+00:00",
                "match_id": None,
            })()
            self._matches_cache[key] = _historical_matches(
                rows, neutral, metrics)
        cutoff = _parse_ts(str(request.kickoff_ts))
        return [
            row for row in self._matches_cache[key]
            if int(row["match_id"]) != request.match_id
            and _parse_ts(str(row["match_date"])) < cutoff
        ]

    def _load_markov(
        self,
    ) -> tuple[dict[str, Any], TeamMarketMarkov, list[str]]:
        """Carga la cadena Fase 88 y valida hash y líneas."""

        if self._markov_loaded is not None:
            return self._markov_loaded
        config = json.loads(
            (self._markov_path / "config.json").read_text(encoding="utf-8"))
        hashes = json.loads(
            (self._markov_path / "hashes.json").read_text(encoding="utf-8"))
        model_path = self._markov_path / "team_market_markov.joblib"
        if _sha(model_path) != hashes.get(model_path.name):
            raise ValueError("team_market_markov_hash_mismatch")
        enabled = sorted(config["enabled_shadow_markets"])
        if set(enabled) != MARKOV_APPROVED_MARKETS:
            raise ValueError("team_market_markov_approval_mismatch")
        model = joblib.load(model_path)
        if not isinstance(model, TeamMarketMarkov):
            raise TypeError("team_market_markov_type_mismatch")
        self._markov_loaded = config, model, enabled
        return self._markov_loaded

    def _markov_prediction(
        self, request: Any,
    ) -> tuple[
        dict[str, float], dict[str, float], dict[str, Any],
        list[dict[str, Any]], dict[str, Any],
    ]:
        """Emite sólo las tres líneas Markov causalmente válidas."""

        config, model, enabled = self._load_markov()
        cutoff = _parse_ts(str(config["training_cutoff_ts"]))
        if _parse_ts(str(request.kickoff_ts)) <= cutoff:
            raise ValueError("kickoff_not_after_markov_training_cutoff")
        match = {
            "league_slug": str(request.league_slug),
            "home_team_id": int(request.home_team_id),
            "away_team_id": int(request.away_team_id),
        }
        trajectories = model.predict_match(match)
        probabilities, baselines, expected = _trajectory_outputs(
            trajectories, enabled)
        for name in MARKOV_BASELINE_FALLBACKS:
            if name in probabilities:
                probabilities[name] = baselines[name]
        distributional = _distributional_view(trajectories)
        return probabilities, baselines, expected, distributional, {
            "status": "available", "phase": "88",
            "baseline_fallback_markets": sorted(MARKOV_BASELINE_FALLBACKS),
            "model_sha256": _sha(
                self._markov_path / "team_market_markov.joblib"),
            "training_cutoff_ts": str(config["training_cutoff_ts"])}

    def _safe_markov(
        self, request: Any,
    ) -> tuple[
        dict[str, float], dict[str, float], dict[str, Any],
        list[dict[str, Any]], dict[str, Any],
    ]:
        """Mantiene Fase 84A si el artefacto Markov no es utilizable."""

        try:
            return self._markov_prediction(request)
        except (FileNotFoundError, KeyError, TypeError, ValueError, OSError) as error:
            LOGGER.warning("team_market_markov_shadow_unavailable: %s", error)
            return {}, {}, {}, [], {
                "status": "shadow_unavailable",
                "reason": type(error).__name__}

    def predict(
        self, rows: tuple[dict[str, Any], ...], request: Any, source: Path,
    ) -> dict[str, Any]:
        """Emite sólo las líneas aprobadas o un fallback explícito."""

        try:
            return self._predict(rows, request, source)
        except (FileNotFoundError, KeyError, TypeError, ValueError, OSError) as error:
            LOGGER.warning("team_market_shadow_unavailable: %s", error)
            return self.unavailable(type(error).__name__)

    def _predict(
        self, rows: tuple[dict[str, Any], ...], request: Any, source: Path,
    ) -> dict[str, Any]:
        """Ejecuta inferencia causal con el contrato congelado."""

        config, models, enabled = self._load()
        metrics = list(config["metrics"])
        matches = self._matches(rows, request, source, metrics)
        team, league = _histories(matches, metrics)
        expected, baseline_expected = {}, {}
        for home, side in ((True, "home"), (False, "away")):
            features, baselines = _features(request, home, metrics, team, league)
            expected[side] = _expected(
                models, config["model_weights"], features, baselines)
            baseline_expected[side] = baselines
        probabilities = _probabilities(enabled, config, expected)
        baselines = _probabilities(enabled, config, baseline_expected)
        markov, markov_baselines, markov_expected, ladders, markov_meta = (
            self._safe_markov(request))
        ladders.extend(_count_distributional_view(
            expected, baseline_expected, config["dispersions"]))
        probabilities.update(markov)
        baselines.update(markov_baselines)
        combined_enabled = sorted(set(enabled) | set(markov))
        return self._payload(
            request, source, matches, expected, baseline_expected,
            probabilities, baselines,
            combined_enabled, markov_expected, ladders, config["dispersions"],
            markov_meta)

    def _payload(
        self, request: Any, source: Path, matches: list[dict[str, Any]],
        expected: dict[str, dict[str, float]],
        baseline_expected: dict[str, dict[str, float]],
        probabilities: dict[str, float],
        baselines: dict[str, float], enabled: list[str],
        markov_expected: dict[str, Any], ladders: list[dict[str, Any]],
        dispersions: dict[str, float],
        markov_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Compone el bloque público de inferencia shadow."""

        payload = {
            "status": "experimental_shadow_not_promoted",
            "enabled_markets": enabled,
            "probabilities": probabilities,
            "baseline_probabilities": baselines,
            "expected_counts": expected,
            "markov_expected_counts": markov_expected,
            "distributional_market_view": ladders,
            "recommended_market_view": _recommendations(ladders),
            "bounded_market_grid_view": _bounded_market_grid(ladders),
            "global_market_view": _global_market_view(
                expected, baseline_expected, dispersions),
            "user_market_view": _user_market_view(
                enabled, probabilities, baselines),
        }
        payload["provenance"] = _provenance(
            self._path, request, source, matches, markov_meta)
        payload["audit"] = _distributional_audit(ladders)
        return payload

    @staticmethod
    def unavailable(reason: str) -> dict[str, Any]:
        """Devuelve degradación segura sin mercados inventados."""

        return {
            "status": "shadow_unavailable", "reason": reason,
            "enabled_markets": [], "probabilities": {},
            "user_market_view": [],
            "distributional_market_view": [],
            "recommended_market_view": [],
            "bounded_market_grid_view": [],
            "global_market_view": [],
            "audit": {"official_output_unchanged": True,
                      "target_match_data_used": False},
        }


def _user_market_view(
    enabled: list[str], probabilities: dict[str, float],
    baselines: dict[str, float],
) -> list[dict[str, Any]]:
    """Construye el contrato legible de interfaz."""

    output = []
    for key in sorted(enabled):
        metric, side, period, line, source = MARKET_METADATA[key]
        if key in MARKOV_BASELINE_FALLBACKS:
            source = "phase88_league_venue_fallback"
        output.append({
            "key": key, "metric": metric, "team_side": side,
            "period": period, "line": line,
            "probability": float(probabilities[key]),
            "baseline_probability": float(baselines[key]),
            "source_model": source,
            "status": "experimental_shadow_not_promoted",
        })
    return output


def _distributional_view(
    trajectories: dict[str, Any],
) -> list[dict[str, Any]]:
    """Construye PMF y escaleras por equipo, métrica y periodo."""

    output = []
    for side in ("home", "away"):
        trajectory = trajectories[side]
        for metric in ("corners", "shots", "yellow_cards"):
            for period in ("first_half", "second_half", "full_match"):
                key = f"{metric}_{period}"
                output.append(_distributional_row(
                    metric, side, period, trajectory.distributions[key],
                    trajectory.baseline_distributions[key]))
    return output


def _trajectory_outputs(
    trajectories: dict[str, Any], enabled: list[str],
) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    """Combina salidas binarias heredadas de ambas orientaciones."""

    probabilities = {
        name: value for trajectory in trajectories.values()
        for name, value in trajectory.probabilities.items() if name in enabled}
    baselines = {
        name: value for trajectory in trajectories.values()
        for name, value in trajectory.baselines.items() if name in enabled}
    expected = {
        side: trajectory.expected_counts
        for side, trajectory in trajectories.items()}
    return probabilities, baselines, expected


def _provenance(
    path: Path, request: Any, source: Path, matches: list[dict[str, Any]],
    markov_meta: dict[str, Any],
) -> dict[str, Any]:
    """Compone procedencia causal de las distribuciones."""

    return {
        "phase": "84A+88+102", "integration_phase": "102",
        "model_sha256": _sha(path / "models.joblib"),
        "source": str(source), "history_matches": len(matches),
        "cutoff_ts": str(request.kickoff_ts),
        "team_market_markov": markov_meta,
    }


def _distributional_audit(
    ladders: list[dict[str, Any]],
) -> dict[str, Any]:
    """Declara invariantes de la salida shadow."""

    return {
        "official_output_unchanged": True,
        "target_match_data_used": False, "cutoff_causal": True,
        "target_match_excluded": True,
        "distributional_markets_promoted": False,
        "over_under_monotonic": _all_monotonic(ladders),
    }


def _distributional_row(
    metric: str, side: str, period: str,
    distribution: dict[int, float], baseline: dict[int, float],
) -> dict[str, Any]:
    """Materializa una distribución y su escalera coherente."""

    ladder = _ladder(metric, period, distribution, baseline)
    expected = sum(count * value for count, value in distribution.items())
    mode = max(distribution.items(), key=lambda item: item[1])[0]
    return {
        "key": f"{side}_{metric}_{period}", "metric": metric,
        "team_side": side, "period": period,
        "expected_count": float(expected), "most_likely_count": int(mode),
        "probability_mass": [
            {"count": int(count), "probability": float(value)}
            for count, value in sorted(distribution.items())],
        "baseline_probability_mass": [
            {"count": int(count), "probability": float(value)}
            for count, value in sorted(baseline.items())],
        "ladder": ladder, "source_model": "phase88_markov_distribution",
        "baseline_model": "phase88_league_venue_distribution",
        "status": "experimental_shadow_not_promoted",
    }


def _count_distributional_view(
    expected: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    dispersions: dict[str, float],
) -> list[dict[str, Any]]:
    """Añade tiros a puerta por equipo y total desde Fase 84A."""

    metric, output = "shots_on_target", []
    for side in ("home", "away", "total"):
        mean = _side_rate(expected, metric, side)
        base_mean = _side_rate(baseline, metric, side)
        phi = _combined_phi(metric, side, expected, dispersions)
        base_phi = _combined_phi(metric, side, baseline, dispersions)
        row = _distributional_row(
            metric, side, "full_match",
            negative_binomial_distribution(mean, phi),
            negative_binomial_distribution(base_mean, base_phi))
        row.update({
            "source_model": "phase84a_negative_binomial",
            "baseline_model": "phase84a_league_venue_negative_binomial",
        })
        output.append(row)
    return output


def _side_rate(
    expected: dict[str, dict[str, float]], metric: str, side: str,
) -> float:
    """Obtiene la intensidad de equipo o suma total."""

    if side == "total":
        return float(expected["home"][metric] + expected["away"][metric])
    return float(expected[side][metric])


def _ladder(
    metric: str, period: str, distribution: dict[int, float],
    baseline: dict[int, float],
) -> list[dict[str, float]]:
    """Deriva over/under complementarios para medias líneas."""

    period_key = "full_match" if period == "full_match" else "half"
    maximum = LADDER_MAXIMUMS[metric][period_key]
    return [
        _ladder_line(threshold, distribution, baseline)
        for threshold in range(maximum)
    ]


def _ladder_line(
    threshold: int, distribution: dict[int, float],
    baseline: dict[int, float],
) -> dict[str, float]:
    """Calcula una línea desde la cola de dos PMF."""

    over = sum(value for count, value in distribution.items()
               if count > threshold)
    base_over = sum(value for count, value in baseline.items()
                    if count > threshold)
    return {
        "line": float(threshold) + 0.5,
        "over_probability": float(over),
        "under_probability": float(1.0 - over),
        "baseline_over_probability": float(base_over),
        "baseline_under_probability": float(1.0 - base_over),
    }


def _recommendations(
    ladders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Selecciona un escenario no trivial por grupo y limita el bloque."""

    candidates = [
        recommendation for row in ladders
        if (recommendation := _best_scenario(row)) is not None
    ]
    return sorted(
        candidates,
        key=lambda row: (
            -row["probability"], -row["incremental_probability"], row["key"]),
    )[:RECOMMENDATION_LIMIT]


def _bounded_market_grid(
    ladders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Selecciona tres líneas informativas dentro de 1.5–9.5."""

    output = []
    for row in ladders:
        eligible = [
            line for line in row["ladder"]
            if VISIBLE_LINE_MIN <= line["line"] <= VISIBLE_LINE_MAX]
        selected = _centered_lines(eligible)
        if len(selected) != VISIBLE_GRID_SIZE:
            continue
        output.append({
            "key": row["key"], "metric": row["metric"],
            "team_side": row["team_side"], "period": row["period"],
            "expected_count": row["expected_count"],
            "most_likely_count": row["most_likely_count"],
            "lines": selected, "status": row["status"],
        })
    return output


def _centered_lines(lines: list[dict[str, float]]) -> list[dict[str, float]]:
    """Toma tres líneas consecutivas alrededor de P(over)≈50%."""

    if len(lines) < VISIBLE_GRID_SIZE:
        return []
    center = min(range(len(lines)), key=lambda index: (
        abs(lines[index]["over_probability"] - 0.5),
        lines[index]["line"]))
    start = min(max(center - 1, 0), len(lines) - VISIBLE_GRID_SIZE)
    return [dict(line) for line in lines[start:start + VISIBLE_GRID_SIZE]]


def _global_market_view(
    expected: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    dispersions: dict[str, float],
) -> list[dict[str, Any]]:
    """Modela totales desde intensidades históricas directas de Fase 84A."""

    return [_direct_total(expected, baseline, dispersions, metric)
            for metric in ("corners", "shots", "yellow_cards", "shots_on_target")]


def _direct_total(
    expected: dict[str, dict[str, float]], baseline: dict[str, dict[str, float]],
    dispersions: dict[str, float], metric: str,
) -> dict[str, Any]:
    """Construye una PMF total NB con tasa y dispersión causal observada."""

    rate = _side_rate(expected, metric, "total")
    base_rate = _side_rate(baseline, metric, "total")
    phi = _combined_phi(metric, "total", expected, dispersions)
    base_phi = _combined_phi(metric, "total", baseline, dispersions)
    model = negative_binomial_distribution(rate, phi)
    base = negative_binomial_distribution(base_rate, base_phi)
    row = _distributional_row(metric, "total", "full_match", model, base)
    return {**row, "aggregation": "phase84a_direct_total_nb",
            "baseline_expected_count": _expectation(base),
            "central_interval_60": _central_interval(model)}


def _expectation(distribution: dict[int, float]) -> float:
    """Calcula la media de una PMF discreta normalizada."""

    return float(sum(count * probability
                     for count, probability in distribution.items()))


def _central_interval(distribution: dict[int, float]) -> list[int]:
    """Obtiene el intervalo central 60% de una PMF ordenada."""

    return [_quantile(distribution, 0.20), _quantile(distribution, 0.80)]


def _quantile(distribution: dict[int, float], target: float) -> int:
    """Devuelve el menor conteo cuya CDF alcanza el cuantil solicitado."""

    cumulative = 0.0
    for count, probability in sorted(distribution.items()):
        cumulative += probability
        if cumulative >= target:
            return int(count)
    return int(max(distribution))


def _best_scenario(row: dict[str, Any]) -> dict[str, Any] | None:
    """Elige la dirección más probable dentro del rango informativo."""

    candidates = []
    for line in row["ladder"]:
        candidates.extend(_scenario_candidates(row, line))
    eligible = [
        item for item in candidates
        if RECOMMENDATION_MIN <= item["probability"] <= RECOMMENDATION_MAX
        and item["incremental_probability"] >= RECOMMENDATION_MIN_TEAM_EDGE
    ]
    return max(eligible, key=lambda item: item["probability"]) if eligible else None


def _scenario_candidates(
    row: dict[str, Any], line: dict[str, float],
) -> list[dict[str, Any]]:
    """Crea candidatos over y under para una línea."""

    common = {
        "key": row["key"], "metric": row["metric"],
        "team_side": row["team_side"], "period": row["period"],
        "line": line["line"], "expected_count": row["expected_count"],
        "most_likely_count": row["most_likely_count"],
        "status": row["status"],
    }
    return [
        {**common, "direction": "over",
         "probability": line["over_probability"],
         "baseline_probability": line["baseline_over_probability"],
         "incremental_probability": (
             line["over_probability"] - line["baseline_over_probability"])},
        {**common, "direction": "under",
         "probability": line["under_probability"],
         "baseline_probability": line["baseline_under_probability"],
         "incremental_probability": (
             line["under_probability"] - line["baseline_under_probability"])},
    ]


def _all_monotonic(rows: list[dict[str, Any]]) -> bool:
    """Verifica que la cola over nunca aumente con la línea."""

    return all(all(
        current["over_probability"] + 1e-12 >= following["over_probability"]
        for current, following in zip(row["ladder"], row["ladder"][1:])
    ) for row in rows)


# Version: 1.4.0
# Created: 2026-07-28
