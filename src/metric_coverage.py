"""Distingue una estadística ausente de un cero real, por liga y por partido.

El pipeline almacenó como `0` las métricas que el proveedor simplemente no
publica para ciertas competiciones. El efecto no es un sesgo pequeño: el
modelo aprendió `0.18` córners esperados para `esp.2`/`eng.3-5` y la Mini App
llegó a publicar "Menos de 4.5 córners: 99.99%" en Segunda División, cuando
el valor real ronda el 50%.

La regla no puede ser "muchos ceros = dato ausente". Las tarjetas rojas son
cero el 89% de las veces y eso es fútbol normal, no un fallo de datos.
Este módulo separa los casos con tres criterios distintos:

- **Ausencia por liga (cero)**: sólo para métricas cuyo cero es implausible en
  un partido profesional (córners y tiros). Medido: las ligas sanas tienen
  0-4% de ceros y las afectadas 72-100%, sin zona intermedia poblada.
- **Ausencia por liga (alias)**: seis ligas nunca reciben `shots`, pero en vez
  de cero el proveedor -o el pipeline- copia ahí el valor de
  `shots_on_target`. En esas ligas `shots == shots_on_target` el 98-100% de
  las veces, frente a 0.4-0.5% en ligas sanas -donde ocurre por azar en
  partidos con pocos tiros-. Sin este criterio esas filas aportaban una media
  de ~1.3 tiros en vez de ~10-13, arrastrando también el ajuste de las ligas
  sanas: era la causa real detrás de una subestimación sistemática de ~0.33
  tiros por equipo que ninguna corrección de dispersión o prior podía quitar.
- **Ausencia por observación**: `shots == 0` en un equipo-partido implica que
  el bloque de estadísticas de ese partido no llegó, de modo que córners y
  tiros a puerta de esa misma fila tampoco son fiables.

Las tarjetas -amarillas y rojas, completas o por mitad- nunca se marcan como
ausentes por conteo: sus ceros son observaciones válidas.

Version: 1.1.0
Created: 2026-08-12
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from src.settlement_store import wilson_interval
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from settlement_store import wilson_interval

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_ARTIFACT = ROOT / "artifacts/metric_coverage/coverage_map.json"

# Métricas cuyo cero en un equipo-partido profesional es implausible. Las
# tarjetas quedan deliberadamente fuera: su cero es real.
#
# Las variantes por periodo entran por el mismo motivo que la de partido
# completo: una liga que no publica córners tampoco los publica por mitad, y
# dejarlas fuera reintroduciría por la puerta de atrás el sesgo que DEC-173
# midió y corrigió -el ajuste aprendería 0.18 córners esperados de ligas
# donde el proveedor nunca entregó el dato-.
ZERO_IMPLAUSIBLE = frozenset({
    "corners", "corners_first_half", "corners_second_half",
    "shots", "shots_first_half", "shots_second_half",
})

# Marca el bloque completo de estadísticas como no recibido. Un equipo no
# remata cero veces en 90 minutos; en las ligas con cobertura sana esto ocurre
# el 0% de las veces.
BLOCK_SENTINEL = "shots"

# Métricas que dependen del mismo bloque del proveedor que `shots`.
BLOCK_DEPENDENT = frozenset({
    "corners", "corners_first_half", "corners_second_half",
    "shots", "shots_first_half", "shots_second_half", "shots_on_target"})

# Una liga se declara sin cobertura cuando supera esta fracción de ceros en
# una métrica implausible. La separación medida es tan amplia (0-4% sanas
# frente a 72-100% afectadas) que el umbral exacto no es delicado.
ABSENT_THRESHOLD = 0.60

# Métrica cuyo alias con otra se comprueba: si `shots` coincide con
# `shots_on_target` en una fracción alta de observaciones, la liga nunca
# recibió tiros totales reales. La separación medida (98-100% afectadas
# frente a 0.4-0.5% sanas) es igual de amplia que la de ceros.
ALIAS_CHECK = ("shots", "shots_on_target")
ALIAS_THRESHOLD = 0.60

# Por debajo de esta muestra no se emite veredicto de **cobertura**: no hay
# evidencia para afirmar que la métrica funciona. La ausencia sí puede
# concluirse antes, ver `_status`.
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
    aliased_metric, alias_source = ALIAS_CHECK
    alias_rate = _alias_rate(observations, aliased_metric, alias_source)
    metrics: dict[str, Any] = {}
    for metric in sorted({key for row in observations for key in row}):
        zeros = sum(1 for row in observations if float(row.get(metric, 0)) == 0)
        rate = zeros / total if total else 0.0
        entry = {
            "observations": total,
            "zero_rate": rate,
            "status": _status(metric, zeros, total),
        }
        if metric == aliased_metric:
            entry["alias_rate"] = alias_rate
            if entry["status"] == "covered" and total >= MINIMUM_OBSERVATIONS:
                entry["status"] = (
                    "absent" if alias_rate >= ALIAS_THRESHOLD else "covered")
        metrics[metric] = entry
    return {"observations": total, "metrics": metrics}


def _alias_rate(
    observations: list[dict[str, Any]], metric: str, reference: str,
) -> float:
    """Fracción de filas donde `metric` es idéntico a `reference`.

    Un partido real puede coincidir por azar -pocos tiros, todos a puerta-,
    pero no de forma sistemática. Ligas sanas lo hacen 0.4-0.5% de las veces;
    ligas donde el proveedor duplica el campo lo hacen 98-100%.
    """

    matched = sum(
        1 for row in observations
        if metric in row and reference in row
        and float(row[metric]) == float(row[reference]))
    return matched / len(observations) if observations else 0.0


def _status(metric: str, zeros: int, total: int) -> str:
    """Clasifica una métrica como cubierta, ausente o sin evidencia.

    El umbral de muestra es **asimétrico a propósito**, y esa asimetría es la
    corrección de esta versión. Antes, `total < MINIMUM_OBSERVATIONS`
    cortocircuitaba antes de mirar los ceros, así que una liga con 8 de 8
    equipos-partido sin un solo córner salía `insufficient_evidence` -es
    decir, no se suprimía nada- y el runtime publicaba una escalera de
    córners construida sobre un dato que el proveedor nunca entregó. Es el
    caso real de `uru.1` y `esp.super_cup`.

    Ocho ceros de ocho no es "poca evidencia sobre si hay córners": es
    evidencia fuerte de que no los hay. Se distingue con el límite inferior
    de Wilson al 95% sobre la tasa de ceros -la misma función que ya usa la
    selección de picks-, que exige más unanimidad cuanto menor es la
    muestra: 8/8 da 0.676 y 12/12 da 0.758, ambos por encima del umbral;
    2/2 sólo da 0.342 y sigue sin concluir nada, que es lo correcto.

    Afirmar **cobertura** sigue exigiendo la muestra completa: la pregunta
    inversa -"¿hay evidencia de que este dato existe y es fiable?"- no se
    puede responder con ocho partidos.
    """

    if metric not in ZERO_IMPLAUSIBLE:
        return "covered" if total >= MINIMUM_OBSERVATIONS else "insufficient_evidence"
    zero_rate = zeros / total if total else 0.0
    if total >= MINIMUM_OBSERVATIONS:
        return "absent" if zero_rate >= ABSENT_THRESHOLD else "covered"
    lower, _ = wilson_interval(zeros, total)
    return "absent" if lower >= ABSENT_THRESHOLD else "insufficient_evidence"


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

    def is_covered(self, league_slug: str, metric: str) -> bool:
        """Afirma cobertura **medida** de esa métrica en esa liga.

        Deliberadamente asimétrica frente a `is_absent`, que degrada abierto
        para no suprimir un mercado que sí funciona ante un artefacto
        ausente. Aquí la pregunta es la contraria -"¿hay evidencia de que
        este dato existe?"- y la respuesta ante cualquier duda es `False`:
        una liga que el mapa no evaluó, o evaluó con muestra insuficiente,
        no puede heredar el veredicto de fiabilidad global de la escalera
        auditada, que se midió sobre un corpus de ligas sanas.

        Es la lección de DEC-182: `uefa.europa.conf_qual` no tenía veredicto
        alguno, así que nada la suprimía, y publicó una línea de córners
        imposible construida sobre datos que el proveedor nunca entregó.
        """

        try:
            leagues = self._load()["leagues"]
        except (OSError, ValueError, KeyError):
            return False
        entry = leagues.get(league_slug, {}).get("metrics", {}).get(metric)
        return isinstance(entry, dict) and entry.get("status") == "covered"


# Version: 1.0.0
# Created: 2026-08-12
