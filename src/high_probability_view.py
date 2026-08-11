"""Selecciona los picks del día cuya fiabilidad histórica está demostrada.

Fase 122. La regla que gobierna este módulo es que una probabilidad alta no
basta: sólo se expone un pick cuando el par (mercado, tramo de confianza) al
que pertenece superó el gate de `artifacts/phase_122_confidence_reliability`.

Dos consecuencias de diseño que no son negociables aquí:

- **Se publica la tasa observada, no la probabilidad del modelo.** El backtest
  encontró mercados que declaran 68% y entregan 89%, y otros que declaran 84% y
  entregan 74%. Mostrar la cifra del modelo sería engañoso en ambos sentidos;
  la cifra empírica del tramo es la única honesta, y por eso el orden del menú
  también usa esa cifra.
- **Se declara el origen de la ventaja.** Seis de las nueve celdas aptas son
  `base_rate_driven`: aciertan mucho porque el mercado acierta mucho solo, no
  porque el modelo discrimine. La interfaz debe poder decirlo.

Degradación segura: si el artefacto falta, no valida o cambia de forma, la
vista devuelve una lista vacía. Nunca inventa un pick ni cae a una heurística.

Version: 1.0.0
Created: 2026-08-11
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from src.artifact_integrity import artifact_hash_matches
    from src.market_exposure_policy import ExposurePolicy
    from src.team_count_market_runtime import MARKET_METADATA
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from artifact_integrity import artifact_hash_matches
    from market_exposure_policy import ExposurePolicy
    from team_count_market_runtime import MARKET_METADATA

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ELIGIBILITY = (
    ROOT / "artifacts/phase_122_confidence_reliability/eligibility.json")
EXPECTED_VERSION = "phase122_confidence_reliability_v1"
STATUS = "experimental_shadow_not_promoted"
MAX_PICKS_PER_MATCH = 3
MAX_PICKS_PER_COMPONENT = 1
GOAL_MARKETS = ("1x2", "over_2_5", "btts")


def _components() -> tuple[tuple[str, ...], ...]:
    """Agrupa mercados que miden lo mismo del mismo equipo.

    Tres líneas de tiros del local no son tres señales independientes: son la
    misma señal partida por periodo. El menú no debe llenarse con ellas.
    """

    groups: dict[tuple[str, str], list[str]] = {}
    for key, (metric, side, _, _, _) in MARKET_METADATA.items():
        groups.setdefault((metric, side), []).append(key)
    groups[("goals", "match")] = list(GOAL_MARKETS)
    return tuple(
        tuple(sorted(value)) for _, value in sorted(groups.items()))


class HighProbabilityView:
    """Traduce una predicción pre-match al menú de mayor probabilidad."""

    def __init__(self, path: Path | None = None) -> None:
        """Fija el artefacto sellado que gobierna la elegibilidad."""

        self._path = Path(path) if path is not None else ELIGIBILITY
        self._cache: dict[str, Any] | None = None
        self._policy = ExposurePolicy(
            MAX_PICKS_PER_MATCH, MAX_PICKS_PER_COMPONENT, 0.0, _components())

    def _verify(self) -> str:
        """Comprueba el hash sellado del artefacto y lo devuelve.

        Tolera únicamente la representación LF/CRLF, igual que el resto del
        runtime: el manifiesto se sella en Windows y la imagen es Linux.
        """

        manifest = self._path.parent / "hashes.json"
        hashes = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(hashes, dict) or self._path.name not in hashes:
            raise ValueError("phase122_hash_manifest_incomplete")
        expected = hashes[self._path.name]
        if not artifact_hash_matches(self._path, expected):
            raise ValueError("phase122_eligibility_hash_mismatch")
        return str(expected)

    def _load(self) -> dict[str, Any]:
        """Carga y valida el artefacto sellado una sola vez."""

        if self._cache is not None:
            return self._cache
        sealed = self._verify()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if str(payload.get("version")) != EXPECTED_VERSION:
            raise ValueError("phase122_eligibility_version_mismatch")
        cells = payload.get("eligible_cells")
        if not isinstance(cells, list):
            raise ValueError("phase122_eligibility_malformed")
        for cell in cells:
            low = float(cell["bucket_low"])
            high = float(cell["bucket_high"])
            rate = float(cell["observed_rate"])
            if not 0.0 <= low < high <= 1.0 or not 0.0 <= rate <= 1.0:
                raise ValueError("phase122_eligibility_bounds_invalid")
            if int(cell["picks"]) <= 0:
                raise ValueError("phase122_eligibility_sample_invalid")
        self._cache = {
            "version": str(payload["version"]),
            "cells": cells,
            "sha256": sealed,
        }
        return self._cache

    def available(self) -> bool:
        """Indica si el artefacto sellado se puede usar."""

        try:
            self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("phase122_eligibility_unavailable: %s", error)
            return False
        return True

    def picks(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve los picks aptos de una predicción, ya priorizados."""

        try:
            config = self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("phase122_view_fallback_empty: %s", error)
            return []
        try:
            candidates = [
                pick for row in _emitted(prediction)
                for pick in [_match_cell(row, config["cells"])] if pick]
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as error:
            LOGGER.warning("phase122_view_fallback_empty: %s", error)
            return []
        return self._prioritize(candidates)

    def _prioritize(
        self, candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ordena por tasa observada y limita señales redundantes."""

        ordered = sorted(
            candidates,
            key=lambda pick: (
                -pick["observed_rate"], -pick["model_probability"],
                pick["market"]))
        selected: list[dict[str, Any]] = []
        for pick in ordered:
            keys = [item["market"] for item in selected] + [pick["market"]]
            if self._policy.validate(keys):
                selected.append(pick)
        return selected

    def provenance(self) -> dict[str, Any]:
        """Publica trazabilidad del artefacto que gobierna el menú."""

        try:
            config = self._load()
        except (OSError, ValueError, KeyError, TypeError):
            return {"status": "unavailable", "eligible_cells": 0}
        return {
            "status": STATUS,
            "version": config["version"],
            "eligibility_sha256": config["sha256"],
            "eligible_cells": len(config["cells"]),
            "source": ELIGIBILITY.relative_to(ROOT).as_posix(),
        }


def _emitted(prediction: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae el pick emitido por el modelo en cada mercado disponible."""

    rows: list[dict[str, Any]] = []
    outcomes = {
        "home": prediction.get("probability_home"),
        "draw": prediction.get("probability_draw"),
        "away": prediction.get("probability_away"),
    }
    if all(isinstance(value, (int, float)) for value in outcomes.values()):
        direction = max(outcomes, key=lambda key: float(outcomes[key]))
        rows.append(_row(
            "1x2", direction, float(outcomes[direction]),
            metric="result", side="match", period="full_match", line=None))
    for market, field in (
        ("over_2_5", "probability_over_2_5"), ("btts", "probability_btts"),
    ):
        value = prediction.get(field)
        if isinstance(value, (int, float)):
            rows.append(_binary(market, float(value), "goals", "match",
                                "full_match", 2.5 if market == "over_2_5" else None))
    shadow = prediction.get("experimental_team_markets") or {}
    for item in shadow.get("user_market_view") or []:
        key = str(item["key"])
        if key not in MARKET_METADATA:
            continue
        rows.append(_binary(
            key, float(item["probability"]), str(item["metric"]),
            str(item["team_side"]), str(item["period"]), float(item["line"])))
    return rows


def _binary(
    market: str, probability: float, metric: str, side: str, period: str,
    line: float | None,
) -> dict[str, Any]:
    """Normaliza un mercado binario al lado que el modelo elige."""

    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"phase122_probability_out_of_range:{market}")
    direction = "over" if probability > 0.5 else "under"
    confidence = probability if probability > 0.5 else 1.0 - probability
    return _row(market, direction, confidence, metric, side, period, line)


