"""Pruebas aisladas para `kalman_v2`.

Requirements:
    None beyond the standard library and project dependencies.

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.kalman_v2 import (
    KalmanV2Config,
    KalmanV2Filter,
    generate_synthetic_dataset,
    poisson_matrix,
    run_synthetic_kalman_v2,
    _softmax_disallowed,
)


class KalmanV2Tests(unittest.TestCase):
    """Pruebas unitarias del filtro sintético v2."""

    def setUp(self) -> None:
        self.frame = generate_synthetic_dataset()
        self.config = KalmanV2Config()
        self.filter = KalmanV2Filter(self.config)

    def test_jacobian_and_observation_shapes(self) -> None:
        """Comprueba jacobiano y observación implícita."""

        state = self.filter._init_state([1, 2, 3, 4, 5], "2025-01-01T00:00:00+00:00")
        vector = self.filter._vector_from_state(state)
        jac = self.filter._jacobian(vector, 0, 1, len(state.team_ids), state.home_advantage, state.league_intercept)
        self.assertEqual(jac.shape, (2, 2 * len(state.team_ids) + 1))
        self.assertTrue(np.all(np.isfinite(jac)))

    def test_poisson_matrix_and_markets_are_valid(self) -> None:
        """Verifica mercados derivados de la matriz Poisson."""

        matrix = poisson_matrix(1.4, 1.1, 8)
        self.assertAlmostEqual(float(matrix.sum()), 1.0, places=10)
        self.assertTrue(np.all(matrix >= 0.0))

    def test_softmax_is_disallowed(self) -> None:
        """Softmax no debe usarse en la implementación."""

        with self.assertRaises(RuntimeError):
            _softmax_disallowed(np.array([1.0, 2.0, 3.0]))

    def test_projection_keeps_covariance_psd(self) -> None:
        """La proyección suma-cero conserva simetría y PSD."""

        state = self.filter._init_state([1, 2, 3, 4, 5], "2025-01-01T00:00:00+00:00")
        cov = np.asarray(state.covariance, dtype=float)
        mean = self.filter._vector_from_state(state)
        projected_mean, projected_cov = self.filter._project_sum_zero(mean, cov, len(state.team_ids))
        self.assertTrue(np.allclose(projected_cov, projected_cov.T))
        self.assertGreaterEqual(float(np.min(np.linalg.eigvalsh(projected_cov))), -1e-9)
        self.assertAlmostEqual(float(projected_mean[: len(state.team_ids)].sum()), 0.0, places=10)
        self.assertAlmostEqual(float(projected_mean[len(state.team_ids) : 2 * len(state.team_ids)].sum()), 0.0, places=10)

    def test_predict_before_update_and_simultaneous_batch(self) -> None:
        """Comprueba predict-before-update y simultáneos deterministas."""

        result = self.filter.fit_predict(self.frame.copy())
        first_batch = [row for row in result["predictions"] if row["cutoff_ts"] == "2025-01-01T12:00:00+00:00"]
        self.assertEqual(len(first_batch), 2)
        state_day0 = result["states_by_date"][0]
        self.assertNotEqual(state_day0["state_before"], state_day0["state_after"])
        self.assertEqual(first_batch[0]["cutoff_ts"], first_batch[1]["cutoff_ts"])

    def test_reproducibility_and_hashes(self) -> None:
        """Verifica determinismo de salidas y hashes."""

        result1 = self.filter.fit_predict(self.frame.copy())
        result2 = KalmanV2Filter(self.config).fit_predict(self.frame.copy())
        self.assertEqual(result1["dataset_hash"], result2["dataset_hash"])
        self.assertEqual(result1["config_hash"], result2["config_hash"])
        self.assertEqual(result1["predictions_hash"], result2["predictions_hash"])
        self.assertEqual(result1["matrices_hash"], result2["matrices_hash"])

    def test_invalid_lambda_raises(self) -> None:
        """Tasas inválidas deben fallar de forma controlada."""

        with self.assertRaises(ValueError):
            self.filter._validate_lambdas(0.0, 1.0)

    def test_artifacts_are_generated(self) -> None:
        """Genera artefactos locales reproducibles."""

        with tempfile.TemporaryDirectory() as tmp:
            result = run_synthetic_kalman_v2(Path(tmp))
            self.assertTrue(result["payload"]["predictions_hash"])
            manifest = json.loads((Path(tmp) / "kalman_v2_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_version"], "kalman_v2")
            self.assertTrue((Path(tmp) / "kalman_v2_result.json").exists())


if __name__ == "__main__":
    unittest.main()
