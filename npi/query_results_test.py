#!/usr/bin/env python3
"""Comprehensive hermetic unit tests for query_results.py (Milestone M3 — Features F8 & F9).

Verifies:
  1. Disaggregated per-iteration sample extraction across fio_* and go_client_* tables
  2. Strict non-aggregation invariant (Sequential Read 'read', Random Read 'randread',
     and Streaming Write 'write' are NEVER collapsed or averaged together)
  3. Canonical single-value point estimation, MAD outlier rejection, Student's t CI
     half-width, relative margin of error, and convergence status propagation
  4. All edge cases (empty tables, CalledProcessError, malformed JSON, missing workload N=0,
     single iteration N=1, zero variance s=0, extreme transient stalls, legacy flat rows)
  5. Welch's two-sample t-test statistical significance & practical effect size verdicts
     with metric polarity awareness (higher_is_better=True for throughput, False for latency)
  6. Dedicated disaggregated CLI reporting in query_results.main()
"""

import io
import json
import math
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

import convergence
import query_results


def _make_per_iter_rows(workload_map, fio_version="fio-3.36"):
    """Helper to generate per-iteration row dicts matching bq query JSON output."""
    max_iters = max((len(v[0]) for v in workload_map.values()), default=0)
    rows = []
    for idx in range(max_iters):
        row = {"iteration": idx + 1, "fio_version": fio_version}
        for w_name, col_bw, col_lat in (
            ("read", "seq_read_bw_mbs", "seq_read_lat_ms"),
            ("randread", "rand_read_bw_mbs", "rand_read_lat_ms"),
            ("write", "write_bw_mbs", "write_lat_ms"),
        ):
            if w_name in workload_map and idx < len(workload_map[w_name][0]):
                row[col_bw] = workload_map[w_name][0][idx]
                row[col_lat] = workload_map[w_name][1][idx]
            else:
                row[col_bw] = None
                row[col_lat] = None
        rows.append(row)
    return rows


