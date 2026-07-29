"""Pruebas de invariantes para simulación pre-match v1.

Version: 1.0.0
Created: 2026-07-26
"""
from __future__ import annotations

import random

from src.pre_match_simulation_v1 import _poisson, _sample


def test_sample_is_seed_deterministic() -> None:
    """Una semilla fija conserva el mismo estado elegido."""
    probabilities = {"equilibrio": 0.4, "presion": 0.3, "repliegue": 0.2, "desorganizacion": 0.1}
    assert _sample(probabilities, random.Random(10)) == _sample(probabilities, random.Random(10))


def test_poisson_accepts_zero_rate() -> None:
    """Una tasa nula no puede producir goles negativos ni positivos."""
    assert _poisson(0.0, random.Random(2)) == 0


# Version: 1.0.0
# Created: 2026-07-26
