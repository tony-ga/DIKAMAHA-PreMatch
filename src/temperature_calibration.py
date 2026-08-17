"""Recalibración por escalado de temperatura para mercados multiclase.

`market_calibration.py` recalibra mercados **binarios** por contracción bayesiana
de liga. 1X2 no tiene equivalente: sale directo de la matriz de marcadores de
`official_goal_chain.py` sin ningún paso posterior, y `DEC-162` midió que no
alcanza fiabilidad en ningún tramo de confianza -declara hasta 1.000 y entrega
51.0% en el tramo 0.65-0.75-.

El escalado de temperatura es la pieza que falta ahí. Reescala los logits antes
del softmax con un único parámetro `T > 0`:

    q_i = softmax(log(p)/T)_i = p_i^(1/T) / Σ_j p_j^(1/T)

`T = 1` es la identidad, `T > 1` aplana la distribución -menos confianza- y
`T < 1` la concentra. La propiedad que lo hace seguro para un producto ya
desplegado es que **no altera cuál resultado es el más probable**: `x^(1/T)` es
monótona creciente para todo `T > 0`, así que el orden de las probabilidades se
conserva y sólo cambia la confianza declarada.

Contrato de composición (`model_composition v1`, R6): la recalibración se aplica
**después** del modelo base, sobre probabilidades ya formadas, y `T` se estima en
un bloque de validación separado del que ajustó ese modelo. Estimar `T` sobre los
mismos datos que produjeron las probabilidades no mide calibración, mide ajuste.

Referencia verificada: Murphy, *Probabilistic Machine Learning*, §14.2.2.5,
p.614-615.

Este módulo **no está conectado al router**. Ver `DEC-199`.

# Requirements:
#   Python>=3.10
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scipy.optimize import minimize_scalar

from src.artifact_integrity import artifact_hash_matches

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts/phase_124_temperature_calibration"
CONTRACT_VERSION = "temperature_scaling_v1"

# Cota inferior de probabilidad antes de tomar logaritmos. Una probabilidad
# exactamente cero haría divergir el logit; el modelo base puede emitirla por
# redondeo en marcadores muy improbables.
PROBABILITY_FLOOR = 1e-15

# Rango de búsqueda de `T`. Fuera de él la recalibración deja de ser una
# corrección de confianza y pasa a destruir la señal: `T → 0` colapsa en el
# argmax con probabilidad uno y `T` muy grande devuelve la uniforme.
TEMPERATURE_MIN = 0.05
TEMPERATURE_MAX = 20.0


def apply_temperature(
    probabilities: Mapping[str, float], temperature: float,
) -> dict[str, float]:
    """Reescala probabilidades con la temperatura dada.

    Args:
        probabilities: Probabilidades del modelo base, una por clase.
        temperature: Parámetro `T > 0`.

    Returns:
        dict[str, float]: Probabilidades recalibradas y normalizadas.
    """

    temperature = _valid_temperature(temperature)
    items = _valid_probabilities(probabilities)
    scaled = {
        label: math.exp(math.log(max(value, PROBABILITY_FLOOR)) / temperature)
        for label, value in items.items()
    }
    total = sum(scaled.values())
    if not math.isfinite(total) or total <= 0.0:
        raise FloatingPointError("temperature_calibration_mass_invalid")
    calibrated = {label: value / total for label, value in scaled.items()}
    if not math.isclose(sum(calibrated.values()), 1.0, abs_tol=1e-9):
        raise FloatingPointError("temperature_calibration_not_normalized")
    return calibrated


def negative_log_likelihood(
    records: Sequence[tuple[Mapping[str, float], str]], temperature: float,
) -> float:
    """Calcula la log-verosimilitud negativa media bajo una temperatura.

    Es la función objetivo del ajuste y también la métrica con la que se compara
    contra `T = 1`: si el ajuste no reduce esta cifra, la recalibración no aporta
    nada y no debe adoptarse.
    """

    if not records:
        raise ValueError("temperature_calibration_empty_records")
    total = 0.0
    for probabilities, outcome in records:
        calibrated = apply_temperature(probabilities, temperature)
        if outcome not in calibrated:
            raise KeyError(f"temperature_calibration_unknown_outcome:{outcome}")
        total -= math.log(max(calibrated[outcome], PROBABILITY_FLOOR))
    return total / len(records)


def fit_temperature(
    records: Sequence[tuple[Mapping[str, float], str]],
) -> dict[str, Any]:
    """Estima `T` por máxima verosimilitud sobre un bloque de validación.

    El bloque debe ser distinto del usado para ajustar el modelo base (R6). Este
    módulo no puede comprobarlo -no ve la procedencia de los registros-, así que
    la separación es responsabilidad del script que lo invoca.

    Returns:
        dict[str, Any]: Temperatura ajustada, verosimilitudes antes y después,
        y el número de registros usados.
    """

    if len(records) < 2:
        raise ValueError("temperature_calibration_insufficient_records")

    result = minimize_scalar(
        lambda value: negative_log_likelihood(records, value),
        bounds=(TEMPERATURE_MIN, TEMPERATURE_MAX),
        method="bounded",
    )
    if not result.success:
        raise RuntimeError("temperature_calibration_did_not_converge")

    temperature = float(result.x)
    baseline = negative_log_likelihood(records, 1.0)
    fitted = negative_log_likelihood(records, temperature)
    return {
        "temperature": temperature,
        "nll_uncalibrated": baseline,
        "nll_calibrated": fitted,
        "nll_improvement": baseline - fitted,
        "records": len(records),
    }


class TemperatureCalibrationPort(ABC):
    """Puerto de recalibración multiclase para un mercado servido."""

    @abstractmethod
    def predict(
        self, market: str, probabilities: Mapping[str, float],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        """Recalibra las probabilidades de un mercado multiclase."""


class ArtifactTemperatureCalibrationProvider(TemperatureCalibrationPort):
    """Carga temperaturas selladas por mercado, una por archivo JSON.

    Mismo patrón de integridad que `ArtifactMarketCalibrationProvider`: el hash
    de cada calibrador se valida contra el `hashes.json` de la carpeta antes de
    usarse, de modo que un artefacto alterado o de otra versión falla cerrado en
    vez de recalibrar con un parámetro desconocido.
    """

    def __init__(self, artifact_dir: Path | None = None) -> None:
        """Configura el directorio de calibradores congelados."""

        self._dir = artifact_dir or DEFAULT_ARTIFACT_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def predict(
        self, market: str, probabilities: Mapping[str, float],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        """Aplica la temperatura congelada del mercado solicitado."""

        config = self._load(market)
        temperature = float(config["temperature"])
        calibrated = apply_temperature(probabilities, temperature)
        return calibrated, {
            "model": "temperature_scaling",
            "version": str(config["version"]),
            "market": market,
            "temperature": temperature,
            "argmax_preserved": True,
        }

    def _load(self, market: str) -> dict[str, Any]:
        """Valida hash y carga el contrato congelado de un mercado."""

        if market in self._cache:
            return self._cache[market]
        hashes_path = self._dir / "hashes.json"
        calibrator_path = self._dir / f"{market}.json"
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        if not isinstance(hashes, dict) or calibrator_path.name not in hashes:
            raise ValueError("temperature_calibration_hash_manifest_incomplete")
        if not calibrator_path.is_file() or not artifact_hash_matches(
                calibrator_path, hashes[calibrator_path.name]):
            raise ValueError("temperature_calibration_hash_mismatch")
        config = json.loads(calibrator_path.read_text(encoding="utf-8"))
        if config.get("version") != CONTRACT_VERSION:
            raise ValueError("temperature_calibration_version_mismatch")
        if config.get("market") != market:
            raise ValueError("temperature_calibration_market_mismatch")
        _valid_temperature(config.get("temperature"))
        self._cache[market] = config
        return config


def _valid_temperature(value: Any) -> float:
    """Valida que la temperatura sea utilizable."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("temperature_calibration_temperature_invalid")
    temperature = float(value)
    if not math.isfinite(temperature) or not (
            TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX):
        raise ValueError("temperature_calibration_temperature_invalid")
    return temperature


