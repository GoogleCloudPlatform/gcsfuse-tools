"""Comprehensive unit tests for convergence.py and gcsfuse-perf-base.dockerfile."""

import math
from pathlib import Path
import unittest

import convergence


class TestBetaIncAndStudentT(unittest.TestCase):
    """Tests for Feature F1: betainc, student_t_two_sided_pvalue, student_t_critical_value."""

    def test_betainc_boundaries_and_validation(self):
        self.assertEqual(convergence.betainc(2.0, 3.0, 0.0), 0.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, -0.5), 0.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, 1.0), 1.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, 1.5), 1.0)
        self.assertTrue(math.isnan(convergence.betainc(float("nan"), 1.0, 0.5)))
        self.assertTrue(math.isnan(convergence.betainc(1.0, float("nan"), 0.5)))
        self.assertTrue(math.isnan(convergence.betainc(1.0, 1.0, float("nan"))))

        with self.assertRaises(ValueError):
            convergence.betainc(0.0, 1.0, 0.5)
        with self.assertRaises(ValueError):
            convergence.betainc(-2.0, 1.0, 0.5)
        with self.assertRaises(ValueError):
            convergence.betainc(1.0, 0.0, 0.5)
        with self.assertRaises(ValueError):
            convergence.betainc(1.0, -3.0, 0.5)

    def test_betainc_symmetry_identity(self):
        grid = [
            (0.5, 0.5, 0.25),
            (1.5, 0.5, 0.37),
            (2.5, 0.5, 0.81),
            (15.0, 0.5, 0.92),
            (500.0, 0.5, 0.995),
            (50000.0, 0.5, 0.9999),
            (3.7, 8.2, 0.42),
        ]
        for a, b, x in grid:
            lhs = convergence.betainc(a, b, x) + convergence.betainc(b, a, 1.0 - x)
            self.assertAlmostEqual(lhs, 1.0, places=13)

    def test_student_t_pvalue_nist_reference(self):
        self.assertEqual(convergence.student_t_two_sided_pvalue(0.0, 5.0), 1.0)
        self.assertEqual(convergence.student_t_two_sided_pvalue(float("inf"), 5.0), 0.0)
        self.assertEqual(convergence.student_t_two_sided_pvalue(float("-inf"), 5.0), 0.0)
        self.assertTrue(math.isnan(convergence.student_t_two_sided_pvalue(float("nan"), 5.0)))
        self.assertTrue(math.isnan(convergence.student_t_two_sided_pvalue(2.0, float("nan"))))

        with self.assertRaises(ValueError):
            convergence.student_t_two_sided_pvalue(1.0, 0.0)
        with self.assertRaises(ValueError):
            convergence.student_t_two_sided_pvalue(1.0, -1.0)

        # Symmetry p(-t, df) == p(t, df)
        for df in [1.0, 2.0, 4.37, 10.0, 100000.0]:
            p_pos = convergence.student_t_two_sided_pvalue(2.345, df)
            p_neg = convergence.student_t_two_sided_pvalue(-2.345, df)
            self.assertEqual(p_pos, p_neg)

        # NIST reference critical values where two-sided p-value is exactly 0.05
        nist_05 = [
            (1.0, 12.706204736174698),
            (2.0, 4.302652729749464),
            (3.0, 3.182446305283708),
            (4.0, 2.776445105197799),
            (5.0, 2.570581835636315),
            (10.0, 2.228138851986273),
            (30.0, 2.042272456301237),
            (4.37, 2.686148100040880),
        ]
        for df, t_crit in nist_05:
            p = convergence.student_t_two_sided_pvalue(t_crit, df)
            self.assertAlmostEqual(p, 0.05, places=11)

    def test_student_t_critical_nist_reference(self):
        with self.assertRaises(ValueError):
            convergence.student_t_critical_value(0.0, 0.95)
        with self.assertRaises(ValueError):
            convergence.student_t_critical_value(5.0, 1.0)
        with self.assertRaises(ValueError):
            convergence.student_t_critical_value(5.0, 0.0)

        nist_05 = [
            (1.0, 12.706204736174698),
            (2.0, 4.302652729749464),
            (3.0, 3.182446305283708),
            (4.0, 2.776445105197799),
            (5.0, 2.570581835636315),
            (10.0, 2.228138851986273),
            (30.0, 2.042272456301237),
            (4.37, 2.686148100040880),
        ]
        for df, expected_t in nist_05:
            computed_t = convergence.student_t_critical_value(df, confidence=0.95)
            self.assertAlmostEqual(computed_t, expected_t, places=10)
            p_back = convergence.student_t_two_sided_pvalue(computed_t, df)
            self.assertAlmostEqual(p_back, 0.05, places=12)

    def test_betainc_exact_threshold_and_symmetry_midpoint(self):
        # Symmetric midpoint cases where x == (a + 1) / (a + b + 2) == 0.5
        self.assertEqual(convergence.betainc(0.5, 0.5, 0.5), 0.5)
        self.assertEqual(convergence.betainc(1.0, 1.0, 0.5), 0.5)
        self.assertEqual(convergence.betainc(2.0, 2.0, 0.5), 0.5)

        # Asymmetric exact continued-fraction threshold points x == (a + 1) / (a + b + 2)
        self.assertAlmostEqual(
            convergence.betainc(1.0, 2.0, 0.4), 0.64, places=14
        )
        self.assertAlmostEqual(
            convergence.betainc(1.0, 5.0, 0.25), 0.7626953125, places=14
        )
        self.assertAlmostEqual(
            convergence.betainc(5.0, 1.0, 0.75), 0.2373046875, places=14
        )

    def test_betainc_extreme_large_parameters(self):
        # Verified against 80-digit arbitrary-precision incomplete beta oracle
        self.assertAlmostEqual(
            convergence.betainc(1e6, 1e6, 0.5 + 1e-8),
            0.5000112837903157,
            delta=1e-12,
        )
        self.assertAlmostEqual(
            convergence.betainc(1e6, 5e5, 2.0 / 3.0 - 2.2e-7),
            0.4996951974702141,
            delta=1e-12,
        )

    def test_student_t_subnormal_underflow_and_overflow(self):
        # Tiny non-zero t_stat where t_stat**2 underflows to 0.0
        self.assertEqual(convergence.student_t_two_sided_pvalue(1e-200, 5.0), 1.0)
        # Huge finite t_stat where t_stat**2 overflows to inf
        self.assertEqual(convergence.student_t_two_sided_pvalue(1e200, 5.0), 0.0)

        # Welch comparison between subnormal samples and zero-mean symmetric samples
        # produces non-zero subnormal t_stat (~2.47e-311) whose square underflows to 0.0
        res = convergence.compare_samples_welch(
            [1e-310, 2e-310, 1.5e-310, 1.2e-310],
            [-10.0, 10.0, -10.0, 10.0],
        )
        self.assertEqual(res.p_value, 1.0)
        self.assertFalse(res.statistically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_student_t_extreme_large_df_precision(self):
        # Exact 100-digit oracle reference values for (df, confidence) -> expected_t_crit
        crit_oracle = [
            (1e6, 0.90, 1.6448551507220406),
            (1e6, 0.95, 1.9599663568141064),
            (1e6, 0.99, 2.575834220105334),
            (1e6, 0.9999, 3.890607581803092),
            (1e7, 0.90, 1.6448537793284017),
            (1e7, 0.95, 1.9599642217672049),
            (1e7, 0.99, 2.5758297952037488),
            (1e7, 0.9999, 3.890593455947045),
        ]
        for df, conf, expected_t in crit_oracle:
            alpha = 1.0 - conf
            t_crit = convergence.student_t_critical_value(df, confidence=conf)
            rel_err_t = abs(t_crit - expected_t) / expected_t
            self.assertLess(rel_err_t, 1e-13)

            p_back = convergence.student_t_two_sided_pvalue(t_crit, df)
            rel_err_p = abs(p_back - alpha) / alpha
            self.assertLess(rel_err_p, 1e-13)

        # Direct two-sided p-value evaluations at df = 1e6 and df = 1e7 vs 100-digit oracle
        pval_oracle = [
            (1.5, 1e6, 0.13361471823679277),
            (1.96, 1e6, 0.04999606758526979),
            (2.5, 1e6, 0.012419489502163246),
            (3.5, 1e6, 0.000465278393681035),
            (1.5, 1e7, 0.13361443410762944),
            (1.96, 1e7, 0.04999581802531419),
            (2.5, 1e7, 0.01241934653657847),
            (3.5, 1e7, 0.00046526018160684913),
        ]
        for t_stat, df, expected_p in pval_oracle:
            p_val = convergence.student_t_two_sided_pvalue(t_stat, df)
            rel_err = abs(p_val - expected_p) / expected_p
            self.assertLess(rel_err, 1e-13)


class TestMADOutlierFilter(unittest.TestCase):
    """Tests for Feature F2: filter_outliers_mad."""

    def test_small_sample_sizes_untouched(self):
        self.assertEqual(convergence.filter_outliers_mad([]), ([], []))
        self.assertEqual(convergence.filter_outliers_mad([1000.0]), ([1000.0], []))
        self.assertEqual(
            convergence.filter_outliers_mad([1000.0, 200.0]),
            ([1000.0, 200.0], []),
        )

    def test_standard_mad_outlier_removal(self):
        samples = [1000.0, 1005.0, 995.0, 1002.0, 200.0]
        inliers, outliers = convergence.filter_outliers_mad(samples)
        self.assertEqual(inliers, [1000.0, 1005.0, 995.0, 1002.0])
        self.assertEqual(outliers, [200.0])

    def test_zero_mad_extreme_stall_ec6(self):
        samples = [1000.0, 1000.0, 1000.0, 200.0]
        inliers, outliers = convergence.filter_outliers_mad(samples)
        self.assertEqual(inliers, [1000.0, 1000.0, 1000.0])
        self.assertEqual(outliers, [200.0])

    def test_zero_mad_within_noise_floor(self):
        samples = [1000.0, 1000.0, 1000.1]
        inliers, outliers = convergence.filter_outliers_mad(samples)
        self.assertEqual(inliers, [1000.0, 1000.0, 1000.1])
        self.assertEqual(outliers, [])

    def test_majority_retention_guard(self):
        samples = [10.0, 10.0, 20.0, 20.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=0.1)
        self.assertEqual(len(inliers), 2)
        self.assertEqual(inliers, [10.0, 10.0])
        self.assertEqual(outliers, [20.0, 20.0])


class TestEvaluateConvergence(unittest.TestCase):
    """Tests for Feature F3: ConvergenceResult and evaluate_convergence."""

    def test_low_variance_converges_early(self):
        res = convergence.evaluate_convergence(
            [1000.0, 1005.0, 995.0, 1002.0],
            min_iterations=3,
            convergence_threshold=0.05,
        )
        self.assertTrue(res.converged)
        self.assertLess(res.relative_margin_of_error, 0.05)
        self.assertAlmostEqual(res.representative_value, 1000.5, places=6)
        self.assertEqual(res.n_total, 4)
        self.assertEqual(res.n_steady, 4)
        self.assertEqual(res.n_inliers, 4)
        self.assertEqual(res.outliers_removed, [])

    def test_high_variance_does_not_converge(self):
        res = convergence.evaluate_convergence(
            [500.0, 1500.0, 700.0, 1300.0],
            min_iterations=3,
            convergence_threshold=0.05,
        )
        self.assertFalse(res.converged)
        self.assertGreater(res.relative_margin_of_error, 0.05)
        self.assertAlmostEqual(res.representative_value, 1000.0, places=6)

    def test_min_iterations_enforced(self):
        res = convergence.evaluate_convergence(
            [1000.0, 1000.0],
            min_iterations=3,
            convergence_threshold=0.05,
        )
        self.assertFalse(res.converged)
        self.assertEqual(res.relative_margin_of_error, 0.0)
        self.assertEqual(res.n_steady, 2)

    def test_warmup_iteration_separation_ec7(self):
        res = convergence.evaluate_convergence(
            [450.0, 1000.0, 1002.0, 998.0],
            min_iterations=3,
            warmup_iterations=1,
        )
        self.assertTrue(res.converged)
        self.assertEqual(res.warmup_samples, [450.0])
        self.assertEqual(res.n_total, 4)
        self.assertEqual(res.n_steady, 3)
        self.assertAlmostEqual(res.representative_value, 1000.0, places=6)
        self.assertAlmostEqual(res.raw_mean, 1000.0, places=6)

    def test_single_and_empty_samples_ec3_ec4(self):
        res_empty = convergence.evaluate_convergence([])
        self.assertFalse(res_empty.converged)
        self.assertEqual(res_empty.n_total, 0)
        self.assertEqual(res_empty.n_steady, 0)
        self.assertEqual(res_empty.n_inliers, 0)
        self.assertEqual(res_empty.representative_value, 0.0)
        self.assertEqual(res_empty.std_dev, 0.0)
        self.assertTrue(math.isinf(res_empty.ci_half_width))
        self.assertTrue(math.isinf(res_empty.relative_margin_of_error))

        res_single = convergence.evaluate_convergence([1000.0], min_iterations=1)
        self.assertFalse(res_single.converged)
        self.assertEqual(res_single.n_total, 1)
        self.assertEqual(res_single.n_steady, 1)
        self.assertEqual(res_single.n_inliers, 1)
        self.assertEqual(res_single.representative_value, 1000.0)
        self.assertEqual(res_single.std_dev, 0.0)
        self.assertTrue(math.isinf(res_single.ci_half_width))
        self.assertTrue(math.isinf(res_single.relative_margin_of_error))


class TestCompareSamplesWelch(unittest.TestCase):
    """Tests for Feature F4: ComparisonResult and compare_samples_welch."""

    def test_significant_improvement_and_regression_higher_is_better(self):
        baseline = [1000.0, 1005.0, 995.0, 1000.0]
        cand_improve = [1150.0, 1155.0, 1145.0, 1150.0]
        cand_regress = [850.0, 855.0, 845.0, 850.0]

        res_imp = convergence.compare_samples_welch(
            baseline, cand_improve, higher_is_better=True
        )
        self.assertTrue(res_imp.statistically_significant)
        self.assertTrue(res_imp.practically_significant)
        self.assertAlmostEqual(res_imp.delta_pct, 15.0, places=5)
        self.assertEqual(res_imp.verdict, "SIGNIFICANT_IMPROVEMENT")

        res_reg = convergence.compare_samples_welch(
            baseline, cand_regress, higher_is_better=True
        )
        self.assertTrue(res_reg.statistically_significant)
        self.assertTrue(res_reg.practically_significant)
        self.assertAlmostEqual(res_reg.delta_pct, -15.0, places=5)
        self.assertEqual(res_reg.verdict, "SIGNIFICANT_REGRESSION")

    def test_polarity_inversion_lower_is_better_latency(self):
        baseline_lat = [10.0, 10.1, 9.9, 10.0]
        cand_lower_lat = [7.0, 7.1, 6.9, 7.0]
        cand_higher_lat = [13.0, 13.1, 12.9, 13.0]

        res_imp = convergence.compare_samples_welch(
            baseline_lat, cand_lower_lat, higher_is_better=False
        )
        self.assertEqual(res_imp.verdict, "SIGNIFICANT_IMPROVEMENT")

        res_reg = convergence.compare_samples_welch(
            baseline_lat, cand_higher_lat, higher_is_better=False
        )
        self.assertEqual(res_reg.verdict, "SIGNIFICANT_REGRESSION")

    def test_statistically_insignificant_high_variance_noise(self):
        baseline = [900.0, 1100.0, 950.0, 1050.0]
        candidate = [920.0, 1120.0, 960.0, 1060.0]
        res = convergence.compare_samples_welch(baseline, candidate)
        self.assertFalse(res.statistically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_statistically_significant_practically_negligible(self):
        baseline = [1000.0, 1000.1, 999.9, 1000.0]
        candidate = [1005.0, 1005.1, 1004.9, 1005.0]  # +0.5% < min_effect_pct=2.0%
        res = convergence.compare_samples_welch(
            baseline, candidate, min_effect_pct=2.0
        )
        self.assertTrue(res.statistically_significant)
        self.assertFalse(res.practically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_outlier_prefiltering_prevents_welch_corruption_ec6(self):
        baseline = [1000.0, 1002.0, 998.0, 1000.0, 200.0]
        candidate = [1100.0, 1102.0, 1098.0, 1100.0]
        res = convergence.compare_samples_welch(baseline, candidate)
        self.assertAlmostEqual(res.baseline_value, 1000.0, places=5)
        self.assertAlmostEqual(res.candidate_value, 1100.0, places=5)
        self.assertAlmostEqual(res.delta_pct, 10.0, places=5)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_edge_case_ec1_both_zero_variance_equal_and_unequal(self):
        res_eq = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0], [1000.0, 1000.0, 1000.0]
        )
        self.assertEqual(res_eq.t_stat, 0.0)
        self.assertEqual(res_eq.p_value, 1.0)
        self.assertEqual(res_eq.cohens_d, 0.0)
        self.assertEqual(res_eq.verdict, "NOISE / NEUTRAL")

        res_uneq = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0], [1100.0, 1100.0, 1100.0]
        )
        self.assertTrue(math.isinf(res_uneq.t_stat) and res_uneq.t_stat > 0.0)
        self.assertEqual(res_uneq.p_value, 0.0)
        self.assertTrue(math.isinf(res_uneq.cohens_d) and res_uneq.cohens_d > 0.0)
        self.assertEqual(res_uneq.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_edge_case_ec2_one_zero_variance(self):
        res_b_zero = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0],
            [1100.0, 1105.0, 1095.0, 1100.0],
        )
        self.assertAlmostEqual(res_b_zero.degrees_of_freedom, 3.0, places=10)
        self.assertEqual(res_b_zero.verdict, "SIGNIFICANT_IMPROVEMENT")

        res_c_zero = convergence.compare_samples_welch(
            [1000.0, 1005.0, 995.0, 1000.0, 1002.0],
            [1100.0, 1100.0, 1100.0],
        )
        self.assertAlmostEqual(res_c_zero.degrees_of_freedom, 4.0, places=10)
        self.assertEqual(res_c_zero.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_edge_case_ec3_ec4_insufficient_samples(self):
        res = convergence.compare_samples_welch([1000.0], [1100.0, 1100.0])
        self.assertFalse(res.statistically_significant)
        self.assertEqual(res.verdict, "INSUFFICIENT_SAMPLES")

        res_empty = convergence.compare_samples_welch([], [1100.0, 1100.0])
        self.assertFalse(res_empty.statistically_significant)
        self.assertEqual(res_empty.verdict, "INSUFFICIENT_SAMPLES")

    def test_edge_case_zero_baseline_mean(self):
        res_both_zero = convergence.compare_samples_welch(
            [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        )
        self.assertEqual(res_both_zero.delta_pct, 0.0)
        self.assertEqual(res_both_zero.verdict, "NOISE / NEUTRAL")

        res_cand_pos = convergence.compare_samples_welch(
            [0.0, 0.0, 0.0], [10.0, 10.0, 10.0]
        )
        self.assertTrue(math.isinf(res_cand_pos.delta_pct) and res_cand_pos.delta_pct > 0.0)
        self.assertEqual(res_cand_pos.verdict, "SIGNIFICANT_IMPROVEMENT")


class TestDockerfilePackaging(unittest.TestCase):
    """Tests for Feature F5: Dockerfile packaging of convergence.py."""

    def test_dockerfile_copies_convergence_py(self):
        dockerfile_path = (
            Path(__file__).resolve().parent / "gcsfuse-perf-base.dockerfile"
        )
        content = dockerfile_path.read_text(encoding="utf-8")
        expected_line = (
            "COPY convergence.py /usr/local/lib/python3.13/site-packages/convergence.py"
        )
        self.assertIn(expected_line, content)


if __name__ == "__main__":
    unittest.main()
