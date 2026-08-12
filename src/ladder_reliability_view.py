"""Consulta en runtime el veredicto de fiabilidad de cada línea auditada.

`artifacts/ladder_audit/ladder_reliability.json` (`scripts/run_ladder_audit.py`)
mide, contra el histórico real, si cada línea de la escalera está calibrada y
si aporta ventaja del modelo o sólo refleja la tasa base. Este módulo expone
esa evidencia al runtime para que la Mini App muestre la escalera completa en
vez del recorte fijo de tres líneas centrales, con cada línea etiquetada por
su fiabilidad real en vez de asumida.

Degrada **cerrado**, al revés que `MetricCoverage`. La asimetría es
deliberada: `MetricCoverage` protege un mercado que funciona de una lista de
supresión ausente -no publicar nada exige evidencia positiva de que el dato
falta-, mientras que aquí publicar una línea exige evidencia positiva de que
es fiable. Sin el artefacto, la escalera completa queda vacía en vez de
mostrarse sin auditar; el sistema entero no se cae por eso, sólo esta vista.

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RELIABILITY_ARTIFACT = ROOT / "artifacts/ladder_audit/ladder_reliability.json"
EXPECTED_VERSION = "ladder_reliability_v1"
PUBLISHABLE_VERDICTS = frozenset({"model_edge", "base_rate_driven"})


class LadderReliabilityView:
    """Búsqueda de veredictos por (métrica, lado, línea), con caché simple."""

    def __init__(self, path: Path | None = None) -> None:
        """Fija el artefacto a consultar sin cargarlo todavía."""

        self._path = Path(path) if path is not None else RELIABILITY_ARTIFACT
        self._index: dict[tuple[str, str, float], dict[str, Any]] | None = None

    def _load(self) -> dict[tuple[str, str, float], dict[str, Any]]:
        """Carga y indexa el artefacto una sola vez.

        Un artefacto ausente, corrupto o de versión distinta indexa vacío:
        ninguna línea se declara fiable sin evidencia verificada.
        """

        if self._index is not None:
            return self._index
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if str(payload.get("version")) != EXPECTED_VERSION:
                raise ValueError("ladder_reliability_version_mismatch")
            cells = payload["cells"]
            if not isinstance(cells, list):
                raise ValueError("ladder_reliability_malformed")
        except (OSError, ValueError, KeyError, TypeError):
            self._index = {}
            return self._index
        index: dict[tuple[str, str, float], dict[str, Any]] = {}
        for cell in cells:
            if str(cell.get("verdict")) not in PUBLISHABLE_VERDICTS:
                continue
            key = (str(cell["metric"]), str(cell["team_side"]),
                   float(cell["line"]))
            index[key] = cell
        self._index = index
        return index

    def available(self) -> bool:
        """Indica si hay al menos una línea publicable cargada."""

        try:
            return bool(self._load())
        except Exception:  # noqa: BLE001 - nunca debe tumbar al llamador
            return False

    def verdict(
        self, metric: str, team_side: str, line: float,
    ) -> dict[str, Any] | None:
        """Devuelve el veredicto publicable de una línea, o `None`.

        `None` cubre tanto "no auditada" como "auditada mal calibrada": en
        ambos casos la línea no se publica.
        """

        try:
            index = self._load()
        except Exception:  # noqa: BLE001 - degrada a no publicar, no a romper
            return None
        return index.get((str(metric), str(team_side), float(line)))


# Version: 1.0.0
# Created: 2026-08-12
