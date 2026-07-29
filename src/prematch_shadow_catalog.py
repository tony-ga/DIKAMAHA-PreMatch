"""Carga y expone el catálogo shadow pre-match en modo sólo lectura.

El módulo valida el contrato publicado por la Fase 25 y construye únicamente
metadatos de observación. Nunca calcula predicciones ni permite activaciones.

Version: 1.0.0
Created: 2026-07-26
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "artifacts/phase_25_shadow_model_catalog/shadow_contract.json"


class ShadowCatalogError(ValueError):
    """Indica que el contrato shadow local no es seguro para observación."""


def _load_json(path: Path) -> dict[str, Any]:
    """Carga un objeto JSON local y comprueba su tipo raíz."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowCatalogError(f"No se pudo cargar el catálogo shadow: {path}") from exc
    if not isinstance(payload, dict):
        raise ShadowCatalogError("El catálogo shadow debe ser un objeto JSON.")
    return payload


def _validate_contract(contract: dict[str, Any]) -> None:
    """Valida las invariantes que impiden activar candidatos."""

    candidates = contract.get("candidates")
    if contract.get("activation_policy") != "all_candidates_disabled":
        raise ShadowCatalogError("La política shadow no mantiene todos los candidatos desactivados.")
    if not isinstance(candidates, list) or not candidates:
        raise ShadowCatalogError("El catálogo shadow no contiene candidatos.")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ShadowCatalogError("El catálogo contiene un candidato inválido.")
        if candidate.get("enabled_by_default") or candidate.get("official_output_allowed"):
            raise ShadowCatalogError("Se detectó un candidato shadow habilitado.")
    if not isinstance(contract.get("official_router"), dict):
        raise ShadowCatalogError("El catálogo no contiene el router oficial.")


@lru_cache(maxsize=1)
def _cached_contract() -> dict[str, Any]:
    """Carga una sola copia inmutable lógica del contrato local."""

    contract = _load_json(CATALOG_PATH)
    _validate_contract(contract)
    return contract


def load_shadow_catalog() -> dict[str, Any]:
    """Devuelve una copia segura del contrato shadow validado."""

    return copy.deepcopy(_cached_contract())


def build_shadow_observation(contract: dict[str, Any]) -> dict[str, Any]:
    """Construye metadatos de observación sin resultados experimentales.

    Args:
        contract: Contrato validado por :func:`load_shadow_catalog`.

    Returns:
        Bloque serializable para una respuesta pre-match.
    """

    _validate_contract(contract)
    candidates = [
        {
            key: candidate[key]
            for key in ("model", "status", "source", "reason")
            if key in candidate
        }
        | {"enabled_by_default": False, "official_output_allowed": False}
        for candidate in contract["candidates"]
    ]
    return {
        "mode": "read_only",
        "observation_only": True,
        "catalog_version": contract["version"],
        "activation_policy": contract["activation_policy"],
        "official_prediction_source": contract["official_prediction_source"],
        "official_router": copy.deepcopy(contract["official_router"]),
        "candidates": candidates,
        "candidate_outputs_computed": False,
        "official_output_unchanged": True,
        "target_match_data_used": False,
    }


# Version: 1.0.0
# Created: 2026-07-26