def _row(
    market: str, direction: str, confidence: float, metric: str, side: str,
    period: str, line: float | None,
) -> dict[str, Any]:
    """Construye la fila intermedia previa al filtro de elegibilidad.

    `model_probability` es siempre la probabilidad que el modelo asigna al lado
    emitido, no la del `over`: para un pick `under` de 0.30 la cifra relevante
    es 0.70.
    """

    return {
        "market": market, "direction": direction, "confidence": confidence,
        "metric": metric, "team_side": side, "period": period, "line": line,
        "model_probability": confidence,
    }


def _match_cell(
    row: dict[str, Any], cells: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Asocia un pick al tramo apto que lo contiene, si existe."""

    confidence = float(row["confidence"])
    for cell in cells:
        if str(cell["market"]) != row["market"]:
            continue
        if not float(cell["bucket_low"]) <= confidence < float(
                cell["bucket_high"]):
            continue
        return {
            **row,
            "observed_rate": float(cell["observed_rate"]),
            "observed_ci95": [float(value) for value in cell["observed_ci95"]],
            "sample_size": int(cell["picks"]),
            "edge_source": str(cell["edge_source"]),
            "skill_vs_naive": float(cell["skill_vs_naive"]),
            "bucket": [
                float(cell["bucket_low"]), float(cell["bucket_high"])],
            "league_stability": float(cell["non_degraded_rate"]),
            "status": STATUS,
        }
    return None


# Version: 1.0.0
# Created: 2026-08-11