class TestQueryResultsDisaggregationAndExtraction(unittest.TestCase):
    """Suite 1: Disaggregated per-iteration extraction & SQL query invariants (7 tests)."""

    @patch("subprocess.run")
    def test_01_fio_sql_query_contains_explicit_rw_disaggregation_and_unnest(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(
                _make_per_iter_rows({"read": ([2500.0, 2505.0, 2495.0], [1.2, 1.2, 1.2])})
            )
        )
        query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        sql_sent = mock_run.call_args[0][0][-1]
        self.assertIn("UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs))", sql_sent)
        self.assertIn('JSON_VALUE(fio_json_output, \'$.\"global options\".rw\')', sql_sent)
        self.assertIn("'read'", sql_sent)
        self.assertIn("'randread'", sql_sent)

    @patch("subprocess.run")
    def test_02_go_client_sql_query_avoids_unnest_and_includes_read_bw_mbps(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(
                [
                    {"iteration": 1, "fio_version": "go-client", "seq_read_bw_mbs": 3100.0, "seq_read_lat_ms": 1.1},
                    {"iteration": 2, "fio_version": "go-client", "seq_read_bw_mbs": 3110.0, "seq_read_lat_ms": 1.1},
                    {"iteration": 3, "fio_version": "go-client", "seq_read_bw_mbs": 3090.0, "seq_read_lat_ms": 1.1},
                ]
            )
        )
        metrics = query_results.get_table_metrics("proj", "ds", "go_client_read_grpc")
        sql_sent = mock_run.call_args[0][0][-1]
        self.assertIn("read_bw_mbps", sql_sent)
        self.assertNotIn("UNNEST", sql_sent)
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 3100.0, delta=1e-3)
        self.assertEqual(metrics["fio_version"], "go-client")

    @patch("subprocess.run")
    def test_03_strict_non_aggregation_read_vs_randread_vs_write(self, mock_run):
        rows = _make_per_iter_rows(
            {
                "read": ([2000.0, 2000.0, 2000.0], [1.0, 1.0, 1.0]),
                "randread": ([500.0, 500.0, 500.0], [3.5, 3.5, 3.5]),
                "write": ([1000.0, 1000.0, 1000.0], [2.0, 2.0, 2.0]),
            }
        )
        mock_run.return_value = MagicMock(stdout=json.dumps(rows))
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2000.0)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 500.0)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 1000.0)
        # Explicitly verify read and randread are NEVER averaged together into 1250.0
        self.assertNotAlmostEqual(metrics["seq_read_bw_mbs"], 1250.0)

    @patch("subprocess.run")
    def test_04_per_iteration_sample_vectors_and_convergence_summaries_populated(self, mock_run):
        rows = _make_per_iter_rows(
            {
                "read": ([2400.0, 2410.0, 2390.0, 2400.0], [1.2, 1.1, 1.3, 1.2]),
                "randread": ([1100.0, 1105.0, 1095.0, 1100.0], [2.4, 2.5, 2.3, 2.4]),
                "write": ([750.0, 755.0, 745.0, 750.0], [3.0, 3.1, 2.9, 3.0]),
            }
        )
        mock_run.return_value = MagicMock(stdout=json.dumps(rows))
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        self.assertIn("workloads", metrics)
        for w in ("read", "randread", "write"):
            self.assertIn(w, metrics["workloads"])
            w_entry = metrics["workloads"][w]
            self.assertEqual(len(w_entry["bw_samples"]), 4)
            self.assertEqual(len(w_entry["lat_samples"]), 4)
            self.assertIsInstance(w_entry["bw_summary"], convergence.ConvergenceResult)
            self.assertIsInstance(w_entry["lat_summary"], convergence.ConvergenceResult)
            self.assertTrue(w_entry["bw_summary"].converged)

    @patch("subprocess.run")
    def test_05_convergence_threshold_and_confidence_level_propagation(self, mock_run):
        tight_rows = _make_per_iter_rows(
            {"write": ([1000.0, 1002.0, 998.0, 1000.0], [2.0, 2.0, 2.0, 2.0])}
        )
        wide_rows = _make_per_iter_rows(
            {"write": ([600.0, 1400.0, 700.0, 1300.0], [2.0, 2.0, 2.0, 2.0])}
        )
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(tight_rows)),
            MagicMock(stdout=json.dumps(wide_rows)),
        ]
        m_tight = query_results.get_table_metrics(
            "proj", "ds", "fio_write_grpc", convergence_threshold=0.02, confidence_level=0.99
        )
        m_wide = query_results.get_table_metrics(
            "proj", "ds", "fio_write_grpc", convergence_threshold=0.02, confidence_level=0.99
        )
        bw_sum_tight = m_tight["workloads"]["write"]["bw_summary"]
        bw_sum_wide = m_wide["workloads"]["write"]["bw_summary"]
        self.assertEqual(bw_sum_tight.convergence_threshold, 0.02)
        self.assertEqual(bw_sum_tight.confidence_level, 0.99)
        self.assertTrue(bw_sum_tight.converged)
        self.assertFalse(bw_sum_wide.converged)

    @patch("subprocess.run")
    def test_06_legacy_single_row_pre_aggregated_payload_compatibility(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(
                [
                    {
                        "fio_version": "fio-3.36",
                        "seq_read_bw_mbs": 2500.5,
                        "rand_read_bw_mbs": 1200.0,
                        "write_bw_mbs": 450.25,
                        "seq_read_lat_ms": 1.2,
                        "rand_read_lat_ms": 2.5,
                        "write_lat_ms": 3.0,
                    }
                ]
            )
        )
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_http1")
        self.assertEqual(metrics["fio_version"], "fio-3.36")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2500.5)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 1200.0)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 450.25)
        self.assertAlmostEqual(metrics["seq_read_lat_ms"], 1.2)
        self.assertAlmostEqual(metrics["rand_read_lat_ms"], 2.5)
        self.assertAlmostEqual(metrics["write_lat_ms"], 3.0)

    @patch("subprocess.run")
    def test_07_go_client_row_extraction_from_legacy_flat_column_and_fio_json_output(self, mock_run):
        flat_rows = [
            {"iteration": 1, "read_bw_mbps": 3100.0},
            {"iteration": 2, "read_bw_mbps": 3110.0},
            {"iteration": 3, "read_bw_mbps": 3090.0},
        ]
        raw_json_rows = [
            {
                "iteration": i + 1,
                "fio_json_output": json.dumps(
                    {
                        "fio version": "go-client",
                        "global options": {"rw": "read"},
                        "jobs": [
                            {
                                "read": {
                                    "bw": bw_mbs * 1000000.0 / 1024.0,
                                    "lat_ns": {"mean": 1500000.0},
                                }
                            }
                        ],
                    }
                ),
            }
            for i, bw_mbs in enumerate([2900.0, 2905.0, 2895.0])
        ]
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(flat_rows)),
            MagicMock(stdout=json.dumps(raw_json_rows)),
        ]
        m_flat = query_results.get_table_metrics("proj", "ds", "go_client_read_grpc")
        m_json = query_results.get_table_metrics("proj", "ds", "go_client_read_grpc")
        self.assertAlmostEqual(m_flat["seq_read_bw_mbs"], 3100.0, delta=1e-3)
        self.assertAlmostEqual(m_json["seq_read_bw_mbs"], 2900.0, delta=1e-3)
        self.assertAlmostEqual(m_json["seq_read_lat_ms"], 1.5, delta=1e-3)


