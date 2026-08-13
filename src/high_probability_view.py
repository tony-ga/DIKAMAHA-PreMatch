"""Selecciona los picks del día cuya fiabilidad histórica está demostrada.

Fase 122, extendida en la Etapa 4 de `docs/objetivo_auditoria_modelos_v1.md`.
Dos fuentes de evidencia, cada una gobernando su propio subconjunto de
mercados, sin mezclarse:

- **1X2, Over 2.5, Ambos marcan**: un pick sólo se expone cuando el par
  (mercado, tramo de confianza) al que pertenece superó el gate de
  `artifacts/phase_122_confidence_reliability` (backtest histórico post-hoc,
  Fase 122 original). Ningún cambio aquí respecto al diseño original.
- **Mercados de equipo (córners, tiros, tiros a puerta, tarjetas)**: vienen
  de la escalera auditada (`audited_market_ladder_view`,
  `src/ladder_pick_selection.py`), que ya filtra cada línea contra
  `scripts/run_ladder_audit.py` -calibración por tramos y ventaja con IC
  bootstrap sobre partido completo-. Es evidencia más rigurosa que el gate
  de Fase 122 (que sólo cubría 9 líneas fijas), así que estos mercados ya no
  pasan por `eligibility.json`: la escalera es su propio gate. A diferencia
  de los mercados de gol, **siempre** se expone al menos una línea por cada
  mercado que la escalera cubra -ver `src/ladder_pick_selection.py` para el
  criterio de banda de confianza que evita tanto el volado como lo obvio.

Dos consecuencias de diseño que no son negociables aquí:

- **Se publica la tasa observada, no la probabilidad del modelo.** El backtest
  encontró mercados que declaran 68% y entregan 89%, y otros que declaran 84% y
  entregan 74%. Mostrar la cifra del modelo sería engañoso en ambos sentidos;
  la cifra empírica es la única honesta, y por eso el orden del menú también
  usa esa cifra (histórica de tramo para mercados de gol, histórica de línea
  para mercados de equipo).
- **Se declara el origen de la ventaja.** Una fracción de las celdas/líneas
  aptas son `base_rate_driven`: aciertan mucho porque el mercado acierta mucho
  solo, no porque el modelo discrimine. La interfaz debe poder decirlo.

Degradación segura, independiente por fuente: si el artefacto de Fase 122
falta, no valida o cambia de forma, los mercados de gol quedan vacíos sin
afectar a los de equipo; si la escalera auditada viene vacía (liga sin
cobertura, artefacto de fiabilidad ausente), los mercados de equipo quedan
vacíos sin afectar a los de gol. Nunca se inventa un pick ni se cae a una
heurística.

Version: 2.0.0
Created: 2026-08-11
Updated: 2026-08-13 (Etapa 4: mercados de equipo migrados a la escalera
auditada, ver DEC-179)
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

try:
    from src.artifact_integrity import artifact_hash_matches
    from src.ladder_pick_selection import select_ladder_picks
    from src.market_exposure_policy import ExposurePolicy
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from artifact_integrity import artifact_hash_matches
    from ladder_pick_selection import select_ladder_picks
    from market_exposure_policy import ExposurePolicy

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ELIGIBILITY = (
    ROOT / "artifacts/phase_122_confidence_reliability/eligibility.json")
LADDER_RELIABILITY_ARTIFACT = ROOT / "artifacts/ladder_audit/ladder_reliability.json"
EXPECTED_VERSION = "phase122_confidence_reliability_v1"
STATUS = "experimental_shadow_not_promoted"
MAX_GOAL_PICKS_PER_MATCH = 3
MAX_PICKS_PER_COMPONENT = 1
GOAL_MARKETS = ("1x2", "over_2_5", "btts")


class HighProbabilityView:
    """Traduce una predicción pre-match al menú de mayor probabilidad."""

    def __init__(self, path: Path | None = None) -> None:
        """Fija el artefacto sellado que gobierna la elegibilidad de gol."""

        self._path = Path(path) if path is not None else ELIGIBILITY
        self._cache: dict[str, Any] | None = None
        self._policy = ExposurePolicy(
            MAX_GOAL_PICKS_PER_MATCH, MAX_PICKS_PER_COMPONENT, 0.0,
            (tuple(sorted(GOAL_MARKETS)),))

    def _verify(self) -> str:
        """Comprueba el hash sellado del artefacto de gol y lo devuelve.

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
        """Carga y valida el artefacto de gol sellado una sola vez."""

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
        """Indica si el artefacto de gol sellado se puede usar.

        Sólo cubre la fuente de 1X2/Over 2.5/Ambos marcan: la escalera
        auditada de mercados de equipo se evalúa por partido en `picks()` y
        degrada de forma independiente.
        """

        try:
            self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("phase122_eligibility_unavailable: %s", error)
            return False
        return True

    def picks(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve los picks aptos de una predicción, ya priorizados.

        Combina las dos fuentes -escalera auditada para equipo, gate de
        Fase 122 para gol- en una sola lista, ordenada por la misma cifra
        (tasa observada histórica) para que el orden sea comparable entre
        mercados de origen distinto.
        """

        combined = self._team_picks(prediction) + self._goal_picks(prediction)
        return sorted(
            combined,
            key=lambda pick: (
                -pick["observed_rate"], -pick["model_probability"],
                pick["market"]))

    def _team_picks(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Un pick por cada mercado de equipo que cubra la escalera auditada.

        No hay tope por partido: se publican **todos** los grupos que
        produzcan pick, así que la diversidad de mercados que ve el usuario
        depende de cuántos sobrevivan aguas arriba (cobertura de liga, peso
        de modelo, veredicto de fiabilidad), no de un recorte aquí.

        `select_ladder_picks` **no** garantiza un pick por grupo: DEC-182
        retiró esa garantía y DEC-187 la repuso sólo dentro de una cota dura,
        de modo que un grupo cuyas líneas sean todas obvias o todas volados
        sigue sin publicarse. La lista también queda vacía si la escalera
        misma llega vacía (liga sin cobertura medida, artefacto de fiabilidad
        ausente aguas arriba en `src/ladder_reliability_view.py`).
        """

        shadow = prediction.get("experimental_team_markets")
        if not isinstance(shadow, dict):
            return []
        ladder = shadow.get("audited_market_ladder_view")
        if not isinstance(ladder, list):
            return []
        try:
            return select_ladder_picks(ladder)
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.warning("ladder_pick_selection_failed: %s", error)
            return []

    def _goal_picks(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Picks de 1X2/Over 2.5/Ambos marcan, gobernados por el gate de Fase 122."""

        try:
            config = self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("phase122_view_fallback_empty: %s", error)
            return []
        try:
            candidates = [
                pick for row in _emitted_goal_markets(prediction)
                for pick in [_match_cell(row, config["cells"])] if pick]
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as error:
            LOGGER.warning("phase122_view_fallback_empty: %s", error)
            return []
        return self._prioritize_goal(candidates)

    def _prioritize_goal(
        self, candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ordena por tasa observada y limita señales redundantes de gol."""

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
        """Publica trazabilidad de las dos fuentes que gobiernan el menú."""

        goal_available = self.available()
        try:
            goal_config = self._load()
            eligible_cells = len(goal_config["cells"])
            goal_sha256 = goal_config["sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            eligible_cells = 0
            goal_sha256 = ""
        ladder_sha256 = _ladder_reliability_hash()
        combined = hashlib.sha256(
            f"{goal_sha256}|{ladder_sha256}".encode()).hexdigest()
        return {
            "status": STATUS,
            "version": EXPECTED_VERSION,
            "eligibility_sha256": combined,
            "eligible_cells": eligible_cells,
            "source": _relative_or_absolute(ELIGIBILITY),
            "goal_markets_gate_available": goal_available,
            "team_markets_source": _relative_or_absolute(LADDER_RELIABILITY_ARTIFACT),
            "team_markets_sha256": ladder_sha256,
        }


def _relative_or_absolute(path: Path) -> str:
    """Formatea una ruta para trazabilidad sin poder romper `provenance()`.

    En producción ambas rutas viven bajo `ROOT`; el `try` sólo cubre una
    sustitución de prueba u otra configuración inesperada.
    """

    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _ladder_reliability_hash() -> str:
    """Hash del artefacto de fiabilidad de la escalera, sólo para trazabilidad.

    A diferencia de `eligibility.json`, este artefacto no tiene un manifiesto
    `hashes.json` sellado -no hace falta un sistema de sellado nuevo sólo
    para esto-: se reporta el hash de lo que está cargado ahora mismo, útil
    para depuración y para que `eligibility_sha256` combinado cambie si
    cualquiera de las dos fuentes cambia, no para verificar tampering.
    """

    try:
        payload = LADDER_RELIABILITY_ARTIFACT.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return ""
    return hashlib.sha256(payload).hexdigest()


def _emitted_goal_markets(prediction: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae el pick emitido por el modelo en 1X2, Over 2.5 y Ambos marcan."""

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
    """Construye la fila intermedia previa al filtro de elegibilidad de gol.

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
    """Asocia un pick de gol al tramo apto que lo contiene, si existe."""

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


# Version: 2.0.0
# Created: 2026-08-11
# Updated: 2026-08-13
