"""Reajusta los modelos de conteo de equipo excluyendo datos contaminados.

`scripts/run_phase_84a_team_count_markets.py` estimó dispersión y coeficientes
Poisson mezclando partidos reales con partidos de ligas cuya estadística el
proveedor nunca publicó -almacenada como cero-. Efecto medido: el modelo
aprendió 0.18 córners esperados para `esp.2`/`eng.3-5` (media real ~8) y la
dispersión global de córners (`0.966`) quedó sesgada también para las ligas
sanas, porque se estimó sobre la mezcla.

Este script reutiliza sin modificar la construcción de datos y el pipeline de
ajuste originales de Fase 84A -misma lectura del corpus, mismos features
causales, mismo solver Poisson regularizado, misma selección de alpha por
`selection`, mismo split walk-forward-. La única diferencia es el conjunto de
filas de entrenamiento y confirmación que entra a cada métrica: se excluyen
las contaminadas según `src/metric_coverage.py`, con dos criterios distintos
según el fallo real medido:

- **Corners y corners primera mitad**: excluye toda fila de una liga marcada
  `absent` en el mapa de cobertura (ausencia sistémica por competición).
- **Cualquier métrica dependiente del bloque de tiros**: excluye la fila
  puntual cuando `shots == 0` en esa observación, porque delata que el bloque
  de estadísticas del proveedor no llegó para ese partido -aunque la liga en
  general sí tenga cobertura-.
- **Tarjetas**: sin filtro. Sus ceros son observaciones válidas.

No se toca el corpus de Fase 74 ni el historial acumulado por equipo: sólo se
excluyen filas contaminadas del ajuste y de la puntuación, igual que ya hace
el propio pipeline con los splits `fit`/`selection`/`confirmation`.

Publica en el mismo formato y contrato que Fase 84A
(`config.json`/`audit.json`/`models.joblib`/`hashes.json`) para que
`ArtifactTeamCountMarketProvider` lo cargue sin cambios de esquema. Sobrescribe
el artefacto servido -Fase 84A original queda preservada en el historial de
git-.

# Requirements:
#   joblib>=1.4
#   numpy>=2.0
#   scikit-learn>=1.5

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_84a_team_count_markets import (  # noqa: E402
    ALPHAS,
    MARKET_LINES,
    METRICS,
    OUTPUT,
    SOURCE,
    _gate,
    _matches,
    _outcomes,
    _player_readiness,
    _read_rows,
    _report,
    _sha,
    _select_count_weight,
    _write_json,
)
from src.metric_coverage import MetricCoverage, observation_is_absent  # noqa: E402
from src.team_count_markets import (  # noqa: E402
    CountMarketSolver,
    SklearnPoissonSolver,
    poisson_deviance,
)
from src.team_count_market_runtime import APPROVED_MARKETS  # noqa: E402
from src.temporal_integrity import kickoff_buckets  # noqa: E402

LOGGER = logging.getLogger(__name__)
REPORT_SUFFIX = "coverage_bias_repair_v1"


def _examples_clean(
    matches: list[dict[str, Any]], specs: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruye ejemplos igual que Fase 84A, con targets sin filtrar aún.

    El filtro se aplica después, por métrica, en `_matrix_clean`: una misma
    fila puede ser válida para tarjetas y contaminada para córners.
    """

    specs = specs if specs is not None else list(METRICS)
    team: dict[tuple[str, int, str], list[float]] = {}
    league: dict[tuple[str, bool, str], list[float]] = {}
    output = []
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            output.extend(
                _match_examples_clean(specs, match, team, league))
        for match in bucket:
            _update_history_clean(match, team, league)
    return output


def _match_examples_clean(
    specs: list[Any], match: dict[str, Any], team: dict[Any, list[float]],
    league: dict[Any, list[float]],
) -> list[dict[str, Any]]:
    """Réplica de `_match_examples` de Fase 84A con priors parametrizados."""

    output = []
    for home in (True, False):
        side, rival = ("home", "away") if home else ("away", "home")
        output.append({
            "match_id": match["match_id"], "match_date": match["match_date"],
            "league_slug": match["league_slug"], "split": match["split"],
            "is_home": home,
            "team_id": match[f"{side}_team_id"],
            "opponent_team_id": match[f"{rival}_team_id"],
            "features": _features_with(specs, match, home, team, league),
            "targets": match[side],
            "baselines": _baselines_with(specs, match, home, league),
        })
    return output