class TestQueryResultsEdgeCases(unittest.TestCase):
    """Suite 2: Exhaustive edge-case handling & fallback contracts (7 tests)."""

    EXPECTED_FALLBACK = {
        "seq_read_bw_mbs": 0.0,
        "rand_read_bw_mbs": 0.0,
        "write_bw_mbs": 0.0,
        "seq_read_lat_ms": 0.0,
        "rand_read_lat_ms": 0.0,
        "write_lat_ms": 0.0,
        "fio_version": "N/A",
    }

    @patch("subprocess.run")
    def test_01_empty_table_returns_exact_7_key_fallback_dict(self, mock_run):
        mock_run.return_value = MagicMock(stdout="[]")
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_http1")
        self.assertEqual(metrics, self.EXPECTED_FALLBACK)

    @patch("subprocess.run")
    def test_02_called_process_error_returns_exact_7_key_fallback_dict(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "bq", stderr="Table not found")
        metrics = query_results.get_table_metrics("proj", "ds", "nonexistent_table")
        self.assertEqual(metrics, self.EXPECTED_FALLBACK)

    @patch("subprocess.run")
    def test_03_malformed_json_stdout_returns_exact_7_key_fallback_dict(self, mock_run):
        mock_run.return_value = MagicMock(stdout="NOT_VALID_JSON")
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_http1")
        self.assertEqual(metrics, self.EXPECTED_FALLBACK)

    @patch("subprocess.run")
    def test_04_missing_workload_n0_within_valid_table(self, mock_run):
        rows = _make_per_iter_rows({"read": ([2200.0, 2210.0, 2190.0], [1.1, 1.1, 1.1])})
        mock_run.return_value = MagicMock(stdout=json.dumps(rows))
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2200.0, delta=1e-3)
        self.assertEqual(metrics["rand_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["write_bw_mbs"], 0.0)
        self.assertEqual(metrics["workloads"]["randread"]["bw_summary"].n_total, 0)
        self.assertFalse(metrics["workloads"]["randread"]["bw_summary"].converged)

    @patch("subprocess.run")
    def test_05_single_sample_n1_handling(self, mock_run):
        rows = _make_per_iter_rows({"read": ([1800.0], [1.5])})
        mock_run.return_value = MagicMock(stdout=json.dumps(rows))
        metrics = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        bw_sum = metrics["workloads"]["read"]["bw_summary"]
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 1800.0)
        self.assertEqual(bw_sum.std_dev, 0.0)
        self.assertEqual(bw_sum.n_total, 1)
        self.assertFalse(bw_sum.converged)
        self.assertFalse(math.isfinite(bw_sum.ci_half_width))

    @patch("subprocess.run")
    def test_06_zero_variance_s0_identical_samples_nonzero_and_zero(self, mock_run):
        nonzero_rows = _make_per_iter_rows({"read": ([1500.0, 1500.0, 1500.0], [1.0, 1.0, 1.0])})
        zero_rows = _make_per_iter_rows({"read": ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])})
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(nonzero_rows)),
            MagicMock(stdout=json.dumps(zero_rows)),
        ]
        m_nz = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        m_z = query_results.get_table_metrics("proj", "ds", "fio_read_grpc")
        bw_nz = m_nz["workloads"]["read"]["bw_summary"]
        bw_z = m_z["workloads"]["read"]["bw_summary"]
        self.assertEqual(bw_nz.std_dev, 0.0)
        self.assertEqual(bw_nz.ci_half_width, 0.0)
        self.assertEqual(bw_nz.relative_margin_of_error, 0.0)
        self.assertTrue(bw_nz.converged)
        self.assertEqual(bw_z.std_dev, 0.0)
        self.assertEqual(bw_z.ci_half_width, 0.0)
        self.assertEqual(bw_z.relative_margin_of_error, 0.0)
        self.assertTrue(bw_z.converged)

    @patch("subprocess.run")
    def test_07_extreme_transient_outlier_rejected_in_get_table_metrics(self, mock_run):
        mad_zero_rows = _make_per_iter_rows(
            {"read": ([2000.0, 2000.0, 300.0, 2000.0], [1.0, 1.0, 8.0, 1.0])}
        )
        mad_pos_rows = _make_per_iter_rows(
            {
                "read": (
                    [1000.0, 1005.0, 995.0, 1002.0, 998.0, 200.0],
                    [1.2, 1.2, 1.2, 1.2, 1.2, 9.5],
                )
            }
        )
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(mad_zero_rows)),
            MagicMock(stdout=json.dumps(mad_pos_rows)),
        ]
        m1 = query_results.get_table_metrics("proj", "ds", "fio_read_http1")
        m2 = query_results.get_table_metrics("proj", "ds", "fio_read_http1")
        self.assertAlmostEqual(m1["seq_read_bw_mbs"], 2000.0, delta=1e-3)
        self.assertEqual(m1["workloads"]["read"]["bw_summary"].outliers_removed, [300.0])
        self.assertAlmostEqual(m2["seq_read_bw_mbs"], 1000.0, delta=1e-3)
        self.assertEqual(m2["workloads"]["read"]["bw_summary"].outliers_removed, [200.0])


