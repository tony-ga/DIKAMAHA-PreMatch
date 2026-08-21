"""Gate de elegibilidad de piernas para el Constructor de Parlays (Fase 135).

Decide qué mercado de una predicción pre-match puede entrar al menú de parlays,
leyendo el artefacto sellado de Fase 135. Un parlay multiplica probabilidades,
de modo que el error de una pierna sobreconfiada no se suma: se compone. Por eso
el gate exige por separado que el mercado tenga ventaja sobre su propia
referencia **y** que su confianza declarada sea cierta en el tramo donde se va a
usar. Son cosas distintas: el 1X2 tiene ventaja confirmada de `+4.8pp` y aun así
queda fuera, porque en su zona alta declara `0.79` y entrega `0.71`.

Tres invariantes que este módulo no negocia:

- **Se publica también el ratio de entrega, no sólo la probabilidad conjunta.**
  Fuera de muestra el parlay entrega entre el 94% y el 97% de lo que promete.
  Mostrar sólo la probabilidad repetiría el error que `high_probability_view`
  ya evita para picks sueltos.
- **Una pierna por partido.** La correlación entre mercados del mismo partido es
  real y no está modelada (DEC-203). Multiplicar dos piernas del mismo partido
  supondría una independencia que el corpus desmiente.
- **Degradación fail-closed.** Si el artefacto falta, no verifica su hash o
  cambia de versión, el menú queda vacío y lo declara. Nunca se cae a una
  heurística ni se inventa una pierna.

Elegibilidad no es promoción: las líneas siguen siendo experimentales. El módulo
no calcula stake, ROI ni valor frente a una cuota, que siguen bloqueados.

Version: 1.0.0
Created: 2026-08-21
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

try:
    from src.artifact_integrity import artifact_hash_matches
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from artifact_integrity import artifact_hash_matches

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
CRITERIA = ROOT / "artifacts/phase_135_parlay_eligibility/criteria.json"
EXPECTED_VERSION = "phase135_parlay_eligibility_v1"
STATUS = "experimental_shadow_not_promoted"
CONTAINER = "experimental_team_markets"
VIEW = "user_market_view"


class ParlayEligibilityError(ValueError):
    """Selección que viola una regla estructural del gate."""


class ParlayEligibilityView:
    """Aplica el gate sellado de Fase 135 a predicciones pre-match."""

    def __init__(self, path: Path | None = None) -> None:
        """Fija el artefacto sellado que gobierna la elegibilidad."""

        self._path = Path(path) if path is not None else CRITERIA
        self._cache: dict[str, Any] | None = None

    # -- carga sellada -----------------------------------------------------
    def _verify(self) -> str:
        """Comprueba el hash sellado del artefacto y lo devuelve.

        Mismo criterio que el resto del runtime: se tolera únicamente la
        representación LF/CRLF, porque el manifiesto se sella en Windows y la
        imagen desplegada es Linux.
        """

        manifest = self._path.parent / "hashes.json"
        hashes = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(hashes, dict) or self._path.name not in hashes:
            raise ValueError("phase135_hash_manifest_incomplete")
        expected = hashes[self._path.name]
        if not artifact_hash_matches(self._path, expected):
            raise ValueError("phase135_criteria_hash_mismatch")
        return str(expected)

    def _load(self) -> dict[str, Any]:
        """Carga y valida el gate sellado una sola vez."""

        if self._cache is not None:
            return self._cache
        sealed = self._verify()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if str(payload.get("version")) != EXPECTED_VERSION:
            raise ValueError("phase135_criteria_version_mismatch")
        markets = payload.get("eligible_markets")
        rules = payload.get("structural_rules")
        if not isinstance(markets, dict) or not isinstance(rules, dict):
            raise ValueError("phase135_criteria_malformed")
        thresholds = {}
        for key, value in markets.items():
            threshold = float(value["threshold"])
            if not 0.0 < threshold <= 1.0:
                raise ValueError("phase135_threshold_out_of_range")
            thresholds[str(key)] = threshold
        minimum, maximum = int(rules["min_legs"]), int(rules["max_legs"])
        per_match = int(rules["max_legs_per_match"])
        if not 1 <= minimum <= maximum or per_match < 1:
            raise ValueError("phase135_structural_rules_invalid")
        self._cache = {
            "version": str(payload["version"]), "sha256": sealed,
            "thresholds": thresholds, "min_legs": minimum,
            "max_legs": maximum, "max_legs_per_match": per_match,
            "delivery": _delivery_table(payload),
        }
        return self._cache

    def available(self) -> bool:
        """Indica si el gate sellado se puede usar."""

        try:
            self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("parlay_eligibility_unavailable: %s", error)
            return False
        return True

    # -- selección ---------------------------------------------------------
    def legs(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve las piernas elegibles de una predicción pre-match.

        Una línea entra sólo si su mercado pasó el gate y además su
        probabilidad alcanza el umbral que ese mercado tiene congelado. Un
        payload sin el bloque de mercados de equipo devuelve lista vacía, que
        es la degradación correcta: significa que esa liga no tiene cobertura,
        no que el partido no valga.
        """

        try:
            config = self._load()
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("parlay_eligibility_unavailable: %s", error)
            return []
        container = prediction.get(CONTAINER)
        if not isinstance(container, dict):
            return []
        rows = container.get(VIEW)
        if not isinstance(rows, list):
            return []
        output = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key", ""))
            threshold = config["thresholds"].get(key)
            if threshold is None:
                continue
            try:
                probability = float(row["probability"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0.0 <= probability <= 1.0 or probability < threshold:
                continue
            output.append({
                "key": key,
                "metric": row.get("metric"),
                "team_side": row.get("team_side"),
                "period": row.get("period"),
                "line": row.get("line"),
                "direction": "over",
                "probability": probability,
                "baseline_probability": _optional_float(
                    row.get("baseline_probability")),
                "threshold": threshold,
                "source_model": row.get("source_model"),
                "status": STATUS,
            })
        return sorted(output, key=lambda item: (-item["probability"], item["key"]))

    def menu(
        self, predictions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Arma el menú del día: piernas elegibles agrupadas por partido."""

        if not self.available():
            return {
                "status": "unavailable",
                "reason": "parlay_criteria_unavailable",
                "matches": [], "legs": 0,
            }
        config = self._load()
        matches = []
        for prediction in predictions:
            legs = self.legs(prediction)
            if not legs:
                continue
            matches.append({
                "fixture_key": prediction.get("fixture_key"),
                "match_id": prediction.get("match_id"),
                "league_slug": prediction.get("league_slug"),
                "kickoff_ts": prediction.get("kickoff_ts"),
                "home_team_name": prediction.get("home_team_name"),
                "away_team_name": prediction.get("away_team_name"),
                "legs": legs,
            })
        return {
            "status": "available", "label": STATUS,
            "criteria_version": config["version"],
            "criteria_sha256": config["sha256"],
            "min_legs": config["min_legs"], "max_legs": config["max_legs"],
            "max_legs_per_match": config["max_legs_per_match"],
            "matches": matches,
            "legs": sum(len(row["legs"]) for row in matches),
        }

    # -- combinación -------------------------------------------------------
    def build(self, selection: list[dict[str, Any]]) -> dict[str, Any]:
        """Combina piernas ya elegibles en una probabilidad conjunta.

        Multiplica porque cada pierna viene de un partido distinto -la regla
        estructural lo garantiza-, que es el único caso en que la independencia
        es defendible con la evidencia disponible. Publica junto a la
        probabilidad el ratio de entrega medido fuera de muestra para ese
        número de piernas, porque el declarado solo ya se sabe optimista.
        """

        config = self._load()
        _validate_selection(selection, config)
        joint = 1.0
        for leg in selection:
            joint *= float(leg["probability"])
        delivery = config["delivery"].get(str(len(selection)))
        return {
            "status": STATUS,
            "legs": len(selection),
            "joint_probability": joint,
            "delivery_ratio": None if delivery is None else delivery["ratio"],
            "expected_delivery": (
                None if delivery is None else joint * delivery["ratio"]),
            "evidence": (
                None if delivery is None else {
                    "source": "out_of_sample",
                    "declared": delivery["declared"],
                    "observed": delivery["observed"],
                    "parlays": delivery["parlays"],
                }),
            "independence_note": (
                "Se multiplica porque cada pierna viene de un partido distinto. "
                "La correlación dentro de un mismo partido es real y no está "
                "modelada, así que el gate no permite dos piernas del mismo "
                "partido."
            ),
            "criteria_version": config["version"],
        }


def _validate_selection(
    selection: list[dict[str, Any]], config: dict[str, Any],
) -> None:
    """Comprueba las reglas estructurales antes de multiplicar nada."""

    if len(selection) < config["min_legs"]:
        raise ParlayEligibilityError("parlay_below_minimum_legs")
    if len(selection) > config["max_legs"]:
        raise ParlayEligibilityError("parlay_above_maximum_legs")
    seen: dict[Any, int] = {}
    for leg in selection:
        key = str(leg.get("key", ""))
        threshold = config["thresholds"].get(key)
        if threshold is None:
            raise ParlayEligibilityError("parlay_market_not_eligible")
        probability = float(leg["probability"])
        if probability < threshold:
            raise ParlayEligibilityError("parlay_leg_below_threshold")
        # `fixture_key` identifica el partido; sin él no se puede comprobar la
        # regla y el gate falla cerrado en vez de multiplicar a ciegas.
        fixture = leg.get("fixture_key")
        if fixture is None:
            raise ParlayEligibilityError("parlay_leg_missing_fixture")
        seen[fixture] = seen.get(fixture, 0) + 1
        if seen[fixture] > config["max_legs_per_match"]:
            raise ParlayEligibilityError("parlay_multiple_legs_same_match")


def _delivery_table(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Extrae el ratio de entrega fuera de muestra por número de piernas."""

    block = payload.get("out_of_sample_simulation")
    if not isinstance(block, dict):
        return {}
    rows = block.get("by_legs")
    if not isinstance(rows, dict):
        return {}
    table = {}
    for legs, value in rows.items():
        try:
            ratio = float(value["delivery_ratio"])
            declared = float(value["declared"])
            observed = float(value["observed"])
            parlays = int(value["parlays"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(ratio) or ratio <= 0.0:
            continue
        table[str(legs)] = {
            "ratio": ratio, "declared": declared,
            "observed": observed, "parlays": parlays}
    return table


def _optional_float(value: Any) -> float | None:
    """Convierte a float sin fallar cuando el campo no viene."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Version: 1.0.0
# Created: 2026-08-21
