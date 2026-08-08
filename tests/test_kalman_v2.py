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
import pandas as pd

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

    def test_poisson_matrix_applies_exact_dixon_coles_orientation(self) -> None:
        """Protege las celdas 1-0 y 0-1 contra el intercambio de lambdas."""

        home, away, rho = 1.7, 0.6, 0.12
        base = poisson_matrix(home, away, 8)
        corrected = poisson_matrix(home, away, 8, rho, True)
        common_scale = corrected[2, 0] / base[2, 0]
        self.assertAlmostEqual(
            corrected[1, 0] / base[1, 0] / common_scale,
            1.0 + away * rho)
        self.assertAlmostEqual(
            corrected[0, 1] / base[0, 1] / common_scale,
            1.0 + home * rho)

    def test_goal_log_score_indexes_the_observed_scoreline(self) -> None:
        """El diagnóstico usa P(goles observados), no P(1)*P(X)."""

        state = self.filter._init_state(
            [1, 2], "2025-01-01T00:00:00+00:00")
        pred, _ = self.filter._predict_one(
            state, 1, 2, 0, 1, "2025-01-02T00:00:00+00:00")
        row = pd.Series({
            "result_1x2": "1", "over_2_5": False, "btts": False,
            "total_goals": 1, "home_goals": 1, "away_goals": 0,
        })
        metrics = self.filter._prediction_metrics(pred, row)
        grid = poisson_matrix(
            pred.lambda_home, pred.lambda_away,
            self.config.max_goals_grid,
            self.config.dixon_coles_tau,
            self.config.use_dixon_coles_correction)
        self.assertAlmostEqual(
            metrics["log_score_goals"], -np.log(grid[1, 0]))

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

    def test_same_kickoff_state_is_invariant_to_match_id_order(self) -> None:
        """Cambiar el orden identificador no altera el estado posterior."""

        swapped = self.frame.copy()
        swapped.loc[swapped["match_id"] == 1, "match_id"] = 101
        swapped.loc[swapped["match_id"] == 2, "match_id"] = 1
        swapped.loc[swapped["match_id"] == 101, "match_id"] = 2
        first = self.filter.fit_predict(self.frame.copy())
        second = KalmanV2Filter(self.config).fit_predict(swapped)
        self.assertEqual(first["state"], second["state"])

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