class TestQueryResultsWelchSignificanceVerdicts(unittest.TestCase):
    """Suite 3: Welch's two-sample t-test & practical effect size verdicts (6 tests)."""

    def test_01_throughput_significant_improvement_verdict(self):
        comp = convergence.compare_samples_welch(
            baseline_samples=[1000.0, 1002.0, 998.0, 1000.0],
            candidate_samples=[1150.0, 1152.0, 1148.0, 1150.0],
            higher_is_better=True,
        )
        self.assertEqual(comp.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertAlmostEqual(comp.delta_pct, 15.0, delta=1e-3)
        self.assertTrue(comp.statistically_significant)
        self.assertTrue(comp.practically_significant)

    def test_02_throughput_significant_regression_verdict(self):
        comp = convergence.compare_samples_welch(
            baseline_samples=[1000.0, 1002.0, 998.0, 1000.0],
            candidate_samples=[840.0, 842.0, 838.0, 840.0],
            higher_is_better=True,
        )
        self.assertEqual(comp.verdict, "SIGNIFICANT_REGRESSION")
        self.assertAlmostEqual(comp.delta_pct, -16.0, delta=1e-3)
        self.assertTrue(comp.statistically_significant)
        self.assertTrue(comp.practically_significant)

    def test_03_noise_neutral_verdict_high_variance_or_sub_threshold_delta(self):
        comp_noisy = convergence.compare_samples_welch(
            baseline_samples=[800.0, 1200.0, 900.0, 1100.0],
            candidate_samples=[840.0, 1240.0, 940.0, 1140.0],
            higher_is_better=True,
        )
        self.assertEqual(comp_noisy.verdict, "NOISE / NEUTRAL")

        comp_trivial = convergence.compare_samples_welch(
            baseline_samples=[1000.0, 1000.01, 999.99, 1000.0],
            candidate_samples=[1003.0, 1003.01, 1002.99, 1003.0],
            min_effect_pct=2.0,
            higher_is_better=True,
        )
        self.assertTrue(comp_trivial.statistically_significant)
        self.assertFalse(comp_trivial.practically_significant)
        self.assertEqual(comp_trivial.verdict, "NOISE / NEUTRAL")

    def test_04_insufficient_samples_verdict_when_n_lt_2(self):
        comp_a = convergence.compare_samples_welch([1000.0], [1200.0, 1205.0, 1195.0])
        comp_b = convergence.compare_samples_welch([1000.0, 1005.0, 995.0], [1200.0])
        self.assertEqual(comp_a.verdict, "INSUFFICIENT_SAMPLES")
        self.assertEqual(comp_b.verdict, "INSUFFICIENT_SAMPLES")

    def test_05_latency_polarity_inversion_lower_is_better(self):
        comp_drop = convergence.compare_samples_welch(
            baseline_samples=[5.0, 5.1, 4.9, 5.0],
            candidate_samples=[2.5, 2.6, 2.4, 2.5],
            higher_is_better=False,
        )
        comp_spike = convergence.compare_samples_welch(
            baseline_samples=[5.0, 5.1, 4.9, 5.0],
            candidate_samples=[8.0, 8.1, 7.9, 8.0],
            higher_is_better=False,
        )
        self.assertEqual(comp_drop.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertEqual(comp_spike.verdict, "SIGNIFICANT_REGRESSION")

    def test_06_zero_variance_and_outlier_filtering_in_welch_comparison(self):
        comp_eq = convergence.compare_samples_welch([1000.0] * 3, [1000.0] * 3)
        comp_diff = convergence.compare_samples_welch([1000.0] * 3, [1150.0] * 3)
        comp_outlier = convergence.compare_samples_welch(
            [1000.0, 1002.0, 998.0, 150.0],
            [1000.0, 1002.0, 998.0, 1000.0],
        )
        self.assertEqual(comp_eq.verdict, "NOISE / NEUTRAL")
        self.assertEqual(comp_eq.p_value, 1.0)
        self.assertEqual(comp_diff.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertEqual(comp_diff.p_value, 0.0)
        self.assertEqual(comp_outlier.verdict, "NOISE / NEUTRAL")


class TestQueryResultsDetailedBreakdown(unittest.TestCase):
    """Suite 4: Granular detailed breakdown queries & disaggregation (2 tests)."""

    @patch("subprocess.run")
    def test_01_get_detailed_table_metrics_fio_and_go_client_support(self, mock_run):
        det_rows = [
            {"iteration": 1, "workload_type": "read", "block_size": "1M", "file_size": "1G", "read_bw_mbs": 2500.0, "read_lat_ms": 1.2, "read_iops": 2500.0},
            {"iteration": 2, "workload_type": "read", "block_size": "1M", "file_size": "1G", "read_bw_mbs": 2510.0, "read_lat_ms": 1.2, "read_iops": 2510.0},
            {"iteration": 3, "workload_type": "read", "block_size": "1M", "file_size": "1G", "read_bw_mbs": 2490.0, "read_lat_ms": 1.2, "read_iops": 2490.0},
        ]
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(det_rows)),
            MagicMock(stdout="[]"),
        ]
        res = query_results.get_detailed_table_metrics("proj", "ds", "fio_read_grpc")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["workload_type"], "read")
        self.assertEqual(res[0]["block_size"], "1M")
        self.assertEqual(res[0]["file_size"], "1G")
        self.assertAlmostEqual(res[0]["read_bw_mbs"], 2500.0)
        self.assertTrue(res[0]["converged"])

        empty_res = query_results.get_detailed_table_metrics("proj", "ds", "go_client_read_grpc")
        self.assertEqual(empty_res, [])

    @patch("subprocess.run")
    def test_02_detailed_breakdown_preserves_disaggregation(self, mock_run):
        det_rows = [
            {"iteration": 1, "workload_type": "read", "block_size": "1M", "file_size": "1G", "read_bw_mbs": 2400.0, "read_lat_ms": 1.1},
            {"iteration": 1, "workload_type": "randread", "block_size": "1M", "file_size": "1G", "read_bw_mbs": 950.0, "read_lat_ms": 2.8},
            {"iteration": 1, "workload_type": "write", "block_size": "1M", "file_size": "1G", "write_bw_mbs": 800.0, "write_lat_ms": 3.2},
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(det_rows))
        res = query_results.get_detailed_table_metrics("proj", "ds", "fio_read_grpc")
        self.assertEqual(len(res), 3)
        self.assertEqual([r["workload_type"] for r in res], ["read", "randread", "write"])
        self.assertAlmostEqual(res[0]["read_bw_mbs"], 2400.0)
        self.assertAlmostEqual(res[1]["read_bw_mbs"], 950.0)
        self.assertAlmostEqual(res[2]["write_bw_mbs"], 800.0)


