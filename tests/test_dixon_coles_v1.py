"""Pruebas aisladas para Dixon-Coles v1.

Estas pruebas usan únicamente datasets sintéticos controlados y no tocan
PostgreSQL ni el histórico real.

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

from src.dixon_coles_v1 import (
    DixonColesConfig,
    DixonColesEstimatorV1,
    generate_synthetic_dataset,
    hash_json,
    low_score_tau,
    poisson_simple_baseline,
    run_real_dry_run_cli,
    run_synthetic_baseline,
)


class DixonColesV1Tests(unittest.TestCase):
    """Pruebas unitarias del estimator aislado."""

    def setUp(self) -> None:
        """Prepara dataset sintético reutilizable."""

        self.frame = generate_synthetic_dataset()
        self.config = DixonColesConfig(initial_train_matches=4, folds=2, validation_horizon_matches=1)
        self.estimator = DixonColesEstimatorV1(self.config)

    def test_fit_and_predictions_are_valid(self) -> None:
        """Verifica intensidades positivas y probabilidades válidas."""

        model = self.estimator.fit(self.frame[self.frame["eligible_for_training"]].copy())
        prediction = self.estimator.predict_match(10, 20)
        self.assertGreater(prediction["expected_home_goals_dc"], 0.0)
        self.assertGreater(prediction["expected_away_goals_dc"], 0.0)
        self.assertTrue(0.0 <= prediction["prob_1"] <= 1.0)
        self.assertTrue(0.0 <= prediction["prob_x"] <= 1.0)
        self.assertTrue(0.0 <= prediction["prob_2"] <= 1.0)
        self.assertAlmostEqual(prediction["prob_1"] + prediction["prob_x"] + prediction["prob_2"], 1.0, places=6)
        self.assertIsNotNone(model.optimize_result)

    def test_low_score_correction_only_applies_to_low_markers(self) -> None:
        """Comprueba que la corrección DC solo afecta marcadores bajos."""

        lambda_home = 1.3
        lambda_away = 0.9
        self.assertNotEqual(low_score_tau(0, 0, lambda_home, lambda_away, 0.1), 1.0)
        self.assertNotEqual(low_score_tau(1, 0, lambda_home, lambda_away, 0.1), 1.0)
        self.assertNotEqual(low_score_tau(0, 1, lambda_home, lambda_away, 0.1), 1.0)
        self.assertNotEqual(low_score_tau(1, 1, lambda_home, lambda_away, 0.1), 1.0)
        self.assertEqual(low_score_tau(2, 2, lambda_home, lambda_away, 0.1), 1.0)

    def test_identifiability_and_determinism(self) -> None:
        """Verifica ajuste determinista y parámetros estables."""

        model1 = DixonColesEstimatorV1(self.config).fit(self.frame[self.frame["eligible_for_training"]].copy())
        model2 = DixonColesEstimatorV1(self.config).fit(self.frame[self.frame["eligible_for_training"]].copy())
        self.assertEqual(model1.attack, model2.attack)
        self.assertEqual(model1.defense, model2.defense)
        self.assertAlmostEqual(model1.home_advantage, model2.home_advantage, places=10)
        self.assertAlmostEqual(model1.league_intercept, model2.league_intercept, places=10)
        self.assertAlmostEqual(model1.tau_dc, model2.tau_dc, places=10)

    def test_small_history_and_controlled_failure(self) -> None:
        """Comprueba comportamiento con poca historia y fallo controlado."""

        tiny = self.frame.iloc[:2].copy()
        estimator = DixonColesEstimatorV1(DixonColesConfig(initial_train_matches=2, folds=2, fail_on_non_convergence=False))
        model = estimator.fit(tiny)
        self.assertIsNotNone(model.optimize_result)
        bad = tiny.copy()
        bad.loc[:, "home_goals"] = -1
        with self.assertRaises(ValueError):
            DixonColesEstimatorV1(DixonColesConfig(fail_on_non_convergence=True)).fit(bad)

    def test_anti_leakage_from_future_changes(self) -> None:
        """Cambiar el futuro no debe alterar una predicción anterior."""

        base = self.frame[self.frame["match_id"] <= 5].copy()
        future = self.frame[self.frame["match_id"] <= 6].copy()
        estimator = DixonColesEstimatorV1(self.config)
        model = estimator.fit(base)
        pred_before = estimator.predict_with_model(model, 10, 20)
        future.loc[future["match_id"] == 6, "home_goals"] = 9
        pred_after = estimator.predict_with_model(model, 10, 20)
        self.assertEqual(pred_before, pred_after)

    def test_expanding_window_and_baselines(self) -> None:
        """Valida expansión temporal sintética y baselines."""

        validation = self.estimator.expanding_window_validate(self.frame)
        self.assertGreaterEqual(validation["fold_count"], 2)
        self.assertGreaterEqual(len(validation["folds"]), 1)
        baseline = poisson_simple_baseline(self.frame)
        self.assertGreater(baseline["home_mean"], 0.0)
        self.assertGreater(baseline["away_mean"], 0.0)

    def test_reproducible_run_artifacts(self) -> None:
        """Genera artefactos locales y valida hashes."""

        with tempfile.TemporaryDirectory() as tmp:
            result = run_synthetic_baseline(Path(tmp))
            self.assertIn("model", result)
            self.assertIn("validation", result)
            self.assertTrue(result["hashes"]["dataset"])
            self.assertTrue(result["hashes"]["predictions"])
            manifest = json.loads((Path(tmp) / "dixon_coles_v1_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_version"], "dixon_coles_v1")
            self.assertEqual(manifest["dataset_hash"], result["model"].dataset_hash)

    @pytest.mark.historical
    def test_real_dry_run_smoke(self) -> None:
        """Ejecuta un dry-run real controlado sobre el baseline aprobado."""

        with tempfile.TemporaryDirectory() as tmp:
            result = run_real_dry_run_cli(Path(tmp))
            self.assertEqual(result["coverage"]["trainable_count"], 331)
            self.assertEqual(result["coverage"]["row_count"], 381)
            self.assertEqual(result["coverage"]["issues"], [])
            self.assertGreaterEqual(result["diagnostics"]["fold_count"], 1)
            self.assertTrue(result["predictions_hash"])
            self.assertTrue(Path(tmp, "dixon_coles_v1_result.json").exists())

    def test_no_nan_or_infinities(self) -> None:
        """Verifica ausencia de NaN e infinitos en el dataset sintético."""

        self.assertFalse(np.isnan(self.frame["home_goals"]).any())
        self.assertFalse(np.isnan(self.frame["away_goals"]).any())
        self.assertFalse(np.isinf(self.frame["home_goals"]).any())
        self.assertFalse(np.isinf(self.frame["away_goals"]).any())


if __name__ == "__main__":
    unittest.main()
