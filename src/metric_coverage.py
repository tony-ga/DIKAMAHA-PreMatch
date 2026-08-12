"""Distingue una estadística ausente de un cero real, por liga y por partido.

El pipeline almacenó como `0` las métricas que el proveedor simplemente no
publica para ciertas competiciones. El efecto no es un sesgo pequeño: el
modelo aprendió `0.18` córners esperados para `esp.2`/`eng.3-5` y la Mini App
llegó a publicar "Menos de 4.5 córners: 99.99%" en Segunda División, cuando
el valor real ronda el 50%.

La regla no puede ser "muchos ceros = dato ausente". Las tarjetas rojas son
cero el 89% de las veces y eso es fútbol normal, no un fallo de datos.
Este módulo separa los dos casos con dos criterios distintos:

- **Ausencia por liga**: sólo para métricas cuyo cero es implausible en un
  partido profesional (córners y tiros). Medido: las ligas sanas tienen 0-4%
  de ceros y las afectadas 72-100%, sin zona intermedia poblada.
- **Ausencia por observación**: `shots == 0` en un equipo-partido implica que
  el bloque de estadísticas de ese partido no llegó, de modo que córners y
  tiros a puerta de esa misma fila tampoco son fiables.

Las tarjetas -amarillas y rojas, completas o por mitad- nunca se marcan como
ausentes por conteo: sus ceros son observaciones válidas.

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_ARTIFACT = ROOT / "artifacts/metric_coverage/coverage_map.json"

# Métricas cuyo cero en un equipo-partido profesional es implausible. Las
# tarjetas quedan deliberadamente fuera: su cero es real.
ZERO_IMPLAUSIBLE = frozenset({"corners", "corners_first_half", "shots"})

# Marca el bloque completo de estadísticas como no recibido. Un equipo no
# remata cero veces en 90 minutos; en las ligas con cobertura sana esto ocurre
# el 0% de las veces.
BLOCK_SENTINEL = "shots"

# Métricas que dependen del mismo bloque del proveedor que `shots`.
BLOCK_DEPENDENT = frozenset({
    "corners", "corners_first_half", "shots", "shots_on_target"})

# Una liga se declara sin cobertura cuando supera esta fracción de ceros en
# una métrica implausible. La separación medida es tan amplia (0-4% sanas
# frente a 72-100% afectadas) que el umbral exacto no es delicado.
ABSENT_THRESHOLD = 0.60

# Por debajo de esta muestra no se emite veredicto: no hay evidencia para
# afirmar ausencia ni cobertura.
MINIMUM_OBSERVATIONS = 20


def build_coverage_map(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Construye el veredicto de cobertura por liga y métrica.

    `rows` son filas equipo-partido con `league_slug` y un diccionario
    `actual` de conteos observados.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(dict(row["actual"]))
    leagues: dict[str, dict[str, Any]] = {}
    for league, observations in sorted(grouped.items()):
        leagues[league] = _league_verdicts(observations)
    return {
        "version": "metric_coverage_v1",
        "absent_threshold": ABSENT_THRESHOLD,
        "minimum_observations": MINIMUM_OBSERVATIONS,
        "zero_implausible_metrics": sorted(ZERO_IMPLAUSIBLE),
        "leagues": leagues,
    }


def _league_verdicts(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Emite un veredicto por métrica dentro de una liga."""

    total = len(observations)
    metrics: dict[str, Any] = {}
    for metric in sorted({key for row in observations for key in row}):
        zeros = sum(1 for row in observations if float(row.get(metric, 0)) == 0)
        rate = zeros / total if total else 0.0
        metrics[metric] = {
            "observations": total,
            "zero_rate": rate,
            "status": _status(metric, rate, total),
        }
    return {"observations": total, "metrics": metrics}


def _status(metric: str, zero_rate: float, total: int) -> str:
    """Clasifica una métrica como cubierta, ausente o sin evidencia."""

    if total < MINIMUM_OBSERVATIONS:
        return "insufficient_evidence"
    if metric not in ZERO_IMPLAUSIBLE:
        return "covered"
    return "absent" if zero_rate >= ABSENT_THRESHOLD else "covered"


def observation_is_absent(actual: dict[str, Any], metric: str) -> bool:
    """Indica si una fila equipo-partido carece del dato de esa métrica.

    Un `shots == 0` delata que el bloque de estadísticas del proveedor no
    llegó para ese partido, así que las métricas del mismo bloque tampoco son
    observaciones válidas aunque figuren como cero.
    """

    if metric not in BLOCK_DEPENDENT:
        return False
    return float(actual.get(BLOCK_SENTINEL, 0)) == 0


class MetricCoverage:
    """Consulta de cobertura para el runtime, con degradación segura."""

    def __init__(self, path: Path | None = None) -> None:
        """Fija el artefacto de cobertura a consultar."""

        self._path = Path(path) if path is not None else COVERAGE_ARTIFACT
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """Carga el mapa una sola vez."""

        if self._cache is None:
            self._cache = json.loads(self._path.read_text(encoding="utf-8"))
        return self._cache

    def is_absent(self, league_slug: str, metric: str) -> bool:
        """Indica si esa liga no publica esa métrica.

        Ante un artefacto ausente o ilegible devuelve `False`: la ausencia de
        evidencia no debe suprimir un mercado que sí funciona. La supresión
        exige evidencia positiva de que el dato no existe.
        """

        try:
            leagues = self._load()["leagues"]
        except (OSError, ValueError, KeyError):
            return False
        entry = leagues.get(league_slug, {}).get("metrics", {}).get(metric)
        if not isinstance(entry, dict):
            return False
        return str(entry.get("status")) == "absent"

    def absent_metrics(self, league_slug: str) -> frozenset[str]:
        """Devuelve las métricas sin cobertura de una liga."""

        try:
            leagues = self._load()["leagues"]
        except (OSError, ValueError, KeyError):
            return frozenset()
        metrics = leagues.get(league_slug, {}).get("metrics", {})
        return frozenset(
            name for name, entry in metrics.items()
            if isinstance(entry, dict) and entry.get("status") == "absent")


# Version: 1.0.0
# Created: 2026-08-12