class TestQueryResultsMainCLI(unittest.TestCase):
    """Suite 5: End-to-end CLI output formatting & baseline comparison in main() (4 tests)."""

    @patch("subprocess.run")
    def test_01_main_cli_without_baseline_prints_dedicated_disaggregated_tables(self, mock_run):
        rows = _make_per_iter_rows(
            {
                "read": ([2500.0, 2505.0, 2495.0], [1.2, 1.2, 1.2]),
                "randread": ([1200.0, 1205.0, 1195.0], [2.5, 2.5, 2.5]),
                "write": ([800.0, 805.0, 795.0], [3.0, 3.0, 3.0]),
            }
        )
        mock_run.return_value = MagicMock(stdout=json.dumps(rows))
        buf = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "query_results.py",
                "--project-id",
                "test-p",
                "--dataset-id",
                "cand_ds",
                "--table-types",
                "fio_read_grpc",
                "fio_write_grpc",
            ],
        ), patch.object(sys, "stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("Sequential Read Performance (read)", output)
        self.assertIn("Random Read Performance (randread)", output)
        self.assertIn("Streaming Write Performance (write)", output)
        self.assertIn("2500.00", output)
        self.assertIn("1200.00", output)
        self.assertIn("800.00", output)

    @patch("subprocess.run")
    def test_02_main_cli_with_baseline_prints_independent_welch_verdicts_per_workload(self, mock_run):
        base_rows = _make_per_iter_rows(
            {
                "read": ([1000.0, 1002.0, 998.0, 1000.0], [1.2, 1.2, 1.2, 1.2]),
                "randread": ([1000.0, 1002.0, 998.0, 1000.0], [2.5, 2.5, 2.5, 2.5]),
                "write": ([1000.0, 1002.0, 998.0, 1000.0], [3.0, 3.0, 3.0, 3.0]),
            }
        )
        cand_rows = _make_per_iter_rows(
            {
                "read": ([1180.0, 1182.0, 1178.0, 1180.0], [1.2, 1.2, 1.2, 1.2]),
                "randread": ([850.0, 852.0, 848.0, 850.0], [2.5, 2.5, 2.5, 2.5]),
                "write": ([1004.0, 1006.0, 1002.0, 1004.0], [3.0, 3.0, 3.0, 3.0]),
            }
        )

        def _fake_bq(cmd, **kwargs):
            sql = cmd[-1]
            if "base_ds" in sql:
                return MagicMock(stdout=json.dumps(base_rows))
            return MagicMock(stdout=json.dumps(cand_rows))

        mock_run.side_effect = _fake_bq
        buf = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "query_results.py",
                "--project-id",
                "test-p",
                "--dataset-id",
                "cand_ds",
                "--baseline-dataset-id",
                "base_ds",
                "--table-types",
                "fio_read_grpc",
                "fio_write_grpc",
            ],
        ), patch.object(sys, "stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("SIGNIFICANT_IMPROVEMENT", output)
        self.assertIn("SIGNIFICANT_REGRESSION", output)
        self.assertIn("NOISE / NEUTRAL", output)

    @patch("subprocess.run")
    def test_03_main_cli_with_missing_or_empty_baseline_degrades_gracefully(self, mock_run):
        cand_rows = _make_per_iter_rows(
            {"read": ([2000.0, 2005.0, 1995.0], [1.2, 1.2, 1.2])}
        )

        def _fake_bq(cmd, **kwargs):
            sql = cmd[-1]
            if "missing_base_ds" in sql:
                return MagicMock(stdout="[]")
            return MagicMock(stdout=json.dumps(cand_rows))

        mock_run.side_effect = _fake_bq
        buf = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "query_results.py",
                "--project-id",
                "test-p",
                "--dataset-id",
                "cand_ds",
                "--baseline-dataset-id",
                "missing_base_ds",
                "--table-types",
                "fio_read_grpc",
            ],
        ), patch.object(sys, "stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("2000.00", output)
        self.assertIn("INSUFFICIENT_SAMPLES", output)

    @patch("subprocess.run")
    def test_04_main_cli_with_detailed_flag_prints_granular_breakdown(self, mock_run):
        summary_rows = _make_per_iter_rows(
            {"read": ([2400.0, 2405.0, 2395.0], [1.1, 1.1, 1.1])}
        )
        detailed_rows = [
            {
                "iteration": 1,
                "workload_type": "read",
                "block_size": "1M",
                "file_size": "1G",
                "read_bw_mbs": 2400.0,
                "read_lat_ms": 1.1,
            }
        ]

        def _fake_bq(cmd, **kwargs):
            sql = cmd[-1]
            if "block_size" in sql:
                return MagicMock(stdout=json.dumps(detailed_rows))
            return MagicMock(stdout=json.dumps(summary_rows))

        mock_run.side_effect = _fake_bq
        buf = io.StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "query_results.py",
                "--project-id",
                "test-p",
                "--dataset-id",
                "cand_ds",
                "--table-types",
                "fio_read_grpc",
                "--detailed",
            ],
        ), patch.object(sys, "stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("DETAILED METRICS BREAKDOWN", output)
        self.assertIn("Detailed Table: fio_read_grpc", output)
        self.assertIn("2400.00", output)


if __name__ == "__main__":
    unittest.main()
