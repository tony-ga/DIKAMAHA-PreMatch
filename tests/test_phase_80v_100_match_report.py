"""Pruebas del reporte causal de 100 partidos."""

import numpy as np

from scripts.run_phase_80v_100_match_report import (
    _marginals,
    _sequence_text,
    _viterbi,
)


def test_marginals_remain_normalized() -> None:
    """La propagación pre-match conserva probabilidad."""

    initial = np.asarray([0.7, 0.1, 0.1, 0.1])
    transition = np.full((4, 4), 0.25)
    values = _marginals(initial, [transition] * 5)
    assert values.shape == (6, 4)
    assert np.allclose(values.sum(axis=1), 1.0)


def test_viterbi_and_labels_are_deterministic() -> None:
    """La secuencia MAP usa el mismo contrato legible."""

    initial = np.asarray([0.7, 0.1, 0.1, 0.1])
    transition = np.eye(4) * 0.6 + 0.1
    sequence = _viterbi(initial, [transition] * 5)
    assert sequence == [0] * 6
    assert _sequence_text(sequence) == "N-N-N-N-N-N"
