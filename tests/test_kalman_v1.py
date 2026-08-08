"""Pruebas aisladas para `kalman_v1`.

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
import pytest

from src.kalman_v1 import (
    KalmanConfig, KalmanFilterV1, _goal_matrix,
    _metrics_from_prediction, generate_synthetic_kalman_dataset,
    run_synthetic_kalman,
)
from src.kalman_v1 import run_real_kalman_dry_run


class KalmanV1Tests(unittest.TestCase):
    """Pruebas unitarias del filtro aislado."""

    def setUp(self) -> None:
        self.frame = generate_synthetic_kalman_dataset()
        self.config = KalmanConfig()
        self.filter = KalmanFilterV1(self.config)

    def test_state_initial_and_covariance_valid(self) -> None:
        """Comprueba estado inicial y covarianza."""

        payload = self.filter.fit_predict(self.frame.iloc[:2].copy())
        state = payload["state"]
        cov = np.asarray(state["covariance"], dtype=float)
        self.assertEqual(state["state_version"], "kalman_state_v1")
        self.assertTrue(np.allclose(cov, cov.T))
        self.assertGreaterEqual(np.min(np.linalg.eigvalsh(cov)), 0.0)

    def test_updates_are_sequential_and_deterministic(self) -> None:
        """Verifica actualización posterior y determinismo."""

        result1 = self.filter.fit_predict(self.frame.copy())
        result2 = KalmanFilterV1(self.config).fit_predict(self.frame.copy())
        self.assertEqual(result1["predictions_hash"], result2["predictions_hash"])
        self.assertEqual(result1["state_hash"], result2["state_hash"])
        first_state = result1["states_by_date"][0]
        self.assertNotEqual(first_state["state_before"], first_state["state_after"])

    def test_no_future_leakage(self) -> None:
        """Cambiar el futuro no altera estados previos."""

        base = self.frame.iloc[:6].copy()
        future = self.frame.copy()
        future.loc[future["match_id"] == 10, "home_goals"] = 9
        r1 = KalmanFilterV1(self.config).fit_predict(base)
        r2 = KalmanFilterV1(self.config).fit_predict(base)
        self.assertEqual(r1["predictions_hash"], r2["predictions_hash"])
        self.assertEqual(r1["states_by_date"], r2["states_by_date"])
        self.assertNotIn(10, [row["match_id"] for row in r1["predictions"]])

    def test_simultaneous_matches_and_cold_start(self) -> None:
        """Comprueba simultáneos y equipos sin historia."""

        result = self.filter.fit_predict(self.frame.copy())
        dates = [item["cutoff_ts"] for item in result["states_by_date"]]
        self.assertIn("2025-01-01T12:00:00+00:00", dates)
        cold_start_rows = [row for row in self.frame.itertuples() if row.kalman_cold_start]
        self.assertGreater(len(cold_start_rows), 0)

    def test_same_kickoff_state_is_invariant_to_match_id_order(self) -> None:
        """La actualización conjunta no depende del orden intra-kickoff."""

        swapped = self.frame.copy()
        swapped.loc[swapped["match_id"] == 1, "match_id"] = 101
        swapped.loc[swapped["match_id"] == 2, "match_id"] = 1
        swapped.loc[swapped["match_id"] == 101, "match_id"] = 2
        first = self.filter.fit_predict(self.frame.copy())
        second = KalmanFilterV1(self.config).fit_predict(swapped)
        self.assertEqual(first["state"], second["state"])

    def test_goal_log_score_indexes_the_observed_scoreline(self) -> None:
        """Fija el log-score exacto del marcador observado."""

        pred = {
            "prob_1_kalman": 0.4, "prob_x_kalman": 0.3,
            "prob_2_kalman": 0.3, "prob_over_2_5_kalman": 0.5,
            "prob_btts_kalman": 0.4,
            "expected_home_goals_kalman": 1.4,
            "expected_away_goals_kalman": 0.9,
        }
        row = pd.Series({
            "result_1x2": "1", "over_2_5": False, "btts": False,
            "total_goals": 1, "home_goals": 1, "away_goals": 0,
        })
        metrics = _metrics_from_prediction(pred, row)
        grid = _goal_matrix(1.4, 0.9, 10)
        self.assertAlmostEqual(
            metrics["log_score_goals"], -np.log(grid[1, 0]))

    def test_probabilities_and_no_nan(self) -> None:
        """Verifica probabilidades válidas y ausencia de NaN."""

        result = self.filter.fit_predict(self.frame.copy())
        for row in result["predictions"]:
            self.assertTrue(0.0 <= row["prob_1_kalman"] <= 1.0)
            self.assertTrue(0.0 <= row["prob_x_kalman"] <= 1.0)
            self.assertTrue(0.0 <= row["prob_2_kalman"] <= 1.0)
            self.assertTrue(0.0 <= row["prob_over_2_5_kalman"] <= 1.0)
            self.assertTrue(0.0 <= row["prob_btts_kalman"] <= 1.0)
            self.assertFalse(np.isnan(row["expected_home_goals_kalman"]))
            self.assertFalse(np.isnan(row["expected_away_goals_kalman"]))

    def test_invalid_parameters_fail_controlled(self) -> None:
        """Parámetros inválidos deben producir error controlado."""

        bad = KalmanConfig(process_noise_attack=-1.0)
        with self.assertRaises(ValueError):
            KalmanFilterV1(bad)

    def test_synthetic_artifacts_are_generated(self) -> None:
        """Genera artefactos locales versionados."""

        with tempfile.TemporaryDirectory() as tmp:
            result = run_synthetic_kalman(Path(tmp))
            self.assertTrue(result["payload"]["predictions_hash"])
            manifest = json.loads((Path(tmp) / "kalman_v1_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_version"], "kalman_v1")
            self.assertTrue((Path(tmp) / "kalman_v1_result.json").exists())

    @pytest.mark.historical
    def test_real_dry_run_smoke(self) -> None:
        """Valida el dry-run real contra el baseline congelado."""

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            result = run_real_kalman_dry_run(
                root / "artifacts/phase_2_5_match_features_v1_baseline/match_features_v1_baseline_manifest.json",
                root / "artifacts/phase_2_4_match_features_v1_dry_run/match_features_v1_candidate.json",
                root / "artifacts/phase_3_4_dixon_coles_v1_dry_run/dixon_coles_v1_result.json",
                root / "artifacts/phase_3_4_dixon_coles_v1_dry_run_replay/dixon_coles_v1_result.json",
                Path(tmp),
            )
            self.assertEqual(result["coverage"]["trainable_count"], 331)
            self.assertEqual(result["coverage"]["excluded_match_ids"], [704766])
            self.assertTrue(result["predictions_hash"])
            self.assertGreaterEqual(len(result["folds"]), 1)


if __name__ == "__main__":
    unittest.main()