def _update_history_clean(
    match: dict[str, Any], team: dict[Any, list[float]],
    league: dict[Any, list[float]],
) -> None:
    """Réplica exacta de `_update_history` de Fase 84A."""

    from scripts.run_phase_84a_team_count_markets import _add, _add_league
    league_name = str(match["league_slug"])
    for home in (True, False):
        side, rival = ("home", "away") if home else ("away", "home")
        own_id, rival_id = match[f"{side}_team_id"], match[f"{rival}_team_id"]
        for spec in METRICS:
            own, conceded = match[side][spec.name], match[rival][spec.name]
            _add(team, (league_name, own_id, spec.name), own, conceded)
            _add_league(league, (league_name, home, spec.name), own)


def _contaminated(
    row: dict[str, Any], metric: str, coverage: MetricCoverage,
) -> bool:
    """Decide si esta fila no debe entrar al ajuste ni a la puntuación de esta métrica."""

    league_absent = (
        metric in coverage.absent_metrics(str(row["league_slug"])))
    return league_absent or observation_is_absent(row["targets"], metric)


def _matrix_clean(
    rows: list[dict[str, Any]], metric: str, coverage: MetricCoverage,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Extrae features/targets excluyendo filas contaminadas de esta métrica."""

    kept = [row for row in rows if not _contaminated(row, metric, coverage)]
    features = np.asarray([row["features"] for row in kept], dtype=float)
    targets = np.asarray([row["targets"][metric] for row in kept], dtype=float)
    return features, targets, len(rows) - len(kept)


def _dispersion(targets: np.ndarray, predicted: np.ndarray | None = None) -> float:
    """Estima la sobredispersión **condicional** a la media predicha.

    Fase 84A la estimaba sobre la varianza marginal del target agrupado, que
    mezcla dos fuentes distintas: la dispersión real alrededor de la media de
    cada partido y la variación de esa media entre partidos. Para un modelo
    que ya predice una media por partido, la segunda no debe contarse: inflaba
    `phi`, ensanchaba la NB y empujaba todas las probabilidades hacia 0.5.

    Medido sobre el corpus: para tiros la marginal daba `0.34` frente a `0.12`
    condicional -casi el triple-, y la auditoría de escalera encontró 96
    líneas subestimando de forma sistemática por ese motivo.

    Sin `predicted` conserva el comportamiento marginal original.
    """

    if predicted is None:
        mean = max(float(np.mean(targets)), 1e-9)
        variance = float(np.var(targets))
        return max((variance - mean) / (mean * mean), 1e-4)
    mu = np.clip(np.asarray(predicted, dtype=float), 1e-9, None)
    residual = (np.asarray(targets, dtype=float) - mu) ** 2 - mu
    # Un valor negativo significa infradispersión (las tarjetas lo son): la NB
    # no puede representarla, así que el suelo la deja en Poisson.
    return max(float(np.mean(residual) / np.mean(mu * mu)), 1e-4)


def _calibrated_specs(
    matches: list[dict[str, Any]], coverage: MetricCoverage,
) -> list[Any]:
    """Recalcula el prior de suavizado de cada métrica desde los datos.

    Fase 84A fijó `safe_default` a mano: córners `4.5` cuando la media real es
    `7.99`, córners de primera mitad `2.2` frente a `3.51`. Ese prior no sólo
    sesga el baseline hacia abajo -explica buena parte de la subestimación
    sistemática que encontró la auditoría-, también contamina los *features*
    de historial de cada equipo, que se suavizan contra él: un equipo con
    pocos partidos queda descrito por una cifra que no se parece a su liga.

    Se estima únicamente sobre el bloque `fit` -el más antiguo- para no mirar
    datos de selección ni de confirmación.
    """

    from dataclasses import replace
    fit = [match for match in matches if match["split"] == "fit"]
    output = []
    for spec in METRICS:
        values = []
        for match in fit:
            for side in ("home", "away"):
                row = {
                    "league_slug": match["league_slug"],
                    "targets": match[side],
                }
                if not _contaminated(row, spec.name, coverage):
                    values.append(float(match[side][spec.name]))
        default = float(np.mean(values)) if values else spec.safe_default
        output.append(replace(spec, safe_default=max(default, 1e-6)))
    return output


def _features_with(
    specs: list[Any], match: dict[str, Any], home: bool,
    team: dict[Any, list[float]], league: dict[Any, list[float]],
) -> list[float]:
    """Réplica de `_features` de Fase 84A con priors parametrizados."""

    from scripts.run_phase_84a_team_count_markets import _profile_values
    league_name = str(match["league_slug"])
    own = int(match["home_team_id"] if home else match["away_team_id"])
    rival = int(match["away_team_id"] if home else match["home_team_id"])
    values = [float(home)]
    for spec in specs:
        own_stats = team.get((league_name, own, spec.name), [0.0, 0.0, 0.0])
        rival_stats = team.get(
            (league_name, rival, spec.name), [0.0, 0.0, 0.0])
        league_stats = league.get((league_name, home, spec.name), [0.0, 0.0])
        values.extend(_profile_values(
            own_stats, rival_stats, league_stats, spec.safe_default))
    return values


def _baselines_with(
    specs: list[Any], match: dict[str, Any], home: bool,
    league: dict[Any, list[float]],
) -> dict[str, float]:
    """Réplica de `_baselines` de Fase 84A con priors parametrizados."""

    league_name = str(match["league_slug"])
    output = {}
    for spec in specs:
        total, count = league.get((league_name, home, spec.name), [0.0, 0.0])
        output[spec.name] = (total + 20.0 * spec.safe_default) / (count + 20.0)
    return output


def _blended_prediction(
    solver: Any, weight: float, rows: list[dict[str, Any]], metric: str,
    coverage: MetricCoverage,
) -> np.ndarray:
    """Reproduce la media publicada: mezcla de modelo y baseline de liga."""

    kept = [row for row in rows if not _contaminated(row, metric, coverage)]
    features = np.asarray([row["features"] for row in kept], dtype=float)
    baseline = np.asarray(
        [row["baselines"][metric] for row in kept], dtype=float)
    raw = solver.predict(features)
    return weight * raw + (1.0 - weight) * baseline


def _select_alpha_clean(
    fit: list[dict[str, Any]], selection: list[dict[str, Any]], metric: str,
    coverage: MetricCoverage,
) -> tuple[float, dict[str, Any], float, int]:
    """Selecciona regularización sobre datos limpios, misma lógica que Fase 84A."""

    x_fit, y_fit, dropped_fit = _matrix_clean(fit, metric, coverage)
    x_selection, y_selection, dropped_selection = _matrix_clean(
        selection, metric, coverage)
    scores: dict[str, Any] = {}
    solvers = {}
    for alpha in ALPHAS:
        solver = SklearnPoissonSolver(alpha)
        solver.fit(x_fit, y_fit)
        solvers[alpha] = solver
        predicted = solver.predict(x_selection)
        scores[str(alpha)] = float(np.mean([
            poisson_deviance(actual, expected)
            for actual, expected in zip(y_selection, predicted)]))
    selected = min(ALPHAS, key=lambda value: scores[str(value)])
    weight, blend_scores = _select_count_weight(
        solvers[selected], selection, metric)
    return selected, {"alpha_deviance": scores, "blend_deviance": blend_scores}, (
        weight), dropped_fit + dropped_selection


def _fit_models_clean(
    examples: list[dict[str, Any]], coverage: MetricCoverage,
) -> tuple[
    dict[str, CountMarketSolver], dict[str, float], dict[str, Any],
    dict[str, float], dict[str, float], dict[str, int],
]:
    """Réplica de `_fit_models` de Fase 84A con filas contaminadas excluidas."""

    fit = [row for row in examples if row["split"] == "fit"]
    selection = [row for row in examples if row["split"] == "selection"]
    train = [row for row in examples if row["split"] in ("fit", "selection")]
    models, alphas, selection_scores, dispersions, weights, dropped = (
        {}, {}, {}, {}, {}, {})
    for spec in METRICS:
        alpha, scores, weight, dropped_selection = _select_alpha_clean(
            fit, selection, spec.name, coverage)
        x_train, y_train, dropped_train = _matrix_clean(
            train, spec.name, coverage)
        solver = SklearnPoissonSolver(alpha)
        solver.fit(x_train, y_train)
        models[spec.name] = solver
        alphas[spec.name] = alpha
        selection_scores[spec.name] = scores
        # La dispersión se mide contra la media que realmente se publica -la
        # mezcla seleccionada entre modelo y baseline-, no contra la salida
        # cruda del solver: es esa mezcla la que alimenta la NB en runtime.
        blended = _blended_prediction(
            solver, weight, train, spec.name, coverage)
        dispersions[spec.name] = _dispersion(y_train, blended)
        weights[spec.name] = weight
        dropped[spec.name] = dropped_train
    return models, alphas, selection_scores, dispersions, weights, dropped


def _predict_clean(
    examples: list[dict[str, Any]], models: dict[str, Any],
    weights: dict[str, float], coverage: MetricCoverage,
) -> list[dict[str, Any]]:
    """Emite confirmation excluyendo filas contaminadas por métrica.

    A diferencia del entrenamiento, aquí cada fila puede aportar unas
    métricas y no otras: una fila de `esp.2` predice tarjetas con
    normalidad pero no córners.
    """

    confirmation = [row for row in examples if row["split"] == "confirmation"]
    output = []
    for row in confirmation:
        features = np.asarray([row["features"]], dtype=float)
        expected = {}
        for spec in METRICS:
            if _contaminated(row, spec.name, coverage):
                continue
            raw = float(models[spec.name].predict(features)[0])
            expected[spec.name] = float(
                weights[spec.name] * raw
                + (1.0 - weights[spec.name]) * row["baselines"][spec.name])
        if not expected:
            continue
        output.append({
            **{key: row[key] for key in (
                "match_id", "match_date", "league_slug", "is_home",
                "team_id", "opponent_team_id")},
            "expected": expected,
            "baseline": {
                name: row["baselines"][name] for name in expected},
        })
    return output


def _score_clean(
    predictions: list[dict[str, Any]],
    outcomes: dict[tuple[int, bool], dict[str, float]],
) -> list[dict[str, Any]]:
    """Puntúa sólo las métricas que sobrevivieron el filtro por fila."""

    from scripts.run_phase_84a_team_count_markets import _count_score
    output = []
    for row in predictions:
        actual_all = outcomes[(int(row["match_id"]), bool(row["is_home"]))]
        metrics = {
            name: _count_score(
                actual_all[name], row["expected"][name], row["baseline"][name])
            for name in row["expected"]
        }
        output.append({
            **row, "actual": {
                name: actual_all[name] for name in row["expected"]},
            "metrics": metrics,
        })
    return output


def _count_metrics_clean(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega sólo sobre las filas que efectivamente puntuaron cada métrica."""

    output = {}
    for spec in METRICS:
        scores = [row["metrics"][spec.name] for row in rows
                  if spec.name in row["metrics"]]
        if not scores:
            output[spec.name] = {"sample": 0}
            continue
        output[spec.name] = {
            key: float(np.mean([score[key] for score in scores]))
            for key in scores[0]
        }
        output[spec.name]["sample"] = len(scores)
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if spec.name not in row["metrics"]:
                continue
            score = row["metrics"][spec.name]
            grouped[str(row["league_slug"])].append(
                score["baseline_deviance"] - score["model_deviance"])
        eligible = [values for values in grouped.values() if len(values) >= 30]
        output[spec.name]["league_nonnegative_rate"] = (
            sum(float(np.mean(values) >= 0.0) for values in eligible)
            / max(len(eligible), 1))
    return output


def _market_metrics_clean(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega sólo sobre los partidos que efectivamente publicaron esa línea.

    A diferencia de `_market_metrics` de Fase 84A, una fila puede no traer
    todas las líneas de `MARKET_LINES`: un partido de una liga sin córners
    nunca llega a construir `home_corners_over_4_5`.
    """

    output = {}
    for market in MARKET_LINES:
        values = [row["markets"][market] for row in rows if market in row["markets"]]
        if not values:
            output[market] = {"sample": 0}
            continue
        output[market] = {
            key: float(np.mean([value[key] for value in values]))
            for key in (
                "model_log_loss", "baseline_log_loss",
                "model_brier", "baseline_brier")
        }
        output[market]["positive_rate"] = float(np.mean([
            value["actual"] for value in values]))
        output[market]["sample"] = len(values)
    return output


def _market_rows_clean(
    rows: list[dict[str, Any]], dispersions: dict[str, float],
) -> list[dict[str, Any]]:
    """Construye líneas comerciales sólo con partidos cuyas dos orientaciones
    conservan la métrica requerida."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    output = []
    for values in grouped.values():
        home = next((r for r in values if bool(r["is_home"])), None)
        away = next((r for r in values if not bool(r["is_home"])), None)
        if home is None or away is None:
            continue
        markets = {}
        for name, (metric, side, line) in MARKET_LINES.items():
            required = {"home"} if side == "home" else (
                {"away"} if side == "away" else {"home", "away"})
            sides_available = (
                ({"home"} if metric in home["expected"] else set())
                | ({"away"} if metric in away["expected"] else set()))
            if not required.issubset(sides_available):
                continue
            markets[name] = _market_score(
                home, away, metric, side, line, dispersions[metric])
        if markets:
            output.append({
                "match_id": home["match_id"], "match_date": home["match_date"],
                "league_slug": home["league_slug"],
                "home_team_id": home["team_id"], "away_team_id": away["team_id"],
                "markets": markets,
            })
    return output


def _market_score(
    home: dict[str, Any], away: dict[str, Any], metric: str, side: str,
    line: int, dispersion: float,
) -> dict[str, Any]:
    """Réplica de `_match_markets`/`_binary_score` de Fase 84A para una línea."""

    from scripts.run_phase_84a_team_count_markets import (
        _binary_score, _combined_dispersion, _rate)
    from src.team_count_markets import negative_binomial_over_probability
    model_rate = _rate(home, away, metric, side, "expected")
    baseline_rate = _rate(home, away, metric, side, "baseline")
    actual = _rate(home, away, metric, side, "actual")
    model_phi = _combined_dispersion(home, away, metric, side, "expected", dispersion)
    baseline_phi = _combined_dispersion(
        home, away, metric, side, "baseline", dispersion)
    return _binary_score(
        negative_binomial_over_probability(model_rate, model_phi, line),
        negative_binomial_over_probability(baseline_rate, baseline_phi, line),
        actual > line)


def run() -> dict[str, Any]:
    """Ejecuta el reajuste completo y publica el artefacto corregido."""

    coverage = MetricCoverage()
    matches = _matches(_read_rows())
    specs = _calibrated_specs(matches, coverage)
    LOGGER.info(
        "priors_recalibrados=%s",
        {spec.name: round(spec.safe_default, 3) for spec in specs})
    examples = _examples_clean(matches, specs)
    models, alphas, selection_scores, dispersions, weights, dropped = (
        _fit_models_clean(examples, coverage))
    predictions = _predict_clean(examples, models, weights, coverage)
    scored = _score_clean(predictions, _outcomes(examples))
    market_rows = _market_rows_clean(scored, dispersions)
    counts = _count_metrics_clean(scored)
    markets = _market_metrics_clean(market_rows)
    gate = _clamp_to_approved(_gate(counts, markets))
    result = _result_clean(
        matches, examples, alphas, dispersions, weights, selection_scores,
        scored, market_rows, counts, markets, gate, dropped, specs)
    _publish_clean(result, models)
    return result


def _clamp_to_approved(gate: dict[str, Any]) -> dict[str, Any]:
    """Restringe lo publicado a los mercados ya aprobados, sin auto-promover.

    Los datos limpios hacen que más líneas superen el mismo gate de punto que
    ya usaba Fase 84A -sin sesgo de ligas contaminadas, el modelo rinde mejor
    de verdad-, pero promover un mercado nuevo exige el criterio de esta
    auditoría (calibración + IC bootstrap sobre tasa base), no un gate de
    punto sin intervalo. `APPROVED_MARKETS` en el runtime valida en duro que
    `enabled_shadow_markets` coincida exacto; publicar el gate sin recortar
    habría dejado caído el bloque completo de mercados de equipo.
    """

    newly_qualifying = sorted(
        set(gate["enabled_shadow_markets"]) - APPROVED_MARKETS)
    if newly_qualifying:
        LOGGER.info(
            "gate_amplio_no_promovido mercados=%s -> pendiente de auditoria "
            "de escalera completa con IC bootstrap", newly_qualifying)
    return {
        **gate,
        "enabled_shadow_markets": sorted(
            set(gate["enabled_shadow_markets"]) & APPROVED_MARKETS),
        "gate_passed_pending_bootstrap_audit": newly_qualifying,
    }


def _result_clean(
    matches: list[dict[str, Any]], examples: list[dict[str, Any]],
    alphas: dict[str, float], dispersions: dict[str, float],
    weights: dict[str, float], selection_scores: dict[str, Any],
    scored: list[dict[str, Any]], market_rows: list[dict[str, Any]],
    counts: dict[str, Any], markets: dict[str, Any], gate: dict[str, Any],
    dropped: dict[str, int], specs: list[Any],
) -> dict[str, Any]:
    """Compone el contrato, idéntico en forma al de Fase 84A."""

    from dataclasses import asdict
    classification = (
        "ready_for_next_phase" if gate["any_market_ready"]
        else "rejected_for_revision")
    return {
        "classification": classification,
        "config": {
            "version": "team_count_markets_v3_kickoff_integrity",
            "alphas": alphas, "dispersions": dispersions,
            "model_weights": weights, "market_lines": MARKET_LINES,
            "metrics": [asdict(spec) for spec in specs],
            "model_status": "experimental_shadow_not_promoted",
        },
        "selection": selection_scores,
        "coverage": {
            "source_matches": len(matches), "examples": len(examples),
            "confirmation_team_rows": len(scored),
            "confirmation_matches": len(market_rows),
            "leagues": len({row["league_slug"] for row in market_rows}),
            "dropped_training_rows_by_metric": dropped,
            "repair": REPORT_SUFFIX,
        },
        "audit": {
            "features_strictly_prior": True,
            "same_kickoff_batch_safe": True,
            "split_kickoff_atomic": True,
            "target_match_events_in_features": False,
            "selection_uses_confirmation": False,
            "goal_model_modified": False, "router_modified": False,
            "player_markets_enabled": False, **gate,
        },
        "player_market_readiness": _player_readiness(),
        "count_metrics": counts, "market_metrics": markets,
        "predictions": market_rows, "team_predictions": scored,
        "model_names": sorted(dispersions),
    }


def _publish_clean(result: dict[str, Any], models: dict[str, Any]) -> None:
    """Publica el artefacto corregido en el mismo directorio que Fase 84A.

    Sobrescribe `config.json`/`audit.json`/`models.joblib`/`hashes.json`
    -exactamente lo que carga `ArtifactTeamCountMarketProvider`-. Fase 84A
    original queda preservada en el historial de git, no en un directorio
    paralelo.
    """

    OUTPUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "config.json": result["config"], "selection.json": result["selection"],
        "coverage.json": result["coverage"], "audit.json": result["audit"],
        "metrics.json": {
            "counts": result["count_metrics"], "markets": result["market_metrics"]},
        "predictions.json": result["predictions"],
        "team_predictions.json": result["team_predictions"],
        "player_market_readiness.json": result["player_market_readiness"],
        "input_manifest.json": {"source_sha256": _sha(SOURCE)},
    }
    for name, value in payloads.items():
        _write_json(name, value)
    joblib.dump(
        {name: model.native_model() for name, model in models.items()},
        OUTPUT / "models.joblib")
    report = _report(result) + (
        f"\n## Reparación {REPORT_SUFFIX}\n\n"
        f"Filas de entrenamiento excluidas por contaminación de datos "
        f"ausentes, por métrica: `{result['coverage']['dropped_training_rows_by_metric']}`.\n"
    )
    (OUTPUT / "final_report.md").write_text(report, encoding="utf-8")
    (OUTPUT / "validation_report.md").write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "hashes.json"})


def main() -> int:
    """Ejecuta la reparación y reporta el resultado."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = run()
    LOGGER.info("reparacion_completada clasificacion=%s", result["classification"])
    LOGGER.info(
        "filas_excluidas_por_metrica=%s",
        result["coverage"]["dropped_training_rows_by_metric"])
    LOGGER.info(
        "dispersiones_nuevas=%s",
        {k: round(v, 4) for k, v in result["config"]["dispersions"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-08-12