def _valid_probabilities(
    probabilities: Mapping[str, float],
) -> dict[str, float]:
    """Valida el vector de probabilidades del modelo base."""

    if not isinstance(probabilities, Mapping) or len(probabilities) < 2:
        raise ValueError("temperature_calibration_probabilities_invalid")
    validated: dict[str, float] = {}
    for label, value in probabilities.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("temperature_calibration_probabilities_invalid")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError("temperature_calibration_probabilities_invalid")
        validated[str(label)] = number
    total = sum(validated.values())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError("temperature_calibration_probabilities_unnormalized")
    return validated


def records_from_rows(
    rows: Iterable[Mapping[str, Any]], labels: Sequence[str],
) -> list[tuple[dict[str, float], str]]:
    """Convierte filas `{probabilities, outcome}` al formato de ajuste.

    Aísla el formato de entrada del ajuste en sí, para que la procedencia de los
    datos -backtest sellado, `prediction_settlements`, artefacto de fase- no
    obligue a tocar la matemática.
    """

    records: list[tuple[dict[str, float], str]] = []
    for row in rows:
        probabilities = {
            label: float(row["probabilities"][label]) for label in labels}
        outcome = str(row["outcome"])
        if outcome not in probabilities:
            raise KeyError(f"temperature_calibration_unknown_outcome:{outcome}")
        records.append((probabilities, outcome))
    return records


# Version: 1.0.0
# Created: 2026-08-16
