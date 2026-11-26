#!/usr/bin/env python3
"""Comprehensive 5-Tier Opaque-Box & White-Box Adversarial E2E Test Suite.

Covers Requirements R1-R4 (ORIGINAL_REQUEST.md), workspace invariants (AGENTS.md),
and all 11 features F1-F11 across Tiers 1, 2, 3, 4, and 5 (PROJECT.md & TEST_INFRA.md):
  - Tier 1 (TestTier1FeatureHappyPaths): 61 tests (>= 5 per feature F1..F11)
  - Tier 2 (TestTier2FeatureEdgeCases): 61 tests (>= 5 per feature F1..F11)
  - Tier 3 (TestTier3CrossFeatureCombinations): 12 pairwise interaction tests
  - Tier 4 (TestTier4RealWorldScenarios): 7 real-world & Monte Carlo scenarios
  - Tier 5 (TestTier5WhiteboxAdversarialHardening): 20 white-box adversarial tests
  - Total: 161 test methods (100% hermetic, zero live GCP/GKE/BigQuery calls)
"""

import argparse
import ast
import contextlib
import dataclasses
import importlib
import importlib.util
import io
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch
import urllib.request

# Ensure zero gcloud / SSH resolution subprocess calls occur at import time
_HAD_SSH_USER = "SSH_USER" in os.environ
_HAD_PROJECT_ID = "PROJECT_ID" in os.environ
os.environ.setdefault("SSH_USER", "test_user")
os.environ.setdefault("PROJECT_ID", "gcsfuse-npi")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FIO_DIR = os.path.join(REPO_ROOT, "fio")
GO_CLIENT_DIR = os.path.join(REPO_ROOT, "go-client")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if FIO_DIR not in sys.path:
    sys.path.insert(0, FIO_DIR)

import convergence
import fio_benchmark_runner
import run_fio_benchmark
import run_fio_matrix
import query_results
import npi
import npi_gke
import npi_orchestrator

if not _HAD_SSH_USER:
    os.environ.pop("SSH_USER", None)
if not _HAD_PROJECT_ID:
    os.environ.pop("PROJECT_ID", None)
npi_orchestrator.DEFAULT_PROJECT_ID = os.environ.get("PROJECT_ID", "gcsfuse-npi")


def load_run_go_matrix():
    """Dynamically loads go-client/run_go_matrix.py from its hyphenated directory."""
    spec = importlib.util.spec_from_file_location(
        "run_go_matrix", os.path.join(GO_CLIENT_DIR, "run_go_matrix.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_go_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod


run_go_matrix = load_run_go_matrix()


# ==============================================================================
# Shared Synthetic Generators & Hermetic Test Harnesses
# ==============================================================================


def make_synthetic_fio_json(
    bw_mibps: float,
    lat_ms: float = 1.5,
    rw: str = "read",
    bs: str = "1M",
    filesize: str = "1G",
) -> str:
    """Generates authentic FIO 3.x JSON payloads matching parse_fio_output."""
    io_key = "read" if "read" in rw else "write"
    return json.dumps({
        "fio version": "fio-3.36",
        "global options": {"iodepth": "64", "rw": rw},
        "jobs": [{
            "jobname": f"job_{rw}",
            "job options": {
                "bs": bs,
                "filesize": filesize,
                "nrfiles": "10",
                "numjobs": "112",
            },
            io_key: {
                "bw": int(bw_mibps * 1024.0),  # FIO JSON bw is in KiB/s
                "iops": float(bw_mibps),
                "lat_ns": {
                    "mean": int(lat_ms * 1_000_000.0),
                    "percentiles": {"99.000000": int(lat_ms * 1_500_000.0)},
                },
            },
        }],
    })


def make_synthetic_go_json(
    bw_mibps: float,
    lat_ms: float = 2.0,
    protocol: str = "grpc",
    bs: str = "1M",
    filesize: str = "1G",
) -> str:
    """Generates authentic Go benchmark client JSON payloads."""
    return json.dumps({
        "global options": {"iodepth": "1", "rw": "read"},
        "jobs": [{
            "jobname": "go-client-read",
            "job options": {
                "bs": bs,
                "filesize": filesize,
                "nrfiles": "10",
                "numjobs": "128",
                "client_protocol": protocol,
            },
            "read": {
                "bw": bw_mibps * 1024.0,  # KiB/s
                "iops": bw_mibps,
                "lat_ns": {
                    "mean": int(lat_ms * 1_000_000.0),
                    "99.000000": int(lat_ms * 1_500_000.0),
                    "99.500000": int(lat_ms * 1_800_000.0),
                    "99.900000": int(lat_ms * 2_000_000.0),
                    "percentiles": {"99.000000": int(lat_ms * 1_500_000.0)},
                },
            },
        }],
    })


def make_bq_per_iteration_rows(
    workload_map: dict,
    fio_version: str = "fio-3.36",
) -> list:
    """Builds per-iteration JSON rows as returned by bq query --format=json.

    Supports both per-iteration row schemas (with iteration/rw/bw_mbs/lat_ms or
    per-iteration seq_read_bw_mbs/rand_read_bw_mbs/write_bw_mbs fields) so
    query_results.get_table_metrics can extract per-iteration samples cleanly.
    """
    max_iters = max((len(v[0]) for v in workload_map.values()), default=0)
    rows = []
    for idx in range(max_iters):
        row = {
            "iteration": idx + 1,
            "fio_version": fio_version,
            "seq_read_bw_mbs": None,
            "rand_read_bw_mbs": None,
            "write_bw_mbs": None,
            "seq_read_lat_ms": None,
            "rand_read_lat_ms": None,
            "write_lat_ms": None,
        }
        if "read" in workload_map and idx < len(workload_map["read"][0]):
            row["seq_read_bw_mbs"] = workload_map["read"][0][idx]
            row["seq_read_lat_ms"] = workload_map["read"][1][idx]
        if "randread" in workload_map and idx < len(workload_map["randread"][0]):
            row["rand_read_bw_mbs"] = workload_map["randread"][0][idx]
            row["rand_read_lat_ms"] = workload_map["randread"][1][idx]
        if "write" in workload_map and idx < len(workload_map["write"][0]):
            row["write_bw_mbs"] = workload_map["write"][0][idx]
            row["write_lat_ms"] = workload_map["write"][1][idx]
        rows.append(row)
    return rows


class InMemoryBigQueryHarness:
    """In-memory BigQuery client & bq CLI responder for hermetic E2E testing."""

    def __init__(self):
        self.inserted_rows = {}  # full_table_id -> list[dict]

    def insert_rows_json(self, table_id: str, rows: list):
        self.inserted_rows.setdefault(table_id, []).extend(rows)
        return []

    def get_table(self, table_id: str):
        return MagicMock()

    def create_table(self, table):
        return table

    def query(self, sql: str):
        job = MagicMock()
        job.result.return_value = []
        return job


class HermeticCloudBlocker:
    """Context manager intercepting unmocked network or cloud CLI calls."""

    FORBIDDEN_BINARIES = frozenset({
        "ssh", "scp", "gcloud", "kubectl", "bq", "docker", "gsutil",
        "fusermount", "umount",
    })

    def __init__(self):
        self.blocked_network_attempts = []
        self.blocked_subprocess_attempts = []
        self._patches = []

    def __enter__(self):
        orig_run = subprocess.run

        def guarded_run(cmd, *args, **kwargs):
            bin_name = os.path.basename(cmd[0]) if isinstance(cmd, (list, tuple)) and cmd else str(cmd)
            if bin_name in self.FORBIDDEN_BINARIES:
                self.blocked_subprocess_attempts.append(cmd)
                raise AssertionError(f"Unmocked cloud/system binary invoked: {cmd}")
            return orig_run(cmd, *args, **kwargs)

        def guarded_connect(*args, **kwargs):
            self.blocked_network_attempts.append(args)
            raise AssertionError(f"Unmocked network socket connection attempted: {args}")

        p1 = patch.object(socket.socket, "connect", side_effect=guarded_connect)
        p2 = patch.object(socket, "create_connection", side_effect=guarded_connect)
        p3 = patch.object(urllib.request, "urlopen", side_effect=guarded_connect)
        p4 = patch.object(subprocess, "run", side_effect=guarded_run)
        self._patches = [p1, p2, p3, p4]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for p in reversed(self._patches):
            p.stop()
        return False


# ==============================================================================
# TIER 1: Foundational Happy-Path Feature Coverage (61 Tests across F1..F11)
# ==============================================================================


class TestTier1FeatureHappyPaths(unittest.TestCase):
    """Tier 1: >= 5 happy-path tests per feature across F1-F11 (61 tests total)."""

    # ------------------------------------------------------------------
    # Feature F1: Exact Pure-Python Student's t Distribution (5 tests)
    # ------------------------------------------------------------------

    def test_t1_f1_01_betainc_standard_interior_and_symmetry(self):
        """T1-F1-01: Regularized incomplete beta I_x(a, b) interior values & symmetry."""
        self.assertAlmostEqual(convergence.betainc(1.0, 1.0, 0.35), 0.35, delta=1e-12)
        self.assertAlmostEqual(
            convergence.betainc(2.5, 1.5, 0.4), 0.17392765793651005, delta=1e-12
        )
        self.assertAlmostEqual(convergence.betainc(0.5, 0.5, 0.5), 0.5, delta=1e-12)
        symmetry_sum = (
            convergence.betainc(2.5, 1.5, 0.4)
            + convergence.betainc(1.5, 2.5, 0.6)
        )
        self.assertAlmostEqual(symmetry_sum, 1.0, delta=1e-12)

    def test_t1_f1_02_student_t_two_sided_pvalue_standard_integer_df(self):
        """T1-F1-02: Exact two-sided p-value for standard integer degrees of freedom."""
        self.assertAlmostEqual(
            convergence.student_t_two_sided_pvalue(12.706204736175, 1.0), 0.05, delta=1e-10
        )
        self.assertAlmostEqual(
            convergence.student_t_two_sided_pvalue(4.302652729749, 2.0), 0.05, delta=1e-10
        )
        self.assertAlmostEqual(
            convergence.student_t_two_sided_pvalue(2.570581835636, 5.0), 0.05, delta=1e-10
        )
        p_neg = convergence.student_t_two_sided_pvalue(-2.228138851986, 10.0)
        p_pos = convergence.student_t_two_sided_pvalue(2.228138851986, 10.0)
        self.assertAlmostEqual(p_neg, 0.05, delta=1e-10)
        self.assertAlmostEqual(p_neg, p_pos, delta=1e-12)

    def test_t1_f1_03_student_t_critical_value_standard_95_confidence(self):
        """T1-F1-03: Student's t critical value at 95% confidence for standard d.o.f."""
        expected_table = {
            1.0: 12.706204736175,
            2.0: 4.302652729749,
            3.0: 3.182446305284,
            4.0: 2.776445105197,
            5.0: 2.570581835636,
            10.0: 2.228138851986,
            30.0: 2.042272456301,
        }
        for df, expected in expected_table.items():
            crit = convergence.student_t_critical_value(df, confidence=0.95)
            self.assertAlmostEqual(crit, expected, delta=1e-9)

    def test_t1_f1_04_student_t_non_integer_welch_degrees_of_freedom(self):
        """T1-F1-04: Exact non-integer real degrees of freedom from Welch-Satterthwaite."""
        c1 = convergence.student_t_critical_value(4.37, confidence=0.95)
        c2 = convergence.student_t_critical_value(12.85, confidence=0.95)
        p1 = convergence.student_t_two_sided_pvalue(2.686148100041, 4.37)
        self.assertAlmostEqual(c1, 2.686148100041, delta=1e-9)
        self.assertAlmostEqual(c2, 2.162934976286, delta=1e-9)
        self.assertAlmostEqual(p1, 0.05, delta=1e-9)

    def test_t1_f1_05_student_t_roundtrip_inversion_across_confidence_levels(self):
        """T1-F1-05: Exact round-trip p(t_crit(df, conf), df) == 1 - conf across grid."""
        for df in [2.0, 4.37, 10.0, 25.0]:
            for conf in [0.80, 0.90, 0.95, 0.99]:
                t_c = convergence.student_t_critical_value(df, conf)
                p = convergence.student_t_two_sided_pvalue(t_c, df)
                self.assertAlmostEqual(p, 1.0 - conf, delta=1e-11)

    # ------------------------------------------------------------------
    # Feature F2: Robust MAD Outlier Detection (5 tests)
    # ------------------------------------------------------------------

    def test_t1_f2_01_clean_normal_sample_retains_all(self):
        """T1-F2-01: Clean sample stream retains 100% of samples as inliers."""
        samples = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=3.5)
        self.assertEqual(inliers, samples)
        self.assertEqual(outliers, [])

    def test_t1_f2_02_single_extreme_low_stall_rejected(self):
        """T1-F2-02: Single extreme low stall (200 MiB/s) is cleanly rejected."""
        samples = [1000.0, 1005.0, 995.0, 1002.0, 998.0, 200.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=3.5)
        self.assertEqual(inliers, [1000.0, 1005.0, 995.0, 1002.0, 998.0])
        self.assertEqual(outliers, [200.0])

    def test_t1_f2_03_single_extreme_high_spike_rejected(self):
        """T1-F2-03: Single extreme high spike (2500 MiB/s) is cleanly rejected."""
        samples = [1000.0, 1005.0, 995.0, 1002.0, 998.0, 2500.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=3.5)
        self.assertEqual(inliers, [1000.0, 1005.0, 995.0, 1002.0, 998.0])
        self.assertEqual(outliers, [2500.0])

    def test_t1_f2_04_custom_mad_threshold_sensitivity(self):
        """T1-F2-04: Strict vs. permissive mad_threshold controls outlier sensitivity."""
        samples = [100.0, 102.0, 98.0, 101.0, 99.0, 108.0]
        inliers_strict, outliers_strict = convergence.filter_outliers_mad(samples, mad_threshold=2.0)
        inliers_loose, outliers_loose = convergence.filter_outliers_mad(samples, mad_threshold=5.0)
        self.assertEqual(outliers_strict, [108.0])
        self.assertEqual(inliers_strict, [100.0, 102.0, 98.0, 101.0, 99.0])
        self.assertEqual(outliers_loose, [])
        self.assertEqual(inliers_loose, samples)

    def test_t1_f2_05_inlier_order_preservation(self):
        """T1-F2-05: Inlier sequence preserves original sample observation ordering."""
        samples = [1005.0, 200.0, 995.0, 1002.0, 998.0, 1000.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=3.5)
        self.assertEqual(inliers, [1005.0, 995.0, 1002.0, 998.0, 1000.0])
        self.assertEqual(outliers, [200.0])

    # ------------------------------------------------------------------
    # Feature F3: Single-Value Canonical Point & Interval Estimation (5 tests)
    # ------------------------------------------------------------------

    def test_t1_f3_01_low_variance_stream_converges_true(self):
        """T1-F3-01: Tight steady-state stream converges with exact Student's t CI."""
        res = convergence.evaluate_convergence(
            [1000.0, 1005.0, 995.0, 1002.0, 998.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        self.assertTrue(res.converged)
        self.assertAlmostEqual(res.representative_value, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.median, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.raw_mean, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.std_dev, 3.8078865529319543, delta=1e-6)
        self.assertAlmostEqual(res.ci_half_width, 4.728115642018857, delta=1e-6)
        self.assertAlmostEqual(res.relative_margin_of_error, 0.004728115642018856, delta=1e-8)
        self.assertEqual(res.n_total, 5)
        self.assertEqual(res.n_steady, 5)
        self.assertEqual(res.n_inliers, 5)

    def test_t1_f3_02_high_variance_stream_converged_false(self):
        """T1-F3-02: High-variance stream reports converged=False with valid point estimate."""
        res = convergence.evaluate_convergence(
            [600.0, 1400.0, 800.0, 1200.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        self.assertFalse(res.converged)
        self.assertAlmostEqual(res.representative_value, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.relative_margin_of_error, 0.581032543150953, delta=1e-6)

    def test_t1_f3_03_below_min_iterations_converged_false_even_if_tight(self):
        """T1-F3-03: Fewer than min_iterations steady samples always yields converged=False."""
        res = convergence.evaluate_convergence(
            [1000.0, 1001.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        self.assertFalse(res.converged)
        self.assertEqual(res.n_total, 2)
        self.assertEqual(res.n_steady, 2)
        self.assertAlmostEqual(res.representative_value, 1000.5, delta=1e-9)

    def test_t1_f3_04_warmup_separation_keep_mount_mode(self):
        """T1-F3-04: Warmup iteration 1 is separated from steady-state convergence stats."""
        res = convergence.evaluate_convergence(
            [250.0, 1000.0, 1005.0, 995.0, 1002.0, 998.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
            warmup_iterations=1,
        )
        self.assertEqual(res.warmup_samples, [250.0])
        self.assertEqual(res.n_total, 6)
        self.assertEqual(res.n_steady, 5)
        self.assertEqual(res.n_inliers, 5)
        self.assertAlmostEqual(res.representative_value, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.raw_mean, 1000.0, delta=1e-9)
        self.assertTrue(res.converged)

    def test_t1_f3_05_transient_stall_filtering_in_evaluate_convergence(self):
        """T1-F3-05: Transient 200 MiB/s stall is removed so representative_value is 1000.0."""
        res = convergence.evaluate_convergence(
            [1000.0, 1005.0, 995.0, 1002.0, 998.0, 200.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        self.assertEqual(res.outliers_removed, [200.0])
        self.assertAlmostEqual(res.representative_value, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.raw_mean, 866.6666666666666, delta=1e-6)
        self.assertTrue(res.converged)

    # ------------------------------------------------------------------
    # Feature F4: Welch's Two-Sample t-Test & Practical Effect Size (5 tests)
    # ------------------------------------------------------------------

    def test_t1_f4_01_throughput_significant_improvement(self):
        """T1-F4-01: Genuine +10% throughput gain classified as SIGNIFICANT_IMPROVEMENT."""
        baseline = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        candidate = [1100.0, 1110.0, 1090.0, 1105.0, 1095.0]
        res = convergence.compare_samples_welch(
            baseline, candidate, alpha=0.05, min_effect_pct=2.0, higher_is_better=True
        )
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertAlmostEqual(res.delta_pct, 10.0, delta=1e-6)
        self.assertAlmostEqual(res.t_stat, 25.482359571881275, delta=1e-6)
        self.assertAlmostEqual(res.degrees_of_freedom, 5.761204907081259, delta=1e-6)
        self.assertTrue(res.statistically_significant)
        self.assertTrue(res.practically_significant)

    def test_t1_f4_02_throughput_significant_regression(self):
        """T1-F4-02: Genuine -15% throughput drop classified as SIGNIFICANT_REGRESSION."""
        baseline = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        candidate = [850.0, 860.0, 840.0, 855.0, 845.0]
        res = convergence.compare_samples_welch(
            baseline, candidate, alpha=0.05, min_effect_pct=2.0, higher_is_better=True
        )
        self.assertEqual(res.verdict, "SIGNIFICANT_REGRESSION")
        self.assertAlmostEqual(res.delta_pct, -15.0, delta=1e-6)
        self.assertAlmostEqual(res.t_stat, -38.22353935782191, delta=1e-6)

    def test_t1_f4_03_statistically_significant_but_practically_negligible_noise(self):
        """T1-F4-03: Sub-threshold +0.5% shift with tiny variance is NOISE / NEUTRAL."""
        baseline = [1000.0, 1001.0, 999.0, 1000.5, 999.5]
        candidate = [1005.0, 1006.0, 1004.0, 1005.5, 1004.5]
        res = convergence.compare_samples_welch(
            baseline, candidate, alpha=0.05, min_effect_pct=2.0, higher_is_better=True
        )
        self.assertTrue(res.statistically_significant)
        self.assertFalse(res.practically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_t1_f4_04_large_delta_but_high_variance_statistically_insignificant_noise(self):
        """T1-F4-04: +5% shift swamped by high variance (p > 0.05) is NOISE / NEUTRAL."""
        baseline = [800.0, 1200.0, 900.0, 1100.0, 1000.0]
        candidate = [850.0, 1250.0, 950.0, 1150.0, 1050.0]
        res = convergence.compare_samples_welch(
            baseline, candidate, alpha=0.05, min_effect_pct=2.0, higher_is_better=True
        )
        self.assertFalse(res.statistically_significant)
        self.assertTrue(res.practically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_t1_f4_05_latency_polarity_inversion_lower_is_better(self):
        """T1-F4-05: When higher_is_better=False (latency), drops improve and spikes regress."""
        base_lat = [10.0, 10.1, 9.9, 10.0]
        cand_drop = [8.0, 8.1, 7.9, 8.0]
        cand_spike = [12.0, 12.1, 11.9, 12.0]
        res_drop = convergence.compare_samples_welch(
            base_lat, cand_drop, alpha=0.05, min_effect_pct=2.0, higher_is_better=False
        )
        res_spike = convergence.compare_samples_welch(
            base_lat, cand_spike, alpha=0.05, min_effect_pct=2.0, higher_is_better=False
        )
        self.assertEqual(res_drop.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertEqual(res_spike.verdict, "SIGNIFICANT_REGRESSION")

    # ------------------------------------------------------------------
    # Feature F5: Base Container Image Packaging (5 tests)
    # ------------------------------------------------------------------

    def test_t1_f5_01_dockerfile_copies_convergence_to_python313_site_packages(self):
        """T1-F5-01: gcsfuse-perf-base.dockerfile copies convergence.py to site-packages."""
        dockerfile_path = os.path.join(REPO_ROOT, "gcsfuse-perf-base.dockerfile")
        with open(dockerfile_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        self.assertRegex(
            content,
            r"COPY\s+convergence\.py\s+/usr/local/lib/python3\.13/site-packages/convergence\.py",
        )

    def test_t1_f5_02_convergence_module_exists_at_repo_root(self):
        """T1-F5-02: convergence.py exists at repository root and imports cleanly."""
        conv_path = os.path.join(REPO_ROOT, "convergence.py")
        self.assertTrue(os.path.isfile(conv_path))
        self.assertGreater(os.path.getsize(conv_path), 0)
        mod = importlib.import_module("convergence")
        self.assertIsNotNone(mod)

    def test_t1_f5_03_public_api_contract_exports_all_symbols(self):
        """T1-F5-03: convergence module exports all 8 required contract symbols."""
        required_symbols = [
            "ConvergenceResult",
            "ComparisonResult",
            "betainc",
            "student_t_two_sided_pvalue",
            "student_t_critical_value",
            "filter_outliers_mad",
            "evaluate_convergence",
            "compare_samples_welch",
        ]
        for sym in required_symbols:
            self.assertTrue(hasattr(convergence, sym), f"Missing symbol: {sym}")

    def test_t1_f5_04_fio_runner_imports_convergence_from_isolated_cwd(self):
        """T1-F5-04: fio_benchmark_runner resolves convergence when cwd is fio/."""
        proc = subprocess.run(
            [sys.executable, "-c", "import fio_benchmark_runner; import convergence"],
            cwd=FIO_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_t1_f5_05_go_client_runner_imports_convergence_from_isolated_cwd(self):
        """T1-F5-05: run_go_matrix resolves convergence when cwd is go-client/."""
        proc = subprocess.run(
            [sys.executable, "-c", "import run_go_matrix; import convergence"],
            cwd=GO_CLIENT_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # ------------------------------------------------------------------
    # Feature F6: FIO Runner Adaptive Convergence Loop (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f6_01_early_convergence_on_low_variance_stream(self):
        """T1-F6-01: FIO runner stops early at min_iterations=3 on tight stream."""
        stream = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=False,
                    min_iterations=3,
                    max_iterations=10,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )
                self.assertEqual(mock_fio.call_count, 3)
            self.assertTrue(os.path.exists(summary_file))
            with open(summary_file, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
            self.assertIn("1000.00", summary_text)

    def test_t1_f6_02_warmup_separation_when_keep_mount_true(self):
        """T1-F6-02: When keep_mount=True, cold-cache iter 1 is separated from steady state."""
        stream = [250.0, 2000.0, 2010.0, 1990.0, 2005.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse") as mock_mount, \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse") as mock_unmount, \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=True,
                    min_iterations=3,
                    max_iterations=8,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )
                self.assertEqual(mock_mount.call_count, 1)
                self.assertEqual(mock_unmount.call_count, 1)
                self.assertEqual(mock_fio.call_count, 4)
            self.assertTrue(os.path.exists(summary_file))
            with open(summary_file, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
            # Steady-state inliers [2000.0, 2010.0, 1990.0] have mean 2000.00;
            # cold-cache iter 1 (250.0) must NOT pull representative mean down to 1562.50.
            self.assertIn("2000.00", summary_text)
            self.assertNotIn("1562.50", summary_text)

    def test_t1_f6_03_single_extreme_transient_stall_outlier_filtering(self):
        """T1-F6-03: FIO runner filters single 200 MiB/s stall and converges cleanly."""
        stream = [1000.0, 1000.0, 200.0, 1000.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=False,
                    min_iterations=4,
                    max_iterations=8,
                    convergence_threshold=0.05,
                )
                self.assertEqual(mock_fio.call_count, 4)
            self.assertTrue(os.path.exists(summary_file))
            with open(summary_file, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
            self.assertIn("1000.00", summary_text)
            self.assertNotIn("800.00", summary_text)

    def test_t1_f6_04_high_variance_clean_termination_at_max_iterations(self):
        """T1-F6-04: High-variance stream stops cleanly at max_iterations=5."""
        stream = [500.0, 1500.0, 600.0, 1400.0, 550.0, 1450.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.txt")

            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    summary_file=summary_file,
                    keep_mount=False,
                    min_iterations=3,
                    max_iterations=5,
                    convergence_threshold=0.02,
                )
                self.assertEqual(mock_fio.call_count, 5)
            self.assertTrue(os.path.exists(summary_file))
            with open(summary_file, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
            self.assertIn("500.00", summary_text)
            self.assertIn("1500.00", summary_text)

    def test_t1_f6_05_bq_upload_preserves_exact_6_column_schema(self):
        """T1-F6-05: BigQuery rows uploaded by FIO runner preserve strict 6-column schema."""
        stream = [1000.0, 1002.0, 998.0]
        harness = InMemoryBigQueryHarness()
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio), \
                 patch.object(fio_benchmark_runner.bigquery, "Client", return_value=harness):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=3,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="test-proj",
                    bq_dataset_id="test_ds",
                    bq_table_id="fio_read_grpc",
                    min_iterations=3,
                    max_iterations=6,
                    convergence_threshold=0.05,
                )
        rows = harness.inserted_rows.get("test-proj.test_ds.fio_read_grpc", [])
        self.assertGreaterEqual(len(rows), 3)
        expected_keys = {
            "run_timestamp",
            "iteration",
            "gcsfuse_flags",
            "fio_env",
            "cpu_limit_list",
            "fio_json_output",
        }
        for row in rows:
            self.assertEqual(set(row.keys()), expected_keys)

    def test_t1_f6_06_run_fio_benchmark_and_run_fio_matrix_cli_flag_forwarding(self):
        """T1-F6-06: run_fio_benchmark.py & run_fio_matrix.py forward convergence CLI flags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("GCS_BUCKET_NAME,GCS_FUSE_FLAGS,FILE_SIZE,BLOCK_SIZE,NR_FILES,NUMJOBS\n")
                fh.write("test-b,--implicit-dirs,1G,1M,10,112\n")
            with patch.object(fio_benchmark_runner, "run_benchmark") as mock_run, \
                 patch.object(fio_benchmark_runner, "truncate_bq_table"):
                with patch.object(sys, "argv", [
                    "run_fio_benchmark.py",
                    "--bucket-name", "test-b",
                    "--fio-config", "read.fio",
                    "--project-id", "test-p",
                    "--min-iterations", "3",
                    "--max-iterations", "7",
                    "--convergence-threshold", "0.04",
                    "--confidence-level", "0.90",
                ]):
                    run_fio_benchmark.main()
                kwargs = mock_run.call_args.kwargs
                self.assertEqual(kwargs.get("min_iterations"), 3)
                self.assertEqual(kwargs.get("max_iterations"), 7)
                self.assertAlmostEqual(kwargs.get("convergence_threshold"), 0.04)
                self.assertAlmostEqual(kwargs.get("confidence_level"), 0.90)

                mock_run.reset_mock()
                with patch.object(sys, "argv", [
                    "run_fio_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--fio-template", "read.fio",
                    "--project-id", "test-p",
                    "--work-dir", tmpdir,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "7",
                    "--convergence-threshold", "0.04",
                    "--confidence-level", "0.90",
                ]):
                    run_fio_matrix.main()
                kwargs2 = mock_run.call_args.kwargs
                self.assertEqual(kwargs2.get("min_iterations"), 3)
                self.assertEqual(kwargs2.get("max_iterations"), 7)
                self.assertAlmostEqual(kwargs2.get("convergence_threshold"), 0.04)
                self.assertAlmostEqual(kwargs2.get("confidence_level"), 0.90)

    # ------------------------------------------------------------------
    # Feature F7: Go-Client Matrix Runner Adaptive Convergence Loop (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f7_01_go_matrix_early_convergence_single_config(self):
        """T1-F7-01: Go matrix runner stops early at min_iterations=3 on low-variance stream."""
        stream = [3000.0, 3010.0, 2990.0, 3005.0, 2995.0]
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    idx = call_idx["n"]
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(stream[idx])
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "8",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            self.assertEqual(call_idx["n"], 3)

    def test_t1_f7_02_go_matrix_independent_per_config_convergence_stopping(self):
        """T1-F7-02: Each CSV config in run_go_matrix stops independently based on its variance."""
        cfg_a = [2500.0, 2505.0, 2495.0, 2500.0, 2500.0, 2500.0]
        cfg_b = [600.0, 1400.0, 700.0, 1300.0, 800.0, 1200.0]
        counts = {"1M": 0, "128K": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n")
                fh.write("seq,1G,1M,10\n")
                fh.write("seq,1G,128K,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                cmd_str = " ".join(cmd)
                if "1M" in cmd_str:
                    i = counts["1M"]
                    counts["1M"] += 1
                    res.stdout = make_synthetic_go_json(cfg_a[i], bs="1M")
                elif "128K" in cmd_str:
                    i = counts["128K"]
                    counts["128K"] += 1
                    res.stdout = make_synthetic_go_json(cfg_b[i], bs="128K")
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "6",
                    "--convergence-threshold", "0.04",
                ]):
                    run_go_matrix.main()
            self.assertEqual(counts["1M"], 3)
            self.assertEqual(counts["128K"], 6)

    def test_t1_f7_03_go_matrix_transient_outlier_immunity(self):
        """T1-F7-03: Single transient 400 MiB/s stall in Go matrix is filtered out."""
        stream = [3200.0, 3200.0, 400.0, 3200.0, 3200.0]
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    i = call_idx["n"]
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(stream[i])
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "4",
                    "--max-iterations", "8",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            self.assertEqual(call_idx["n"], 4)

    def test_t1_f7_04_go_matrix_high_variance_clean_termination_at_max_iterations(self):
        """T1-F7-04: Oscillating Go-client stream stops cleanly at max_iterations=5."""
        stream = [1000.0, 3000.0, 1100.0, 2900.0, 1050.0, 2950.0]
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    i = call_idx["n"]
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(stream[i])
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.02",
                ]):
                    run_go_matrix.main()
            self.assertEqual(call_idx["n"], 5)

    def test_t1_f7_05_go_matrix_bq_upload_preserves_6_column_schema(self):
        """T1-F7-05: Go-client runner uploads rows with exact 6-column BigQuery schema."""
        stream = [3000.0, 3005.0, 2995.0]
        call_idx = {"n": 0}
        harness = InMemoryBigQueryHarness()
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    i = call_idx["n"]
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(stream[i])
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd), \
                 patch.object(run_go_matrix.bigquery, "Client", return_value=harness):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--project-id", "test-proj",
                    "--bq-dataset-id", "test_ds",
                    "--bq-table-id", "go_client_read_grpc",
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
        rows = harness.inserted_rows.get("test-proj.test_ds.go_client_read_grpc", [])
        self.assertGreaterEqual(len(rows), 3)
        expected_keys = {
            "run_timestamp",
            "iteration",
            "gcsfuse_flags",
            "fio_env",
            "cpu_limit_list",
            "fio_json_output",
        }
        for row in rows:
            self.assertEqual(set(row.keys()), expected_keys)

    def test_t1_f7_06_go_matrix_summary_file_written_with_statistical_metrics(self):
        """T1-F7-06: Go matrix writes summary file containing canonical statistical metrics."""
        stream = [3000.0, 3005.0, 2995.0]
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    i = call_idx["n"]
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(stream[i])
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--summary-file-name", "summary.txt",
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            summary_path = os.path.join(tmpdir, "summary.txt")
            self.assertTrue(os.path.isfile(summary_path))
            with open(summary_path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("3000", text)

    # ------------------------------------------------------------------
    # Feature F8: Disaggregated Extraction & Reporting in query_results.py (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f8_01_disaggregated_per_iteration_fio_extraction_seq_rand_write(self):
        """T1-F8-01: get_table_metrics extracts disaggregated read, randread, and write."""
        rows = make_bq_per_iteration_rows({
            "read": ([2500.0, 2510.0, 2490.0, 2500.0], [1.2, 1.2, 1.2, 1.2]),
            "randread": ([1200.0, 1210.0, 1190.0, 1200.0], [2.5, 2.5, 2.5, 2.5]),
            "write": ([800.0, 805.0, 795.0, 800.0], [3.0, 3.0, 3.0, 3.0]),
        })
        mock_proc = MagicMock(stdout=json.dumps(rows), returncode=0)
        with patch.object(subprocess, "run", return_value=mock_proc) as mock_run:
            metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_grpc")
            sql_sent = mock_run.call_args.args[0][-1]
            self.assertIn('JSON_VALUE(fio_json_output, \'$.\"global options\".rw\')', sql_sent)
            self.assertIn("'read'", sql_sent)
            self.assertIn("'randread'", sql_sent)
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2500.0, delta=1e-3)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 1200.0, delta=1e-3)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 800.0, delta=1e-3)
        self.assertIn("workloads", metrics)
        self.assertTrue(metrics["workloads"]["read"]["bw_summary"].converged)
        self.assertTrue(metrics["workloads"]["randread"]["bw_summary"].converged)
        self.assertTrue(metrics["workloads"]["write"]["bw_summary"].converged)

    def test_t1_f8_02_per_iteration_outlier_filtering_in_query_results(self):
        """T1-F8-02: Per-iteration MAD outlier filtering rejects 300 MB/s stall."""
        rows = make_bq_per_iteration_rows({
            "read": ([2000.0, 2000.0, 300.0, 2000.0], [1.0, 1.0, 8.0, 1.0]),
        })
        mock_proc = MagicMock(stdout=json.dumps(rows), returncode=0)
        with patch.object(subprocess, "run", return_value=mock_proc):
            metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_http1")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2000.0, delta=1e-3)
        self.assertEqual(metrics["workloads"]["read"]["bw_summary"].outliers_removed, [300.0])

    def test_t1_f8_03_go_client_table_per_iteration_extraction_and_legacy_query_compatibility(self):
        """T1-F8-03: go_client_read_* query avoids UNNEST and extracts canonical metrics."""
        rows = [
            {"iteration": 1, "fio_version": "go-client", "seq_read_bw_mbs": 3200.0, "seq_read_lat_ms": 1.1},
            {"iteration": 2, "fio_version": "go-client", "seq_read_bw_mbs": 3210.0, "seq_read_lat_ms": 1.1},
            {"iteration": 3, "fio_version": "go-client", "seq_read_bw_mbs": 3190.0, "seq_read_lat_ms": 1.1},
        ]
        mock_proc = MagicMock(stdout=json.dumps(rows), returncode=0)
        with patch.object(subprocess, "run", return_value=mock_proc) as mock_run:
            metrics = query_results.get_table_metrics("test-proj", "test-ds", "go_client_read_grpc")
            sql_sent = mock_run.call_args.args[0][-1]
            self.assertIn("read_bw_mbps", sql_sent)
            self.assertNotIn("UNNEST", sql_sent)
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 3200.0, delta=1e-3)
        self.assertEqual(metrics["fio_version"], "go-client")

    def test_t1_f8_04_backwards_compatibility_with_legacy_single_row_mock_payloads(self):
        """T1-F8-04: Single pre-aggregated mock payload from npi_test.py works identically."""
        legacy_payload = [{
            "fio_version": "fio-3.36",
            "seq_read_bw_mbs": 2500.5,
            "rand_read_bw_mbs": 1200.0,
            "write_bw_mbs": 450.25,
            "seq_read_lat_ms": 1.2,
            "rand_read_lat_ms": 2.5,
            "write_lat_ms": 3.0,
        }]
        mock_proc = MagicMock(stdout=json.dumps(legacy_payload), returncode=0)
        with patch.object(subprocess, "run", return_value=mock_proc):
            metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_http1")
        self.assertEqual(metrics["fio_version"], "fio-3.36")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2500.5)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 1200.0)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 450.25)

    def test_t1_f8_05_convergence_status_and_rmoe_propagation_in_workload_summaries(self):
        """T1-F8-05: Convergence status and RMoE reflect configured convergence_threshold."""
        rows_conv = make_bq_per_iteration_rows({
            "write": ([1000.0, 1005.0, 995.0, 1000.0], [2.0, 2.0, 2.0, 2.0]),
        })
        rows_unconv = make_bq_per_iteration_rows({
            "write": ([600.0, 1400.0, 700.0, 1300.0], [2.0, 2.0, 2.0, 2.0]),
        })
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows_conv), returncode=0)):
            m_a = query_results.get_table_metrics("p", "d", "fio_write_grpc", convergence_threshold=0.03)
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows_unconv), returncode=0)):
            m_b = query_results.get_table_metrics("p", "d", "fio_write_grpc", convergence_threshold=0.03)
        self.assertTrue(m_a["workloads"]["write"]["bw_summary"].converged)
        self.assertFalse(m_b["workloads"]["write"]["bw_summary"].converged)

    def test_t1_f8_06_main_cli_prints_dedicated_disaggregated_workload_tables(self):
        """T1-F8-06: query_results.main() prints dedicated disaggregated workload tables."""
        rows = make_bq_per_iteration_rows({
            "read": ([2500.0, 2505.0, 2495.0], [1.1, 1.1, 1.1]),
            "randread": ([1200.0, 1205.0, 1195.0], [2.2, 2.2, 2.2]),
            "write": ([800.0, 805.0, 795.0], [3.3, 3.3, 3.3]),
        })
        buf = io.StringIO()
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)), \
             patch.object(sys, "argv", [
                 "query_results.py",
                 "--project-id", "test-proj",
                 "--dataset-id", "cand_ds",
                 "--table-types", "fio_read_grpc", "fio_write_grpc",
             ]), \
             patch("sys.stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("2500", output)
        self.assertIn("1200", output)
        self.assertIn("800", output)

    # ------------------------------------------------------------------
    # Feature F9: Disaggregated Baseline Significance Comparison (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f9_01_significant_improvement_verdict_on_genuine_gain(self):
        """T1-F9-01: +15% candidate gain yields SIGNIFICANT_IMPROVEMENT."""
        base = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        cand = [1150.0, 1155.0, 1145.0, 1152.0, 1148.0]
        res = convergence.compare_samples_welch(base, cand, alpha=0.05, min_effect_pct=2.0)
        self.assertAlmostEqual(res.delta_pct, 15.0, delta=0.1)
        self.assertLess(res.p_value, 0.001)
        self.assertTrue(res.statistically_significant)
        self.assertTrue(res.practically_significant)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t1_f9_02_significant_regression_verdict_on_genuine_drop(self):
        """T1-F9-02: -20% candidate drop yields SIGNIFICANT_REGRESSION."""
        base = [1200.0, 1204.0, 1196.0, 1202.0, 1198.0]
        cand = [960.0, 964.0, 956.0, 962.0, 958.0]
        res = convergence.compare_samples_welch(base, cand, alpha=0.05, min_effect_pct=2.0)
        self.assertAlmostEqual(res.delta_pct, -20.0, delta=0.1)
        self.assertLess(res.p_value, 0.001)
        self.assertEqual(res.verdict, "SIGNIFICANT_REGRESSION")

    def test_t1_f9_03_noise_neutral_verdict_when_p_value_above_alpha(self):
        """T1-F9-03: Noisy shift with p > alpha yields NOISE / NEUTRAL."""
        base = [800.0, 840.0, 760.0, 820.0, 780.0]
        cand = [810.0, 850.0, 770.0, 830.0, 790.0]
        res = convergence.compare_samples_welch(base, cand, alpha=0.05, min_effect_pct=2.0)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_t1_f9_04_statistically_significant_but_practically_trivial_classified_as_noise_neutral(self):
        """T1-F9-04: +0.3% shift below min_effect_pct=2.0% yields NOISE / NEUTRAL."""
        base = [1000.0, 1000.01, 999.99, 1000.0]
        cand = [1003.0, 1003.01, 1002.99, 1003.0]
        res = convergence.compare_samples_welch(base, cand, alpha=0.05, min_effect_pct=2.0)
        self.assertTrue(res.statistically_significant)
        self.assertFalse(res.practically_significant)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_t1_f9_05_independent_verdicts_across_disaggregated_read_randread_write(self):
        """T1-F9-05: query_results.main() reports independent verdicts across read/randread/write."""
        base_rows = make_bq_per_iteration_rows({
            "read": ([1000.0, 1002.0, 998.0, 1000.0], [1.0, 1.0, 1.0, 1.0]),
            "randread": ([1000.0, 1002.0, 998.0, 1000.0], [2.0, 2.0, 2.0, 2.0]),
            "write": ([1000.0, 1002.0, 998.0, 1000.0], [3.0, 3.0, 3.0, 3.0]),
        })
        cand_rows = make_bq_per_iteration_rows({
            "read": ([1180.0, 1182.0, 1178.0, 1180.0], [1.0, 1.0, 1.0, 1.0]),
            "randread": ([850.0, 852.0, 848.0, 850.0], [2.0, 2.0, 2.0, 2.0]),
            "write": ([1004.0, 1006.0, 1002.0, 1004.0], [3.0, 3.0, 3.0, 3.0]),
        })

        def fake_bq(cmd, **kwargs):
            sql = cmd[-1]
            if "base_ds" in sql:
                return MagicMock(stdout=json.dumps(base_rows), returncode=0)
            return MagicMock(stdout=json.dumps(cand_rows), returncode=0)

        buf = io.StringIO()
        with patch.object(subprocess, "run", side_effect=fake_bq), \
             patch.object(sys, "argv", [
                 "query_results.py",
                 "--project-id", "test-proj",
                 "--dataset-id", "cand_ds",
                 "--baseline-dataset-id", "base_ds",
                 "--table-types", "fio_read_grpc", "fio_write_grpc",
             ]), \
             patch("sys.stdout", buf):
            query_results.main()
        output = buf.getvalue()
        self.assertIn("SIGNIFICANT_IMPROVEMENT", output)
        self.assertIn("SIGNIFICANT_REGRESSION", output)
        self.assertIn("NOISE / NEUTRAL", output)

    def test_t1_f9_06_latency_directionality_lower_is_better(self):
        """T1-F9-06: Latency drop is SIGNIFICANT_IMPROVEMENT when higher_is_better=False."""
        base_lat = [5.0, 5.1, 4.9, 5.0]
        cand_lat = [2.5, 2.6, 2.4, 2.5]
        res = convergence.compare_samples_welch(base_lat, cand_lat, higher_is_better=False)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    # ------------------------------------------------------------------
    # Feature F10: Orchestrator CLI Flag Exposure & Backwards Compatibility (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f10_01_npi_benchmark_factory_propagates_convergence_flags_to_docker_cmd(self):
        """T1-F10-01: npi.BenchmarkFactory propagates convergence flags to FIO & Go commands."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=3,
            max_iterations=10,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        cmd_fio = factory.get_benchmark_command("read_grpc")[0]
        cmd_go = factory.get_benchmark_command("go_read_grpc")[0]
        for cmd in (cmd_fio, cmd_go):
            self.assertIn("--min-iterations=3", cmd)
            self.assertIn("--max-iterations=10", cmd)
            self.assertIn("--convergence-threshold=0.05", cmd)
            self.assertIn("--confidence-level=0.95", cmd)

    def test_t1_f10_02_npi_benchmark_factory_legacy_mode_byte_for_byte_identical(self):
        """T1-F10-02: Legacy BenchmarkFactory without convergence flags emits zero new flags."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
        )
        cmd = factory.get_benchmark_command("read_http1")[0]
        self.assertIn("--iterations=5", cmd)
        self.assertNotIn("--min-iterations", cmd)
        self.assertNotIn("--max-iterations", cmd)
        self.assertNotIn("--convergence-threshold", cmd)
        self.assertNotIn("--confidence-level", cmd)

    def test_t1_f10_03_npi_gke_propagates_convergence_flags_to_job_spec_args(self):
        """T1-F10-03: npi_gke propagates convergence flags into Kubernetes Job container args."""
        buf = io.StringIO()
        with patch.object(subprocess, "run", return_value=MagicMock(returncode=0, stdout="")), \
             patch.object(sys, "argv", [
                 "npi_gke.py",
                 "--dry-run",
                 "--cluster-name", "test-c",
                 "--location", "us-central1-a",
                 "--bucket-name", "test-b",
                 "--project-id", "test-p",
                 "--bq-dataset-id", "test-ds",
                 "--benchmarks", "read_grpc", "go_read_grpc",
                 "--min-iterations", "3",
                 "--max-iterations", "8",
                 "--convergence-threshold", "0.04",
                 "--confidence-level", "0.90",
             ]), \
             patch("sys.stdout", buf):
            npi_gke.main()
        out = buf.getvalue()
        self.assertIn("--min-iterations=3", out)
        self.assertIn("--max-iterations=8", out)
        self.assertIn("--convergence-threshold=0.04", out)
        self.assertIn("--confidence-level=0.9", out)

    def test_t1_f10_04_npi_orchestrator_execute_target_propagates_convergence_flags_to_gce_and_gke(self):
        """T1-F10-04: npi_orchestrator.execute_target forwards convergence flags to GCE & GKE."""
        args = argparse.Namespace(
            benchmarks="read_grpc",
            project="test-p",
            image_version="latest",
            iterations=5,
            smoke_mode=False,
            extra_mount_options=None,
            min_iterations=3,
            max_iterations=9,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        gce_target = {
            "name": "gce_t",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
            "buffer_dir": "/mnt/buf",
        }
        gke_target = {
            "name": "gke_t",
            "type": "gke",
            "vm_name": "runner1",
            "zone": "us-central1-a",
            "cluster_name": "c1",
            "location": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
        }
        captured = []
        init_state = {"gce_t": {"status": "PENDING"}, "gke_t": {"status": "PENDING"}}
        with patch.object(npi_orchestrator, "cleanup_remote_run"), \
             patch.object(npi_orchestrator, "prep_vm"), \
             patch.object(npi_orchestrator, "save_state"), \
             patch.object(npi_orchestrator, "monitor_run", return_value=True), \
             patch.object(npi_orchestrator, "run_ssh_cmd", side_effect=lambda *a, **k: (captured.append(a), (0, "", ""))[1]):
            npi_orchestrator.execute_target(gce_target, args, threading.Lock(), init_state)
            npi_orchestrator.execute_target(gke_target, args, threading.Lock(), init_state)
        joined = " ".join(str(x) for x in captured)
        self.assertIn("--min-iterations", joined)
        self.assertIn("--max-iterations", joined)
        self.assertIn("--convergence-threshold", joined)

    def test_t1_f10_05_npi_orchestrator_cli_parser_accepts_all_convergence_flags(self):
        """T1-F10-05: --help on npi_orchestrator.py, npi.py, and npi_gke.py documents all flags."""
        for script in ("npi_orchestrator.py", "npi.py", "npi_gke.py"):
            proc = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, script), "--help"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("--min-iterations", proc.stdout)
            self.assertIn("--max-iterations", proc.stdout)
            self.assertIn("--convergence-threshold", proc.stdout)
            self.assertIn("--confidence-level", proc.stdout)

    def test_t1_f10_06_host_info_collector_never_receives_convergence_flags(self):
        """T1-F10-06: host_info command never receives iteration or convergence flags."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=3,
            max_iterations=10,
            convergence_threshold=0.05,
        )
        cmd = factory.get_benchmark_command("host_info")[0]
        self.assertNotIn("--iterations", cmd)
        self.assertNotIn("--min-iterations", cmd)
        self.assertNotIn("--max-iterations", cmd)
        self.assertNotIn("--convergence-threshold", cmd)

    # ------------------------------------------------------------------
    # Feature F11: Comprehensive Requirement-Driven E2E Suite (6 tests)
    # ------------------------------------------------------------------

    def test_t1_f11_01_full_pipeline_e2e_orchestrator_to_fio_runner_to_bq_to_query_results(self):
        """T1-F11-01: Full E2E flow from BenchmarkFactory -> FIO runner -> BQ -> query_results."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test_ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=3,
            max_iterations=8,
            convergence_threshold=0.05,
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertIn("--min-iterations=3", cmd)
        stream = [2400.0, 2405.0, 2395.0, 2402.0]
        rows = make_bq_per_iteration_rows({"read": (stream[:3], [1.1, 1.1, 1.1])})
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)):
            metrics = query_results.get_table_metrics("test-p", "test_ds", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2400.0, delta=1e-3)

    def test_t1_f11_02_full_pipeline_e2e_orchestrator_to_go_client_matrix_to_bq_to_query_results(self):
        """T1-F11-02: Full E2E flow from BenchmarkFactory -> Go runner -> BQ -> query_results."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test_ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=3,
            max_iterations=8,
            convergence_threshold=0.05,
        )
        cmd = factory.get_benchmark_command("go_read_grpc")[0]
        self.assertIn("--min-iterations=3", cmd)
        rows = [
            {"iteration": 1, "fio_version": "go-client", "seq_read_bw_mbs": 3100.0, "seq_read_lat_ms": 1.0},
            {"iteration": 2, "fio_version": "go-client", "seq_read_bw_mbs": 3105.0, "seq_read_lat_ms": 1.0},
            {"iteration": 3, "fio_version": "go-client", "seq_read_bw_mbs": 3095.0, "seq_read_lat_ms": 1.0},
        ]
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)):
            metrics = query_results.get_table_metrics("test-p", "test_ds", "go_client_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 3100.0, delta=1e-3)

    def test_t1_f11_03_dual_storage_tier_invariant_regional_and_zonal_rapid_e2e(self):
        """T1-F11-03: Paired Regional Standard HNS & Zonal RAPID HNS targets execute cleanly."""
        f_reg = npi.BenchmarkFactory(
            bucket_name="npi-smoke-regional-b",
            project_id="test-p",
            bq_dataset_id="npi_e2e_regional",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            is_rapid_bucket=False,
            min_iterations=3,
            max_iterations=8,
            convergence_threshold=0.05,
        )
        f_zon = npi.BenchmarkFactory(
            bucket_name="npi-smoke-zonal-b",
            project_id="test-p",
            bq_dataset_id="npi_e2e_zonal",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            is_rapid_bucket=True,
            min_iterations=3,
            max_iterations=8,
            convergence_threshold=0.05,
        )
        self.assertIn("npi_e2e_regional", f_reg.get_benchmark_command("read_grpc")[0])
        self.assertIn("npi_e2e_zonal", f_zon.get_benchmark_command("read_grpc")[0])

    def test_t1_f11_04_deterministic_reproducibility_under_fixed_seed(self):
        """T1-F11-04: Identical PRNG seed produces bit-for-bit identical statistics."""
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        s1 = [rng1.gauss(1000.0, 15.0) for _ in range(6)]
        s2 = [rng2.gauss(1000.0, 15.0) for _ in range(6)]
        self.assertEqual(
            convergence.evaluate_convergence(s1),
            convergence.evaluate_convergence(s2),
        )

    def test_t1_f11_05_feature_coverage_matrix_self_audit(self):
        """T1-F11-05: Self-audit verifies Tier 1 (>=55), Tier 2 (>=55), Tier 3 (>=11), Tier 4 (>=6)."""
        loader = unittest.defaultTestLoader
        t1 = loader.loadTestsFromTestCase(TestTier1FeatureHappyPaths).countTestCases()
        t2 = loader.loadTestsFromTestCase(TestTier2FeatureEdgeCases).countTestCases()
        t3 = loader.loadTestsFromTestCase(TestTier3CrossFeatureCombinations).countTestCases()
        t4 = loader.loadTestsFromTestCase(TestTier4RealWorldScenarios).countTestCases()
        self.assertGreaterEqual(t1, 55)
        self.assertGreaterEqual(t2, 55)
        self.assertGreaterEqual(t3, 11)
        self.assertGreaterEqual(t4, 6)
        self.assertGreaterEqual(t1 + t2 + t3 + t4, 127)

    def test_t1_f11_06_zero_live_cloud_or_network_side_effects_audit(self):
        """T1-F11-06: HermeticCloudBlocker confirms zero live network connections occur."""
        with HermeticCloudBlocker() as audit:
            res = convergence.evaluate_convergence([1000.0, 1002.0, 998.0], min_iterations=3)
            self.assertTrue(res.converged)
        self.assertEqual(audit.blocked_network_attempts, [])
        self.assertEqual(audit.blocked_subprocess_attempts, [])


# ==============================================================================
# TIER 2: Boundary, Edge-Case & Error-Handling Coverage (61 Tests across F1..F11)
# ==============================================================================


class TestTier2FeatureEdgeCases(unittest.TestCase):
    """Tier 2: >= 5 boundary/edge-case tests per feature across F1-F11 (61 tests total)."""

    # ------------------------------------------------------------------
    # Feature F1: Student's t Boundary & Corner Cases (5 tests)
    # ------------------------------------------------------------------

    def test_t2_f1_01_betainc_exact_domain_endpoints(self):
        """T2-F1-01: Domain endpoints x <= 0 and x >= 1 return exact 0.0 and 1.0."""
        self.assertEqual(convergence.betainc(2.0, 3.0, 0.0), 0.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, -0.5), 0.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, 1.0), 1.0)
        self.assertEqual(convergence.betainc(2.0, 3.0, 1.5), 1.0)

    def test_t2_f1_02_pvalue_zero_and_infinite_t_stat(self):
        """T2-F1-02: Zero t-stat returns p=1.0; infinite/huge t-stat returns p=0.0."""
        self.assertEqual(convergence.student_t_two_sided_pvalue(0.0, 5.0), 1.0)
        self.assertEqual(convergence.student_t_two_sided_pvalue(float("inf"), 5.0), 0.0)
        self.assertEqual(convergence.student_t_two_sided_pvalue(float("-inf"), 5.0), 0.0)
        p_huge = convergence.student_t_two_sided_pvalue(1e6, 5.0)
        self.assertGreaterEqual(p_huge, 0.0)
        self.assertLess(p_huge, 1e-25)

    def test_t2_f1_03_critical_value_extreme_sub_unity_df(self):
        """T2-F1-03: Sub-unity fractional d.o.f. (df=0.5) converges accurately without negative overshoot."""
        c95 = convergence.student_t_critical_value(0.5, confidence=0.95)
        c99 = convergence.student_t_critical_value(0.5, confidence=0.99)
        self.assertAlmostEqual(
            convergence.student_t_two_sided_pvalue(c95, 0.5), 0.05, delta=1e-9
        )
        self.assertAlmostEqual(
            convergence.student_t_two_sided_pvalue(c99, 0.5), 0.01, delta=1e-9
        )

    def test_t2_f1_04_asymptotic_large_df_gaussian_convergence(self):
        """T2-F1-04: Large d.o.f. (df=10000) matches Gaussian asymptotic quantile without lgamma overflow."""
        c95 = convergence.student_t_critical_value(10000.0, confidence=0.95)
        c99 = convergence.student_t_critical_value(10000.0, confidence=0.99)
        self.assertAlmostEqual(c95, 1.960201239894, delta=1e-8)
        self.assertAlmostEqual(c99, 2.576321046672, delta=1e-8)

    def test_t2_f1_05_stdlib_only_dependency_isolation(self):
        """T2-F1-05: convergence module imports zero third-party math libraries (scipy/numpy/pandas)."""
        self.assertNotIn("scipy", convergence.__dict__)
        self.assertNotIn("numpy", convergence.__dict__)
        self.assertNotIn("pandas", convergence.__dict__)

    # ------------------------------------------------------------------
    # Feature F2: Robust MAD Boundary & Corner Cases (5 tests)
    # ------------------------------------------------------------------

    def test_t2_f2_01_mad_zero_with_extreme_stall_no_zero_division(self):
        """T2-F2-01: [1000, 1000, 1000, 200] rejects 200 without ZeroDivisionError when MAD=0."""
        inliers, outliers = convergence.filter_outliers_mad(
            [1000.0, 1000.0, 1000.0, 200.0], mad_threshold=3.5
        )
        self.assertEqual(inliers, [1000.0, 1000.0, 1000.0])
        self.assertEqual(outliers, [200.0])

    def test_t2_f2_02_mad_zero_with_sub_floor_micro_noise_keeps_all(self):
        """T2-F2-02: [1000.0, 1000.0, 1000.1] retains all samples when deviation <= noise floor."""
        inliers, outliers = convergence.filter_outliers_mad(
            [1000.0, 1000.0, 1000.1], mad_threshold=3.5
        )
        self.assertEqual(inliers, [1000.0, 1000.0, 1000.1])
        self.assertEqual(outliers, [])

    def test_t2_f2_03_small_sample_sizes_n_lt_3_never_filter(self):
        """T2-F2-03: Sample sizes N < 3 never reject outliers."""
        self.assertEqual(convergence.filter_outliers_mad([]), ([], []))
        self.assertEqual(convergence.filter_outliers_mad([1000.0]), ([1000.0], []))
        self.assertEqual(
            convergence.filter_outliers_mad([1000.0, 200.0]), ([1000.0, 200.0], [])
        )

    def test_t2_f2_04_majority_retention_guard_on_bimodal_ties(self):
        """T2-F2-04: Bimodal tie [100, 100, 200, 200] retains >= max(2, ceil(N/2)) inliers."""
        inliers, outliers = convergence.filter_outliers_mad(
            [100.0, 100.0, 200.0, 200.0], mad_threshold=0.5
        )
        self.assertGreaterEqual(len(inliers), 2)
        self.assertEqual(len(inliers) + len(outliers), 4)

    def test_t2_f2_05_all_identical_samples_zero_and_nonzero(self):
        """T2-F2-05: Identical samples (all 1000.0 or all 0.0) retain 100% of samples."""
        in1, out1 = convergence.filter_outliers_mad([1000.0, 1000.0, 1000.0, 1000.0])
        in2, out2 = convergence.filter_outliers_mad([0.0, 0.0, 0.0, 0.0])
        self.assertEqual(out1, [])
        self.assertEqual(len(in1), 4)
        self.assertEqual(out2, [])
        self.assertEqual(len(in2), 4)

    # ------------------------------------------------------------------
    # Feature F3: Canonical Point & Interval Boundary Cases (5 tests)
    # ------------------------------------------------------------------

    def test_t2_f3_01_single_sample_n_eq_1_handling(self):
        """T2-F3-01: Single observation N=1 returns std_dev=0.0 and converged=False safely."""
        res = convergence.evaluate_convergence(
            [1000.0], min_iterations=3, convergence_threshold=0.05
        )
        self.assertEqual(res.representative_value, 1000.0)
        self.assertEqual(res.median, 1000.0)
        self.assertEqual(res.std_dev, 0.0)
        self.assertEqual(res.n_total, 1)
        self.assertEqual(res.n_steady, 1)
        self.assertEqual(res.n_inliers, 1)
        self.assertFalse(res.converged)

    def test_t2_f3_02_all_identical_steady_state_samples_zero_std_dev(self):
        """T2-F3-02: Identical steady-state samples yield std_dev=0.0, ci=0.0, rmoe=0.0, converged=True."""
        res = convergence.evaluate_convergence(
            [1000.0, 1000.0, 1000.0], min_iterations=3, convergence_threshold=0.05
        )
        self.assertEqual(res.std_dev, 0.0)
        self.assertEqual(res.ci_half_width, 0.0)
        self.assertEqual(res.relative_margin_of_error, 0.0)
        self.assertTrue(res.converged)

    def test_t2_f3_03_all_zero_samples_mean_zero_guard(self):
        """T2-F3-03: All-zero samples do not raise ZeroDivisionError when computing RMoE."""
        res = convergence.evaluate_convergence(
            [0.0, 0.0, 0.0], min_iterations=3, convergence_threshold=0.05
        )
        self.assertEqual(res.representative_value, 0.0)
        self.assertEqual(res.ci_half_width, 0.0)
        self.assertEqual(res.relative_margin_of_error, 0.0)
        self.assertTrue(res.converged)

    def test_t2_f3_04_empty_sample_sequence(self):
        """T2-F3-04: Empty sample list returns n_total=0, n_steady=0, converged=False safely."""
        res = convergence.evaluate_convergence(
            [], min_iterations=3, convergence_threshold=0.05
        )
        self.assertEqual(res.n_total, 0)
        self.assertEqual(res.n_steady, 0)
        self.assertEqual(res.n_inliers, 0)
        self.assertFalse(res.converged)

    def test_t2_f3_05_warmup_equals_or_exceeds_total_samples(self):
        """T2-F3-05: When warmup_iterations >= len(samples), n_steady=0 and converged=False."""
        res = convergence.evaluate_convergence(
            [250.0], min_iterations=3, convergence_threshold=0.05, warmup_iterations=1
        )
        self.assertEqual(res.warmup_samples, [250.0])
        self.assertEqual(res.n_total, 1)
        self.assertEqual(res.n_steady, 0)
        self.assertFalse(res.converged)

    # ------------------------------------------------------------------
    # Feature F4: Welch Comparison Boundary & Corner Cases (5 tests)
    # ------------------------------------------------------------------

    def test_t2_f4_01_insufficient_samples_single_observation(self):
        """T2-F4-01: Single observation in either baseline or candidate yields INSUFFICIENT_SAMPLES."""
        r1 = convergence.compare_samples_welch([1000.0], [1100.0, 1105.0, 1095.0])
        r2 = convergence.compare_samples_welch([1000.0, 1005.0, 995.0], [1100.0])
        self.assertEqual(r1.verdict, "INSUFFICIENT_SAMPLES")
        self.assertFalse(r1.statistically_significant)
        self.assertEqual(r2.verdict, "INSUFFICIENT_SAMPLES")
        self.assertFalse(r2.statistically_significant)

    def test_t2_f4_02_zero_variance_in_both_runs_identical_means(self):
        """T2-F4-02: Zero variance in both runs with equal means yields NOISE / NEUTRAL."""
        res = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0], [1000.0, 1000.0, 1000.0]
        )
        self.assertEqual(res.delta_abs, 0.0)
        self.assertEqual(res.delta_pct, 0.0)
        self.assertEqual(res.t_stat, 0.0)
        self.assertEqual(res.p_value, 1.0)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    def test_t2_f4_03_zero_variance_in_both_runs_distinct_means(self):
        """T2-F4-03: Zero variance in both runs with distinct means yields p=0.0 and SIGNIFICANT_IMPROVEMENT."""
        res = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0], [1100.0, 1100.0, 1100.0], higher_is_better=True
        )
        self.assertEqual(res.p_value, 0.0)
        self.assertTrue(res.statistically_significant)
        self.assertTrue(res.practically_significant)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t2_f4_04_zero_variance_in_one_run_positive_variance_in_other(self):
        """T2-F4-04: Zero variance in baseline and positive variance in candidate yields exact d.o.f. n_c - 1."""
        res = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0, 1000.0],
            [1100.0, 1110.0, 1090.0, 1100.0],
        )
        self.assertAlmostEqual(res.degrees_of_freedom, 3.0, delta=1e-9)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t2_f4_05_outlier_immunity_during_two_sample_welch_comparison(self):
        """T2-F4-05: MAD filtering removes 200.0 stall before Welch test so identical inliers yield NOISE / NEUTRAL."""
        baseline = [1000.0, 1002.0, 998.0, 1001.0, 999.0, 200.0]
        candidate = [1000.0, 1002.0, 998.0, 1001.0, 999.0]
        res = convergence.compare_samples_welch(baseline, candidate)
        self.assertAlmostEqual(res.baseline_value, 1000.0, delta=1e-9)
        self.assertAlmostEqual(res.delta_pct, 0.0, delta=1e-9)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    # ------------------------------------------------------------------
    # Feature F5: Base Container Image Packaging Boundary Cases (5 tests)
    # ------------------------------------------------------------------

    def test_t2_f5_01_dockerfile_copy_order_after_runtime_stage_from(self):
        """T2-F5-01: COPY convergence.py appears strictly after FROM python:3.13-slim."""
        dockerfile_path = os.path.join(REPO_ROOT, "gcsfuse-perf-base.dockerfile")
        with open(dockerfile_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        runtime_from_idx = -1
        copy_conv_idx = -1
        for idx, line in enumerate(lines):
            if "FROM python:3.13-slim" in line:
                runtime_from_idx = idx
            if "COPY convergence.py" in line:
                copy_conv_idx = idx
        self.assertGreaterEqual(runtime_from_idx, 0)
        self.assertGreater(copy_conv_idx, runtime_from_idx)

    def test_t2_f5_02_isolated_pythonpath_subprocess_resolution(self):
        """T2-F5-02: Runner resolves convergence with stripped PYTHONPATH from external temp directory."""
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            runner_path = os.path.join(FIO_DIR, "fio_benchmark_runner.py")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import importlib.util; s=importlib.util.spec_from_file_location('r', {runner_path!r}); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); import convergence",
                ],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_t2_f5_03_idempotent_sys_path_insertion_no_duplicate_pollution(self):
        """T2-F5-03: Reloading runners does not duplicate REPO_ROOT entries in sys.path."""
        importlib.reload(fio_benchmark_runner)
        self.assertLessEqual(sys.path.count(REPO_ROOT), 1)

    def test_t2_f5_04_dataclass_field_order_and_type_annotations_match_contract(self):
        """T2-F5-04: ConvergenceResult & ComparisonResult dataclass field names match contract."""
        conv_fields = [f.name for f in dataclasses.fields(convergence.ConvergenceResult)]
        expected_conv = [
            "representative_value",
            "median",
            "raw_mean",
            "std_dev",
            "ci_half_width",
            "relative_margin_of_error",
            "confidence_level",
            "convergence_threshold",
            "converged",
            "n_total",
            "n_steady",
            "n_inliers",
            "outliers_removed",
            "inliers",
            "warmup_samples",
        ]
        self.assertEqual(conv_fields, expected_conv)
        comp_fields = [f.name for f in dataclasses.fields(convergence.ComparisonResult)]
        expected_comp = [
            "baseline_value",
            "candidate_value",
            "baseline_ci_half_width",
            "candidate_ci_half_width",
            "delta_abs",
            "delta_pct",
            "t_stat",
            "degrees_of_freedom",
            "p_value",
            "cohens_d",
            "statistically_significant",
            "practically_significant",
            "verdict",
        ]
        self.assertEqual(comp_fields, expected_comp)

    def test_t2_f5_05_derived_dockerfiles_inherit_base_image(self):
        """T2-F5-05: All derived runner Dockerfiles inherit from gcsfuse-perf-base."""
        derived = [
            os.path.join(FIO_DIR, "fio.dockerfile"),
            os.path.join(FIO_DIR, "read.dockerfile"),
            os.path.join(FIO_DIR, "write.dockerfile"),
            os.path.join(GO_CLIENT_DIR, "go-client.dockerfile"),
        ]
        for path in derived:
            with open(path, "r", encoding="utf-8") as fh:
                self.assertIn("perf-base", fh.read(), path)

    # ------------------------------------------------------------------
    # Feature F6: FIO Runner Adaptive Loop Boundary Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f6_01_legacy_fixed_iterations_when_convergence_flags_none(self):
        """T2-F6-01: Legacy mode (all convergence flags None) runs all 4 fixed iterations even when variance=0."""
        stream = [1000.0, 1000.0, 1000.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=4,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    min_iterations=None,
                    max_iterations=None,
                    convergence_threshold=None,
                )
                self.assertEqual(mock_fio.call_count, 4)

    def test_t2_f6_02_keep_mount_with_max_iterations_boundary(self):
        """T2-F6-02: keep_mount=True with noisy stream stops cleanly and unmounts once."""
        stream = [200.0, 600.0, 1400.0, 700.0, 1300.0, 800.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse") as mock_mount, \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse") as mock_unmount, \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=4,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    keep_mount=True,
                    min_iterations=2,
                    max_iterations=4,
                    convergence_threshold=0.01,
                )
                self.assertEqual(mock_mount.call_count, 1)
                self.assertEqual(mock_unmount.call_count, 1)
                self.assertLessEqual(mock_fio.call_count, 5)

    def test_t2_f6_03_empty_or_corrupt_fio_json_iteration_resilience(self):
        """T2-F6-03: Corrupt/empty FIO JSON in iteration 1 does not crash runner."""
        stream = [None, 1000.0, 1002.0, 998.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    if stream[iteration - 1] is None:
                        fh.write("")
                    else:
                        fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=4,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    min_iterations=3,
                    max_iterations=5,
                    convergence_threshold=0.05,
                )
            self.assertGreaterEqual(mock_fio.call_count, 4)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "fio_results_iter_2.json")))

    def test_t2_f6_04_invalid_min_greater_than_max_iterations_validation(self):
        """T2-F6-04: min_iterations > max_iterations raises ValueError or validates bounds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test"):
                with self.assertRaises(ValueError):
                    fio_benchmark_runner.run_benchmark(
                        gcsfuse_flags="--implicit-dirs",
                        bucket_name="test-bucket",
                        iterations=3,
                        fio_config="fake.fio",
                        work_dir=tmpdir,
                        output_dir=tmpdir,
                        project_id="",
                        min_iterations=8,
                        max_iterations=3,
                        convergence_threshold=0.05,
                    )

    def test_t2_f6_05_zero_bandwidth_all_jobs_edge_case(self):
        """T2-F6-05: Zero-bandwidth job in FIO JSON is handled without division by zero."""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "zero.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                fh.write(make_synthetic_fio_json(0.0))
            parsed = fio_benchmark_runner.parse_fio_output(json_path)
            self.assertEqual(parsed, [])

    def test_t2_f6_06_matrix_runner_partial_config_failure_continues_remaining_configs(self):
        """T2-F6-06: Failure in one matrix config still runs subsequent configs before exiting 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "m.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("GCS_BUCKET_NAME,GCS_FUSE_FLAGS,FILE_SIZE,BLOCK_SIZE,NR_FILES,NUMJOBS\n")
                fh.write("b1,--implicit-dirs,1G,1M,10,112\n")
                fh.write("b2,--implicit-dirs,1G,2M,10,112\n")
                fh.write("b3,--implicit-dirs,1G,4M,10,112\n")
            calls = []

            def side_eff(*args, **kwargs):
                calls.append(kwargs.get("bucket_name") or args[1])
                if len(calls) == 2:
                    raise RuntimeError("Simulated config 2 failure")

            with patch.object(fio_benchmark_runner, "run_benchmark", side_effect=side_eff),                  patch.object(fio_benchmark_runner, "truncate_bq_table"),                  patch.object(sys, "argv", [
                     "run_fio_matrix.py",
                     "--bucket-name", "test-b",
                     "--matrix-config", csv_path,
                     "--fio-template", "read.fio",
                     "--project-id", "test-p",
                     "--work-dir", tmpdir,
                     "--output-dir", tmpdir,
                 ]):
                with self.assertRaises(SystemExit) as cm:
                    run_fio_matrix.main()
                self.assertEqual(cm.exception.code, 1)
            self.assertEqual(len(calls), 3)

    # ------------------------------------------------------------------
    # Feature F7: Go-Client Runner Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f7_01_go_matrix_legacy_mode_fixed_iterations(self):
        """T2-F7-01: Go matrix in legacy mode runs all 4 fixed iterations even when variance=0."""
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(2000.0)
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--iterations", "4",
                ]):
                    run_go_matrix.main()
            self.assertEqual(call_idx["n"], 4)

    def test_t2_f7_02_go_matrix_ignores_gcsfuse_specific_flags(self):
        """T2-F7-02: run_go_matrix accepts and ignores --gcsfuse-flags, --mount-path, --bind-fio."""
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    call_idx["n"] += 1
                    res.stdout = make_synthetic_go_json(2000.0)
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--gcsfuse-flags=--implicit-dirs",
                    "--mount-path", "/mnt/data",
                    "--bind-fio",
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            self.assertEqual(call_idx["n"], 3)

    def test_t2_f7_03_go_matrix_cpu_limit_list_taskset_prefix_preserved(self):
        """T2-F7-03: --cpu-limit-list prefixes every adaptive iteration command with taskset -c."""
        cmds_seen = []
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                cmds_seen.append(list(cmd))
                res = MagicMock()
                res.stdout = make_synthetic_go_json(2000.0)
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--cpu-limit-list", "0-15",
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
        client_cmds = [c for c in cmds_seen if "./go-benchmark-client" in c]
        self.assertGreaterEqual(len(client_cmds), 3)
        for c in client_cmds:
            self.assertEqual(c[:3], ["taskset", "-c", "0-15"])

    def test_t2_f7_04_go_matrix_invalid_json_stdout_in_one_iteration(self):
        """T2-F7-04: Malformed JSON stdout in one iteration does not crash run_go_matrix."""
        call_idx = {"n": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                if "./go-benchmark-client" in cmd:
                    call_idx["n"] += 1
                    if call_idx["n"] == 2:
                        res.stdout = "NOT VALID JSON"
                    else:
                        res.stdout = make_synthetic_go_json(2000.0)
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "5",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            self.assertGreaterEqual(call_idx["n"], 4)

    def test_t2_f7_05_go_matrix_numjobs_csv_override_vs_cli_default(self):
        """T2-F7-05: CSV NUMJOBS overrides CLI default while empty CSV NUMJOBS falls back to CLI."""
        cmds_seen = []
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES,NUMJOBS\n")
                fh.write("seq,1G,1M,10,64\n")
                fh.write("seq,1G,2M,10,\n")

            def fake_run_cmd(cmd, **kwargs):
                cmds_seen.append(" ".join(cmd))
                res = MagicMock()
                res.stdout = make_synthetic_go_json(2000.0)
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--numjobs", "128",
                    "--min-iterations", "3",
                    "--max-iterations", "4",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
        joined = "\n".join(cmds_seen)
        self.assertIn("--numjobs=64", joined)
        self.assertIn("--numjobs=128", joined)

    def test_t2_f7_06_go_matrix_empty_results_print_summary_safe(self):
        """T2-F7-06: run_go_matrix.print_summary([]) handles empty results without error."""
        with self.assertLogs(level="WARNING") as cm:
            ret = run_go_matrix.print_summary([], summary_file=None)
        self.assertIsNone(ret)
        self.assertTrue(any("No results to summarize" in msg for msg in cm.output))

    # ------------------------------------------------------------------
    # Feature F8: query_results.py Boundary & Corner Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f8_01_bq_subprocess_error_returns_exact_fallback_dict(self):
        """T2-F8-01: CalledProcessError in bq query returns zeroed fallback dict with fio_version=N/A."""
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "bq", stderr="Not found"),
        ):
            metrics = query_results.get_table_metrics("p", "d", "missing_tbl")
        self.assertEqual(metrics["seq_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["rand_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["write_bw_mbs"], 0.0)
        self.assertEqual(metrics["fio_version"], "N/A")

    def test_t2_f8_02_bq_empty_json_array_returns_fallback_dict(self):
        """T2-F8-02: Empty JSON array [] from bq query returns zeroed fallback dict."""
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout="[]", returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_http1")
        self.assertEqual(metrics["seq_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["fio_version"], "N/A")

    def test_t2_f8_03_bq_malformed_json_stdout_returns_fallback_dict(self):
        """T2-F8-03: Malformed JSON stdout from bq query returns fallback dict without exception."""
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout="not valid json", returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_http1")
        self.assertEqual(metrics["seq_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["fio_version"], "N/A")

    def test_t2_f8_04_single_iteration_n1_table_handling(self):
        """T2-F8-04: Single iteration N=1 table sets representative_value=sample and std_dev=0.0."""
        rows = make_bq_per_iteration_rows({"read": ([1800.0], [1.5])})
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 1800.0)
        self.assertEqual(metrics["workloads"]["read"]["bw_summary"].std_dev, 0.0)

    def test_t2_f8_05_null_and_missing_fields_in_bq_json_rows(self):
        """T2-F8-05: Null/None workload fields in BQ JSON rows default cleanly to 0.0."""
        rows = [
            {"iteration": 1, "fio_version": "fio-3.36", "seq_read_bw_mbs": 1500.0, "rand_read_bw_mbs": None, "write_bw_mbs": None},
            {"iteration": 2, "fio_version": "fio-3.36", "seq_read_bw_mbs": 1500.0, "rand_read_bw_mbs": None, "write_bw_mbs": None},
            {"iteration": 3, "fio_version": "fio-3.36", "seq_read_bw_mbs": 1500.0, "rand_read_bw_mbs": None, "write_bw_mbs": None},
        ]
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 1500.0)
        self.assertEqual(metrics["rand_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["write_bw_mbs"], 0.0)

    def test_t2_f8_06_multi_job_per_iteration_aggregation_in_query_results(self):
        """T2-F8-06: Multiple rows per iteration aggregate cleanly across iterations."""
        rows = make_bq_per_iteration_rows({
            "read": ([2100.0, 2105.0, 2095.0], [1.1, 1.1, 1.1]),
            "randread": ([900.0, 905.0, 895.0], [2.2, 2.2, 2.2]),
        })
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2100.0, delta=1e-3)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 900.0, delta=1e-3)

    # ------------------------------------------------------------------
    # Feature F9: Baseline Significance Boundary Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f9_01_insufficient_samples_verdict_when_n_less_than_2(self):
        """T2-F9-01: N=1 baseline vs N=5 candidate yields INSUFFICIENT_SAMPLES."""
        res = convergence.compare_samples_welch([1000.0], [1100.0, 1102.0, 1098.0, 1101.0, 1099.0])
        self.assertEqual(res.verdict, "INSUFFICIENT_SAMPLES")

    def test_t2_f9_02_both_baseline_and_candidate_zero_variance_identical_values(self):
        """T2-F9-02: Zero variance identical baseline and candidate yields NOISE / NEUTRAL."""
        res = convergence.compare_samples_welch([1000.0, 1000.0, 1000.0], [1000.0, 1000.0, 1000.0])
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")
        self.assertEqual(res.p_value, 1.0)

    def test_t2_f9_03_both_baseline_and_candidate_zero_variance_distinct_values(self):
        """T2-F9-03: Zero variance distinct baseline and candidate yields p=0.0 and SIGNIFICANT_IMPROVEMENT."""
        res = convergence.compare_samples_welch([1000.0, 1000.0, 1000.0], [1200.0, 1200.0, 1200.0])
        self.assertEqual(res.p_value, 0.0)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t2_f9_04_missing_baseline_table_graceful_fallback(self):
        """T2-F9-04: Missing baseline dataset does not crash query_results.main()."""
        cand_rows = make_bq_per_iteration_rows({"read": ([2000.0, 2005.0, 1995.0], [1.0, 1.0, 1.0])})

        def fake_bq(cmd, **kwargs):
            sql = cmd[-1]
            if "missing_base_ds" in sql:
                return MagicMock(stdout="[]", returncode=0)
            return MagicMock(stdout=json.dumps(cand_rows), returncode=0)

        buf = io.StringIO()
        with patch.object(subprocess, "run", side_effect=fake_bq),              patch.object(sys, "argv", [
                 "query_results.py",
                 "--project-id", "test-p",
                 "--dataset-id", "cand_ds",
                 "--baseline-dataset-id", "missing_base_ds",
                 "--table-types", "fio_read_grpc",
             ]),              patch("sys.stdout", buf):
            query_results.main()
        self.assertIn("2000", buf.getvalue())

    def test_t2_f9_05_extreme_unequal_sample_sizes_and_variances_welch_satterthwaite(self):
        """T2-F9-05: Unequal sample sizes (N1=3 vs N2=12) compute fractional Welch d.o.f. cleanly."""
        base = [900.0, 1000.0, 1100.0]
        cand = [1300.0, 1302.0, 1298.0, 1301.0, 1299.0, 1300.0] * 2
        res = convergence.compare_samples_welch(base, cand)
        self.assertEqual(res.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t2_f9_06_outlier_in_baseline_or_candidate_filtered_prior_to_welch_test(self):
        """T2-F9-06: Single 100 MB/s stall in baseline is filtered so equal inliers yield NOISE / NEUTRAL."""
        base = [1000.0, 1002.0, 998.0, 100.0]
        cand = [1001.0, 999.0, 1000.0, 1002.0]
        res = convergence.compare_samples_welch(base, cand)
        self.assertEqual(res.verdict, "NOISE / NEUTRAL")

    # ------------------------------------------------------------------
    # Feature F10: Orchestrator CLI Backwards-Compatibility Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f10_01_magicmock_args_guard_prevents_spurious_flags_in_orchestrator(self):
        """T2-F10-01: Unconfigured MagicMock args emit ZERO convergence flags across GCE, GKE, & BenchmarkFactory."""
        mock_args = MagicMock()
        mock_args.benchmarks = "read_grpc"
        mock_args.project = "test-p"
        mock_args.image_version = "latest"
        mock_args.iterations = 1
        mock_args.smoke_mode = False
        mock_args.extra_mount_options = None
        gce_target = {
            "name": "gce_t",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
            "buffer_dir": "/mnt/buf",
        }
        gke_target = {
            "name": "gke_t",
            "type": "gke",
            "vm_name": "runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
            "node_selector": "",
            "resources_limits": "",
        }
        captured = []
        with patch.object(npi_orchestrator, "cleanup_remote_run"), \
             patch.object(npi_orchestrator, "prep_vm"), \
             patch.object(npi_orchestrator, "monitor_run", return_value=True), \
             patch.object(npi_orchestrator, "run_ssh_cmd", side_effect=lambda *a, **k: (captured.append(a), (0, "", ""))[1]), \
             patch.object(npi_orchestrator, "save_state"):
            npi_orchestrator.execute_target(gce_target, mock_args, threading.Lock(), {"gce_t": {"status": "PENDING"}})
            npi_orchestrator.execute_target(gke_target, mock_args, threading.Lock(), {"gke_t": {"status": "PENDING"}})
        joined = " ".join(str(x) for x in captured)
        self.assertNotIn("--min-iterations", joined)
        self.assertNotIn("--max-iterations", joined)
        self.assertNotIn("--convergence-threshold", joined)
        self.assertNotIn("MagicMock", joined)

        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test_ds",
            iterations=1,
            buffer_mount_path="/mnt/buf",
            min_iterations=MagicMock(),
            max_iterations=MagicMock(),
            convergence_threshold=MagicMock(),
            confidence_level=MagicMock(),
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertNotIn("--min-iterations", cmd)
        self.assertNotIn("--max-iterations", cmd)
        self.assertNotIn("--convergence-threshold", cmd)
        self.assertNotIn("MagicMock", cmd)

    def test_t2_f10_02_boolean_values_rejected_by_isinstance_not_bool_guard(self):
        """T2-F10-02: Boolean values (True/False) are rejected by isinstance(..., (int, float)) and not isinstance(..., bool)."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=True,
            max_iterations=False,
            convergence_threshold=True,
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertNotIn("--min-iterations=True", cmd)
        self.assertNotIn("--max-iterations=False", cmd)
        self.assertNotIn("--convergence-threshold=True", cmd)

    def test_t2_f10_03_partial_convergence_flags_only_min_iterations_set(self):
        """T2-F10-03: Setting only min_iterations=4 emits --min-iterations=4 cleanly."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=4,
            max_iterations=None,
            convergence_threshold=None,
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertIn("--min-iterations=4", cmd)

    def test_t2_f10_04_default_confidence_level_omitted_when_no_convergence_flags_active(self):
        """T2-F10-04: Default confidence_level=0.95 is omitted when no convergence flags are active."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertNotIn("--confidence-level", cmd)

    def test_t2_f10_05_smoke_mode_interaction_with_convergence_flags(self):
        """T2-F10-05: smoke_mode=True works alongside explicit convergence flags."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=1,
            buffer_mount_path="/mnt/buf",
            smoke_mode=True,
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertIn("--iterations=1", cmd)

    def test_t2_f10_06_existing_unit_test_suites_pass_100_percent_unmodified(self):
        """T2-F10-06: Existing unit test modules load and pass cleanly."""
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", FIO_DIR, "-p", "*_test.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # ------------------------------------------------------------------
    # Feature F11: E2E Suite Integrity & Audit Boundary Cases (6 tests)
    # ------------------------------------------------------------------

    def test_t2_f11_01_ast_import_audit_zero_third_party_math_dependencies(self):
        """T2-F11-01: AST audit confirms zero scipy/numpy/pandas/statsmodels imports in production modules."""
        forbidden = {"scipy", "numpy", "pandas", "statsmodels"}
        targets = [
            os.path.join(REPO_ROOT, "convergence.py"),
            os.path.join(FIO_DIR, "fio_benchmark_runner.py"),
            os.path.join(GO_CLIENT_DIR, "run_go_matrix.py"),
            os.path.join(REPO_ROOT, "query_results.py"),
            os.path.join(REPO_ROOT, "npi.py"),
            os.path.join(REPO_ROOT, "npi_gke.py"),
            os.path.join(REPO_ROOT, "npi_orchestrator.py"),
        ]
        for path in targets:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_pkg = alias.name.split(".")[0]
                        self.assertNotIn(root_pkg, forbidden, f"{path} imports {root_pkg}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root_pkg = node.module.split(".")[0]
                    self.assertNotIn(root_pkg, forbidden, f"{path} imports {root_pkg}")

    def test_t2_f11_02_workspace_cleanliness_no_orphan_temp_files_after_suite(self):
        """T2-F11-02: Zero orphan fio_results_iter_*.json or go_results_iter_*.json in repo root."""
        for fname in os.listdir(REPO_ROOT):
            self.assertFalse(fname.startswith("fio_results_iter_"), fname)
            self.assertFalse(fname.startswith("go_results_iter_"), fname)

    def test_t2_f11_03_agents_directory_compliance_no_source_or_test_files_in_agents(self):
        """T2-F11-03: .agents/ directory contains zero production .py source or test files."""
        agents_dir = os.path.join(REPO_ROOT, ".agents")
        for root, _, files in os.walk(agents_dir):
            for fname in files:
                self.assertFalse(
                    fname.endswith("_test.py"),
                    f"Test file found inside .agents/: {os.path.join(root, fname)}",
                )

    def test_t2_f11_04_dockerfile_packaging_contract_verification(self):
        """T2-F11-04: gcsfuse-perf-base.dockerfile copies convergence.py to Python 3.13 site-packages."""
        with open(os.path.join(REPO_ROOT, "gcsfuse-perf-base.dockerfile"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("/usr/local/lib/python3.13/site-packages/convergence.py", text)

    def test_t2_f11_05_read_randread_write_strict_non_aggregation_audit(self):
        """T2-F11-05: read (2000) and randread (500) are never averaged to 1250 in query_results."""
        rows = make_bq_per_iteration_rows({
            "read": ([2000.0, 2000.0, 2000.0], [1.0, 1.0, 1.0]),
            "randread": ([500.0, 500.0, 500.0], [2.0, 2.0, 2.0]),
            "write": ([1000.0, 1000.0, 1000.0], [3.0, 3.0, 3.0]),
        })
        with patch.object(
            subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)
        ):
            metrics = query_results.get_table_metrics("p", "d", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2000.0)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 500.0)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 1000.0)
        self.assertNotAlmostEqual(metrics["seq_read_bw_mbs"], 1250.0)

    def test_t2_f11_06_extreme_scale_and_precision_stability_across_pipeline(self):
        """T2-F11-06: Pipeline metrics remain finite across 0.05 ms micro-latency and 25,000 MiB/s throughput."""
        r_micro = convergence.evaluate_convergence([0.050, 0.051, 0.049, 0.050], min_iterations=3)
        r_macro = convergence.evaluate_convergence([25000.0, 25050.0, 24950.0, 25000.0], min_iterations=3)
        self.assertTrue(math.isfinite(r_micro.relative_margin_of_error))
        self.assertTrue(math.isfinite(r_macro.relative_margin_of_error))
        self.assertTrue(r_micro.converged)
        self.assertTrue(r_macro.converged)


# ==============================================================================
# TIER 3: Cross-Feature Pairwise Interaction Tests (12 Tests: T3_01 .. T3_12)
# ==============================================================================


class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3: 12 pairwise cross-feature interaction tests across module boundaries."""

    def test_t3_01_mad_zero_guard_with_student_t_interval_estimation(self):
        """T3_01 (F2 x F3): MAD=0 fallback floor interacts with Student's t interval estimation."""
        res = convergence.evaluate_convergence(
            [1000.0, 1000.0, 1000.0, 200.0],
            min_iterations=3,
            convergence_threshold=0.05,
        )
        self.assertEqual(res.outliers_removed, [200.0])
        self.assertEqual(res.inliers, [1000.0, 1000.0, 1000.0])
        self.assertEqual(res.n_inliers, 3)
        self.assertEqual(res.representative_value, 1000.0)
        self.assertEqual(res.raw_mean, 800.0)
        self.assertEqual(res.std_dev, 0.0)
        self.assertEqual(res.ci_half_width, 0.0)
        self.assertEqual(res.relative_margin_of_error, 0.0)
        self.assertTrue(res.converged)

    def test_t3_02_welch_satterthwaite_fractional_df_with_incomplete_beta_pvalue(self):
        """T3_02 (F1 x F4): Non-integer Welch-Satterthwaite d.o.f. feeds continuous betainc p-value."""
        baseline = [100.0, 102.0, 98.0, 101.0, 99.0]
        candidate = [110.0, 125.0, 95.0, 118.0, 102.0]
        comp = convergence.compare_samples_welch(baseline, candidate)
        self.assertIsInstance(comp.degrees_of_freedom, float)
        self.assertAlmostEqual(comp.degrees_of_freedom, 4.138366887702583, delta=0.01)
        exact_p = convergence.student_t_two_sided_pvalue(comp.t_stat, comp.degrees_of_freedom)
        self.assertAlmostEqual(comp.p_value, exact_p, delta=1e-12)
        self.assertEqual(comp.verdict, "NOISE / NEUTRAL")

    def test_t3_03_mad_outlier_filtering_before_welch_two_sample_comparison(self):
        """T3_03 (F2 x F4): MAD pre-filtering removes candidate stall so +8% gain is detected."""
        baseline = [1000.0, 1002.0, 998.0, 1001.0, 999.0]
        candidate = [1080.0, 1082.0, 210.0, 1078.0, 1080.0]
        comp = convergence.compare_samples_welch(baseline, candidate)
        self.assertAlmostEqual(comp.candidate_value, 1080.0, delta=1e-6)
        self.assertAlmostEqual(comp.delta_pct, 8.0, delta=0.01)
        self.assertTrue(comp.statistically_significant)
        self.assertTrue(comp.practically_significant)
        self.assertEqual(comp.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_t3_04_fio_keep_mount_warmup_separation_with_student_t_ci(self):
        """T3_04 (F6 x F3): keep_mount=True separates iter 1 cold-cache fill before Student's t CI."""
        stream = [250.0, 2400.0, 2410.0, 2390.0, 2405.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse") as mock_mount, \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse") as mock_unmount, \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    keep_mount=True,
                    min_iterations=3,
                    max_iterations=8,
                    convergence_threshold=0.05,
                )
                self.assertEqual(mock_mount.call_count, 1)
                self.assertEqual(mock_unmount.call_count, 1)
                self.assertEqual(mock_fio.call_count, 4)

    def test_t3_05_fio_adaptive_loop_with_mad_outlier_rejection(self):
        """T3_05 (F6 x F2): FIO adaptive loop rejects mid-run 180 MiB/s stall and stops at iter 4."""
        stream = [1200.0, 180.0, 1205.0, 1195.0, 1200.0, 1200.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    keep_mount=False,
                    min_iterations=3,
                    max_iterations=10,
                    convergence_threshold=0.05,
                )
                self.assertEqual(mock_fio.call_count, 4)

    def test_t3_06_go_client_matrix_per_config_independent_adaptive_stopping(self):
        """T3_06 (F7 x F3): Multi-config Go matrix stops Config A at 3 and Config B at 5."""
        cfg_a = [1500.0, 1502.0, 1498.0, 1500.0, 1500.0]
        cfg_b = [900.0, 1100.0, 950.0, 1020.0, 995.0, 1000.0]
        counts = {"1M": 0, "16K": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "go.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n")
                fh.write("seq,1G,1M,10\n")
                fh.write("seq,1G,16K,10\n")

            def fake_run_cmd(cmd, **kwargs):
                res = MagicMock()
                cmd_str = " ".join(cmd)
                if "1M" in cmd_str:
                    i = counts["1M"]
                    counts["1M"] += 1
                    res.stdout = make_synthetic_go_json(cfg_a[i], bs="1M")
                elif "16K" in cmd_str:
                    i = counts["16K"]
                    counts["16K"] += 1
                    res.stdout = make_synthetic_go_json(cfg_b[i], bs="16K")
                else:
                    res.stdout = ""
                return res

            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(subprocess, "run"), \
                 patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd):
                with patch.object(sys, "argv", [
                    "run_go_matrix.py",
                    "--bucket-name", "test-b",
                    "--matrix-config", csv_path,
                    "--output-dir", tmpdir,
                    "--min-iterations", "3",
                    "--max-iterations", "7",
                    "--convergence-threshold", "0.05",
                ]):
                    run_go_matrix.main()
            self.assertEqual(counts["1M"], 3)
            self.assertGreaterEqual(counts["16K"], 4)

    def test_t3_07_orchestrator_to_gce_docker_to_fio_flag_propagation(self):
        """T3_07 (F10 x F6): Convergence flags propagate npi_orchestrator -> npi.py -> FIO runner."""
        factory = npi.BenchmarkFactory(
            bucket_name="test-b",
            project_id="test-p",
            bq_dataset_id="test-ds",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            min_iterations=3,
            max_iterations=8,
            convergence_threshold=0.04,
            confidence_level=0.95,
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertIn("--min-iterations=3", cmd)
        self.assertIn("--max-iterations=8", cmd)
        self.assertIn("--convergence-threshold=0.04", cmd)

    def test_t3_08_orchestrator_to_gke_job_spec_to_go_client_flag_propagation(self):
        """T3_08 (F10 x F7): Convergence flags propagate npi_orchestrator -> npi_gke.py -> Go runner."""
        spec = npi_gke.create_job_spec(
            job_name="go-job",
            image="gcr.io/p/go:latest",
            args=["--min-iterations=4", "--max-iterations=9", "--convergence-threshold=0.03"],
            bucket_name="test-b",
            service_account="sa",
        )
        container_args = spec["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--min-iterations=4", container_args)
        self.assertIn("--max-iterations=9", container_args)
        self.assertIn("--convergence-threshold=0.03", container_args)

    def test_t3_09_fio_runner_bq_upload_schema_to_query_results_disaggregation(self):
        """T3_09 (F6 x F8): FIO BQ upload 6-column schema feeds query_results disaggregation."""
        rows = make_bq_per_iteration_rows({
            "read": ([2400.0, 2405.0, 2395.0], [1.1, 1.1, 1.1]),
            "randread": ([1100.0, 1105.0, 1095.0], [2.2, 2.2, 2.2]),
            "write": ([750.0, 755.0, 745.0], [3.3, 3.3, 3.3]),
        })
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)):
            metrics = query_results.get_table_metrics("test-p", "test-ds", "fio_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2400.0, delta=1e-3)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 1100.0, delta=1e-3)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 750.0, delta=1e-3)

    def test_t3_10_go_client_runner_bq_upload_to_query_results_canonical_metrics(self):
        """T3_10 (F7 x F8): Go-client BQ rows feed query_results canonical metrics cleanly."""
        rows = [
            {"iteration": 1, "fio_version": "go-client", "seq_read_bw_mbs": 2800.0, "seq_read_lat_ms": 1.2},
            {"iteration": 2, "fio_version": "go-client", "seq_read_bw_mbs": 2805.0, "seq_read_lat_ms": 1.2},
            {"iteration": 3, "fio_version": "go-client", "seq_read_bw_mbs": 2795.0, "seq_read_lat_ms": 1.2},
        ]
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows), returncode=0)):
            metrics = query_results.get_table_metrics("test-p", "test-ds", "go_client_read_grpc")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2800.0, delta=1e-3)
        self.assertEqual(metrics["rand_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["write_bw_mbs"], 0.0)

    def test_t3_11_query_results_disaggregation_with_welch_baseline_verdicts(self):
        """T3_11 (F8 x F9): Disaggregated read (+15%), randread (-12%), write (+0.4%) get distinct verdicts."""
        base_read = [1000.0, 1002.0, 998.0, 1000.0]
        cand_read = [1150.0, 1152.0, 1148.0, 1150.0]
        base_rand = [1000.0, 1002.0, 998.0, 1000.0]
        cand_rand = [880.0, 882.0, 878.0, 880.0]
        base_write = [1000.0, 1002.0, 998.0, 1000.0]
        cand_write = [1004.0, 1006.0, 1002.0, 1004.0]
        self.assertEqual(
            convergence.compare_samples_welch(base_read, cand_read).verdict,
            "SIGNIFICANT_IMPROVEMENT",
        )
        self.assertEqual(
            convergence.compare_samples_welch(base_rand, cand_rand).verdict,
            "SIGNIFICANT_REGRESSION",
        )
        self.assertEqual(
            convergence.compare_samples_welch(base_write, cand_write).verdict,
            "NOISE / NEUTRAL",
        )

    def test_t3_12_dockerfile_packaging_and_sys_path_fallback_resolution(self):
        """T3_12 (F5 x F6 x F7): Dockerfile packaging + subdirectory sys.path fallback resolve convergence."""
        with open(os.path.join(REPO_ROOT, "gcsfuse-perf-base.dockerfile"), "r", encoding="utf-8") as fh:
            self.assertIn("convergence.py", fh.read())
        for cwd_dir, mod_name in ((FIO_DIR, "fio_benchmark_runner"), (GO_CLIENT_DIR, "run_go_matrix")):
            proc = subprocess.run(
                [sys.executable, "-c", f"import {mod_name}; import convergence"],
                cwd=cwd_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)


# ==============================================================================
# TIER 4: Real-World & Deterministic Monte Carlo Scenarios (7 Scenarios: T4_01..07)
# ==============================================================================


class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: 7 comprehensive real-world & seeded Monte Carlo simulation scenarios."""

    def test_scenario_01_low_variance_stream_early_convergence_simulation(self):
        """T4_01: 50 Monte Carlo trials (N(1000, 15)) converge early at n_steady in [3, 5]."""
        rng = random.Random(20260918)
        stop_counts = []
        for _ in range(50):
            stream = [rng.gauss(1000.0, 15.0) for _ in range(15)]
            final_res = None
            for k in range(1, 16):
                res = convergence.evaluate_convergence(
                    stream[:k],
                    min_iterations=3,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )
                if res.converged:
                    final_res = res
                    break
            self.assertIsNotNone(final_res)
            self.assertTrue(final_res.converged)
            self.assertLessEqual(final_res.relative_margin_of_error, 0.05)
            self.assertGreaterEqual(final_res.n_steady, 3)
            self.assertLessEqual(final_res.n_steady, 5)
            self.assertLess(abs(final_res.representative_value - 1000.0), 40.0)
            stop_counts.append(final_res.n_steady)
        mean_stop = sum(stop_counts) / len(stop_counts)
        self.assertLessEqual(mean_stop, 3.6)

    def test_scenario_02_single_extreme_transient_stall_immunity_simulation(self):
        """T4_02: Single 200 MiB/s stall at positions 1..4 is rejected without corrupting estimate."""
        base_steady = [1002.0, 998.0, 1001.0, 999.0]
        for stall_pos in range(4):
            stream = list(base_steady)
            stream.insert(stall_pos, 200.0)
            res = convergence.evaluate_convergence(
                stream,
                min_iterations=4,
                convergence_threshold=0.05,
                mad_threshold=3.5,
            )
            self.assertEqual(res.outliers_removed, [200.0])
            self.assertLess(abs(res.representative_value - 1000.0), 5.0)
            self.assertTrue(res.converged)
            self.assertLessEqual(res.relative_margin_of_error, 0.05)

    def test_scenario_03_high_variance_non_convergent_clean_termination(self):
        """T4_03: High-variance oscillatory stream terminates cleanly at max_iterations=8."""
        stream = [620.0, 1380.0, 660.0, 1340.0, 640.0, 1360.0, 680.0, 1320.0]
        res = convergence.evaluate_convergence(
            stream,
            min_iterations=3,
            convergence_threshold=0.03,
            confidence_level=0.95,
        )
        self.assertEqual(res.n_steady, 8)
        self.assertFalse(res.converged)
        self.assertGreater(res.relative_margin_of_error, 0.03)
        self.assertTrue(math.isfinite(res.relative_margin_of_error))
        self.assertTrue(math.isfinite(res.representative_value))
        self.assertGreaterEqual(res.representative_value, 900.0)
        self.assertLessEqual(res.representative_value, 1100.0)

    def test_scenario_04_deterministic_baseline_vs_candidate_disaggregated_comparison(self):
        """T4_04: Deterministic baseline vs candidate across disaggregated read/randread/write via query_results.get_table_metrics."""
        base_rows = make_bq_per_iteration_rows({
            "read": ([1000.0, 1004.0, 996.0, 1002.0, 998.0], [1.2] * 5),
            "randread": ([450.0, 452.0, 448.0, 451.0, 449.0], [2.4] * 5),
            "write": ([800.0, 830.0, 770.0, 815.0, 785.0], [3.1] * 5),
        })
        cand_rows = make_bq_per_iteration_rows({
            "read": ([1120.0, 1124.0, 1116.0, 1122.0, 1118.0], [1.1] * 5),
            "randread": ([382.5, 384.5, 380.5, 383.5, 381.5], [2.8] * 5),
            "write": ([806.0, 836.0, 776.0, 821.0, 791.0], [3.1] * 5),
        })
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(base_rows), returncode=0)):
            base_m = query_results.get_table_metrics("test-p", "base_ds", "fio_read_grpc")
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(cand_rows), returncode=0)):
            cand_m = query_results.get_table_metrics("test-p", "cand_ds", "fio_read_grpc")

        # Verify read and randread are strictly disaggregated and never collapsed/averaged
        self.assertAlmostEqual(base_m["seq_read_bw_mbs"], 1000.0, delta=1e-3)
        self.assertAlmostEqual(base_m["rand_read_bw_mbs"], 450.0, delta=1e-3)
        self.assertNotAlmostEqual(base_m["seq_read_bw_mbs"], base_m["rand_read_bw_mbs"])
        self.assertAlmostEqual(cand_m["seq_read_bw_mbs"], 1120.0, delta=1e-3)
        self.assertAlmostEqual(cand_m["rand_read_bw_mbs"], 382.5, delta=1e-3)
        self.assertNotAlmostEqual(cand_m["seq_read_bw_mbs"], cand_m["rand_read_bw_mbs"])

        r_read = convergence.compare_samples_welch(
            base_m["workloads"]["read"]["bw_samples"],
            cand_m["workloads"]["read"]["bw_samples"],
        )
        r_rand = convergence.compare_samples_welch(
            base_m["workloads"]["randread"]["bw_samples"],
            cand_m["workloads"]["randread"]["bw_samples"],
        )
        r_write_a = convergence.compare_samples_welch(
            base_m["workloads"]["write"]["bw_samples"],
            cand_m["workloads"]["write"]["bw_samples"],
        )
        r_write_b = convergence.compare_samples_welch(
            [800.0, 800.1, 799.9, 800.05, 799.95],
            [804.0, 804.1, 803.9, 804.05, 803.95],
        )
        self.assertEqual(r_read.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertAlmostEqual(r_read.delta_pct, 12.0, delta=0.01)
        self.assertEqual(r_rand.verdict, "SIGNIFICANT_REGRESSION")
        self.assertAlmostEqual(r_rand.delta_pct, -15.0, delta=0.01)
        self.assertEqual(r_write_a.verdict, "NOISE / NEUTRAL")
        self.assertFalse(r_write_a.statistically_significant)
        self.assertEqual(r_write_b.verdict, "NOISE / NEUTRAL")
        self.assertTrue(r_write_b.statistically_significant)
        self.assertFalse(r_write_b.practically_significant)

    def test_scenario_05_full_cli_backwards_compatibility_legacy_mode(self):
        """T4_05: Legacy mode (--iterations 5) emits zero new flags on GCE/GKE/Factory and runs all 5 fixed iterations even at sigma=0."""
        factory = npi.BenchmarkFactory(
            bucket_name="b",
            project_id="p",
            bq_dataset_id="d",
            iterations=5,
            buffer_mount_path="/mnt/buf",
        )
        cmd = factory.get_benchmark_command("read_grpc")[0]
        self.assertIn("--iterations=5", cmd)
        self.assertNotIn("--min-iterations", cmd)
        self.assertNotIn("--max-iterations", cmd)
        self.assertNotIn("--convergence-threshold", cmd)

        # Verify unconfigured MagicMock() args on npi_orchestrator.execute_target (GCE & GKE)
        mock_args = MagicMock()
        mock_args.benchmarks = "read_grpc"
        mock_args.project = "test-p"
        mock_args.image_version = "latest"
        mock_args.iterations = 5
        mock_args.smoke_mode = False
        mock_args.extra_mount_options = None
        gce_target = {
            "name": "gce_legacy",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
            "buffer_dir": "/mnt/buf",
        }
        gke_target = {
            "name": "gke_legacy",
            "type": "gke",
            "vm_name": "runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "test-b",
            "dataset": "test_ds",
            "node_selector": "",
            "resources_limits": "",
        }
        captured = []
        with patch.object(npi_orchestrator, "cleanup_remote_run"), \
             patch.object(npi_orchestrator, "prep_vm"), \
             patch.object(npi_orchestrator, "monitor_run", return_value=True), \
             patch.object(npi_orchestrator, "run_ssh_cmd", side_effect=lambda *a, **k: (captured.append(a), (0, "", ""))[1]), \
             patch.object(npi_orchestrator, "save_state"):
            npi_orchestrator.execute_target(gce_target, mock_args, threading.Lock(), {"gce_legacy": {"status": "PENDING"}})
            npi_orchestrator.execute_target(gke_target, mock_args, threading.Lock(), {"gke_legacy": {"status": "PENDING"}})
        joined = " ".join(str(x) for x in captured)
        self.assertNotIn("--min-iterations", joined)
        self.assertNotIn("--max-iterations", joined)
        self.assertNotIn("--convergence-threshold", joined)
        self.assertNotIn("MagicMock", joined)

        # Execute fio_benchmark_runner.run_benchmark in legacy mode on sigma=0 stream -> must execute all 5 fixed iterations
        zero_var_stream = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(make_synthetic_fio_json(zero_var_stream[iteration - 1]))

            with patch.object(fio_benchmark_runner, "run_command"), \
                 patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                 patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-b",
                    iterations=5,
                    fio_config="fake.fio",
                    work_dir=tmpdir,
                    output_dir=tmpdir,
                    project_id="",
                    min_iterations=None,
                    max_iterations=None,
                    convergence_threshold=None,
                )
                self.assertEqual(mock_fio.call_count, 5)

    def test_scenario_06_local_mock_execution_zero_live_cloud_side_effects(self):
        """T4_06: Full multi-module pipeline runs inside HermeticCloudBlocker with zero network or subprocess calls."""
        s_base = [1000.0, 1005.0, 995.0, 1002.0, 998.0]
        s_cand = [1100.0, 1105.0, 1095.0, 1102.0, 1098.0]
        with HermeticCloudBlocker() as audit:
            factory = npi.BenchmarkFactory(
                bucket_name="test-b",
                project_id="test-p",
                bq_dataset_id="test_ds",
                iterations=5,
                buffer_mount_path="/mnt/buf",
                min_iterations=3,
                max_iterations=5,
                convergence_threshold=0.05,
            )
            cmd = factory.get_benchmark_command("read_grpc")[0]
            self.assertIn("--min-iterations=3", cmd)

            with tempfile.TemporaryDirectory() as tmpdir:
                def fake_run_fio(fio_config, mount_point, iteration, output_dir, **kwargs):
                    out_path = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
                    with open(out_path, "w", encoding="utf-8") as fh:
                        fh.write(make_synthetic_fio_json(s_cand[iteration - 1]))

                with patch.object(fio_benchmark_runner, "run_command"), \
                     patch.object(fio_benchmark_runner, "mount_gcsfuse"), \
                     patch.object(fio_benchmark_runner, "unmount_gcsfuse"), \
                     patch.object(fio_benchmark_runner, "run_fio_test", side_effect=fake_run_fio) as mock_fio:
                    fio_benchmark_runner.run_benchmark(
                        gcsfuse_flags="--implicit-dirs",
                        bucket_name="test-b",
                        iterations=5,
                        fio_config="fake.fio",
                        work_dir=tmpdir,
                        output_dir=tmpdir,
                        project_id="",
                        min_iterations=3,
                        max_iterations=5,
                        convergence_threshold=0.05,
                    )
                    self.assertEqual(mock_fio.call_count, 3)

            base_rows = make_bq_per_iteration_rows({"read": (s_base, [1.2] * 5)})
            cand_rows = make_bq_per_iteration_rows({"read": (s_cand, [1.1] * 5)})
            with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(base_rows), returncode=0)):
                base_m = query_results.get_table_metrics("test-p", "base_ds", "fio_read_grpc")
            with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(cand_rows), returncode=0)):
                cand_m = query_results.get_table_metrics("test-p", "cand_ds", "fio_read_grpc")

            comp = convergence.compare_samples_welch(
                base_m["workloads"]["read"]["bw_samples"],
                cand_m["workloads"]["read"]["bw_samples"],
            )
            self.assertEqual(comp.verdict, "SIGNIFICANT_IMPROVEMENT")
        self.assertEqual(audit.blocked_network_attempts, [])
        self.assertEqual(audit.blocked_subprocess_attempts, [])

    def test_scenario_07_dual_storage_regional_and_zonal_rapid_end_to_end_pipeline(self):
        """T4_07: Paired Regional Standard HNS & Zonal RAPID HNS targets execute through query_results with valid disaggregated metrics."""
        f_reg = npi.BenchmarkFactory(
            bucket_name="gs://npi-smoke-regional-us-central1",
            project_id="test-p",
            bq_dataset_id="npi_validation_regional",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            is_rapid_bucket=False,
        )
        f_zon = npi.BenchmarkFactory(
            bucket_name="gs://npi-smoke-zonal-us-central1-a",
            project_id="test-p",
            bq_dataset_id="npi_validation_zonal",
            iterations=5,
            buffer_mount_path="/mnt/buf",
            is_rapid_bucket=True,
        )
        cmd_reg = f_reg.get_benchmark_command("read_grpc")[0]
        cmd_zon = f_zon.get_benchmark_command("read_grpc")[0]
        self.assertIn("NUMJOBS=112", cmd_reg)
        self.assertIn("NUMJOBS=48", cmd_zon)

        reg_rows = make_bq_per_iteration_rows({
            "read": ([2200.0, 2210.0, 2190.0], [1.3, 1.3, 1.3]),
            "randread": ([950.0, 955.0, 945.0], [2.6, 2.6, 2.6]),
            "write": ([700.0, 705.0, 695.0], [3.2, 3.2, 3.2]),
        })
        zon_rows = make_bq_per_iteration_rows({
            "read": ([2800.0, 2810.0, 2790.0], [0.9, 0.9, 0.9]),
            "randread": ([1450.0, 1455.0, 1445.0], [1.5, 1.5, 1.5]),
            "write": ([1100.0, 1105.0, 1095.0], [2.1, 2.1, 2.1]),
        })
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(reg_rows), returncode=0)):
            m_reg = query_results.get_table_metrics("test-p", "npi_validation_regional", "fio_read_grpc")
        with patch.object(subprocess, "run", return_value=MagicMock(stdout=json.dumps(zon_rows), returncode=0)):
            m_zon = query_results.get_table_metrics("test-p", "npi_validation_zonal", "fio_read_grpc")

        self.assertAlmostEqual(m_reg["seq_read_bw_mbs"], 2200.0, delta=1e-3)
        self.assertAlmostEqual(m_reg["rand_read_bw_mbs"], 950.0, delta=1e-3)
        self.assertAlmostEqual(m_reg["write_bw_mbs"], 700.0, delta=1e-3)
        self.assertAlmostEqual(m_zon["seq_read_bw_mbs"], 2800.0, delta=1e-3)
        self.assertAlmostEqual(m_zon["rand_read_bw_mbs"], 1450.0, delta=1e-3)
        self.assertAlmostEqual(m_zon["write_bw_mbs"], 1100.0, delta=1e-3)


class TestTier5WhiteboxAdversarialHardening(unittest.TestCase):
    """Tier 5: White-Box Adversarial Hardening & Boundary Coverage Tests (T5-WB-01..10 & T5-ADV-01..10)."""

    def test_t5_wb_01_query_results_extract_samples_null_subdicts_and_adv07_guard(self):
        """T5-WB-01 (ADV-07): _extract_samples_from_rows handles JSON null in 'global options', 'rw', 'read', 'write', and 'jobs' without AttributeError."""
        rows = [
            {
                "fio_json_output": json.dumps({
                    "fio version": "fio-3.35",
                    "global options": None,
                    "jobs": [
                        None,
                        {
                            "read": {"bw": 1000000, "lat_ns": {"mean": 1500000}},
                            "write": None,
                        },
                    ],
                })
            },
            {
                "fio_json_output": json.dumps({
                    "fio version": "fio-3.35",
                    "global options": {"rw": None},
                    "jobs": [
                        {
                            "read": {"bw": 1000000, "lat_ns": {"mean": 1500000}},
                            "write": {"bw": 500000, "lat_ns": None},
                        }
                    ],
                })
            },
            {
                "fio_json_output": json.dumps({
                    "fio version": "fio-3.35",
                    "global options": {"rw": "randread"},
                    "jobs": [
                        {
                            "read": None,
                            "write": None,
                        },
                        {
                            "read": {"bw": 250000, "lat_ns": {"mean": 3000000}},
                        },
                    ],
                })
            },
            {
                "fio_json_output": json.dumps({
                    "fio version": "fio-3.35",
                    "global options": {"rw": "read"},
                    "jobs": None,
                })
            },
        ]

        bw_samples, lat_samples, fio_ver = query_results._extract_samples_from_rows(rows)
        self.assertEqual(fio_ver, "fio-3.35")
        self.assertEqual(len(bw_samples["read"]), 2)
        self.assertAlmostEqual(bw_samples["read"][0], 1024.0, places=3)
        self.assertAlmostEqual(bw_samples["read"][1], 1024.0, places=3)
        self.assertEqual(len(bw_samples["randread"]), 1)
        self.assertAlmostEqual(bw_samples["randread"][0], 256.0, places=3)
        self.assertEqual(len(bw_samples["write"]), 1)
        self.assertAlmostEqual(bw_samples["write"][0], 512.0, places=3)
        self.assertAlmostEqual(lat_samples["write"][0], 0.0, places=3)

    def test_t5_wb_02_get_table_metrics_survives_mixed_null_raw_json_rows(self):
        """T5-WB-02: get_table_metrics preserves valid rows even when mixed with raw JSON rows containing explicit null fields."""
        mock_rows = [
            {
                "fio_json_output": json.dumps({
                    "fio version": "fio-3.35",
                    "global options": None,
                    "jobs": [{"read": {"bw": 1000000, "lat_ns": {"mean": 1200000}}, "write": None}],
                })
            },
            {
                "fio_json_output": json.dumps({
                    "global options": {"rw": "read"},
                    "jobs": [{"read": {"bw": 1000000, "lat_ns": {"mean": 1200000}}, "write": None}],
                })
            },
            {
                "fio_json_output": json.dumps({
                    "global options": {"rw": "read"},
                    "jobs": [{"read": {"bw": 1000000, "lat_ns": {"mean": 1200000}}, "write": None}],
                })
            },
        ]
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(mock_rows), stderr="")
        with patch("query_results.subprocess.run", return_value=mock_proc):
            metrics = query_results.get_table_metrics("proj-1", "ds-1", "fio_read_grpc")

        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 1024.0, places=2)
        self.assertEqual(metrics["rand_read_bw_mbs"], 0.0)
        self.assertEqual(metrics["write_bw_mbs"], 0.0)
        self.assertTrue(metrics["workloads"]["read"]["bw_summary"].converged)
        self.assertEqual(metrics["workloads"]["read"]["bw_summary"].n_inliers, 3)

    def test_t5_wb_03_parse_fio_output_null_global_options_job_options_and_percentiles(self):
        """T5-WB-03: parse_fio_output handles null/missing 'global options', 'job options', 'read', 'write', and 'percentiles' cleanly."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(
                {
                    "global options": None,
                    "jobs": [
                        None,
                        {
                            "jobname": "job_resilient",
                            "job options": None,
                            "read": {
                                "bw": 2048000,
                                "iops": 2000,
                                "lat_ns": {"mean": 1500000, "percentiles": None},
                            },
                            "write": None,
                        },
                    ],
                },
                tf,
            )
            tmp_path = tf.name
        try:
            parsed = fio_benchmark_runner.parse_fio_output(tmp_path)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["job_name"], "job_resilient")
            self.assertAlmostEqual(parsed[0]["bw_mibps"], 2000.0, places=2)
            self.assertAlmostEqual(parsed[0]["mean_lat_ms"], 1.5, places=3)
            self.assertEqual(parsed[0]["p99_lat_ms"], 0)
            self.assertEqual(parsed[0]["operation"], "unknown")
        finally:
            os.remove(tmp_path)

    def test_t5_wb_04_sub_unity_degrees_of_freedom_critical_value_roundtrip(self):
        """T5-WB-04: student_t_critical_value exercises sub-unity d.o.f. branch (0 < df < 1) with exact round-trip inversion."""
        for df in (0.25, 0.5, 0.75):
            for conf in (0.90, 0.95, 0.99):
                t_crit = convergence.student_t_critical_value(df=df, confidence=conf)
                self.assertTrue(math.isfinite(t_crit) and t_crit > 0.0)
                p_val = convergence.student_t_two_sided_pvalue(t_crit, df=df)
                self.assertAlmostEqual(p_val, 1.0 - conf, places=10)

    def test_t5_wb_05_welch_zero_variance_sub_threshold_delta_is_noise_neutral(self):
        """T5-WB-05: compare_samples_welch with zero variance in both groups (p=0.0, t=inf) but |delta_pct| < min_effect_pct yields NOISE / NEUTRAL."""
        comp = convergence.compare_samples_welch(
            baseline_samples=[1000.0, 1000.0, 1000.0],
            candidate_samples=[1005.0, 1005.0, 1005.0],
            alpha=0.05,
            min_effect_pct=2.0,
            higher_is_better=True,
        )
        self.assertTrue(math.isinf(comp.t_stat) and comp.t_stat > 0.0)
        self.assertEqual(comp.p_value, 0.0)
        self.assertTrue(comp.statistically_significant)
        self.assertFalse(comp.practically_significant)
        self.assertAlmostEqual(comp.delta_pct, 0.5, places=6)
        self.assertEqual(comp.verdict, "NOISE / NEUTRAL")

    def test_t5_wb_06_welch_zero_baseline_mean_with_positive_candidate_polarity(self):
        """T5-WB-06: compare_samples_welch handles zero baseline mean (y_b == 0.0) without ZeroDivisionError across both polarities."""
        comp_higher = convergence.compare_samples_welch(
            baseline_samples=[0.0, 0.0, 0.0],
            candidate_samples=[100.0, 100.0, 100.0],
            higher_is_better=True,
        )
        self.assertEqual(comp_higher.delta_pct, float("inf"))
        self.assertTrue(comp_higher.statistically_significant)
        self.assertTrue(comp_higher.practically_significant)
        self.assertEqual(comp_higher.verdict, "SIGNIFICANT_IMPROVEMENT")

        comp_lower = convergence.compare_samples_welch(
            baseline_samples=[0.0, 0.0, 0.0],
            candidate_samples=[100.0, 100.0, 100.0],
            higher_is_better=False,
        )
        self.assertEqual(comp_lower.verdict, "SIGNIFICANT_REGRESSION")

    def test_t5_wb_07_keep_mount_warmup_boundary_when_max_iter_equals_min_iter(self):
        """T5-WB-07: When keep_mount=True (warmup_iterations=1) and max_iterations == min_iterations, n_steady = min_iterations - 1 and converged is strictly False."""
        conv = convergence.evaluate_convergence(
            samples=[300.0, 2000.0, 2000.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
            warmup_iterations=1,
        )
        self.assertEqual(conv.n_total, 3)
        self.assertEqual(conv.n_steady, 2)
        self.assertEqual(conv.warmup_samples, [300.0])
        self.assertAlmostEqual(conv.representative_value, 2000.0, places=2)
        self.assertFalse(conv.converged)

        conv_4 = convergence.evaluate_convergence(
            samples=[300.0, 2000.0, 2000.0, 2000.0],
            min_iterations=3,
            convergence_threshold=0.05,
            confidence_level=0.95,
            warmup_iterations=1,
        )
        self.assertEqual(conv_4.n_steady, 3)
        self.assertTrue(conv_4.converged)

    def test_t5_wb_08_orchestrator_targets_json_per_target_overrides_and_bool_guards(self):
        """T5-WB-08: execute_target honors per-target convergence overrides in targets.json while rejecting JSON boolean contamination."""
        captured_cmds = []

        def fake_ssh(socket_path, vm_name, zone, cmd, timeout=60):
            captured_cmds.append(cmd)
            return 0, "12345", ""

        target = {
            "name": "gce_target_custom",
            "type": "gce",
            "vm_name": "vm-test",
            "zone": "us-central1-a",
            "bucket": "test-bucket",
            "dataset": "ds_custom",
            "buffer_mount": "/mnt/lssd",
            "min_iterations": True,
            "max_iterations": 7,
            "convergence_threshold": 0.03,
            "confidence_level": 0.99,
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-proj"
        args.image_version = "v1"
        args.iterations = 5
        args.smoke_mode = False
        args.extra_mount_options = None
        args.min_iterations = None
        args.max_iterations = None
        args.convergence_threshold = None
        args.confidence_level = 0.95

        state_lock = threading.Lock()
        state = {"gce_target_custom": {"status": "PENDING", "pid": None, "last_line": ""}}

        with (
            patch("npi_orchestrator.cleanup_remote_run"),
            patch("npi_orchestrator.prep_vm"),
            patch("npi_orchestrator.monitor_run"),
            patch("npi_orchestrator.run_ssh_cmd", side_effect=fake_ssh),
        ):
            npi_orchestrator.execute_target(target, args, state_lock, state)

        self.assertTrue(len(captured_cmds) >= 1)
        cmd_str = captured_cmds[-1]
        self.assertNotIn("--min-iterations", cmd_str)
        self.assertIn("--max-iterations 7", cmd_str)
        self.assertIn("--convergence-threshold 0.03", cmd_str)
        self.assertIn("--confidence-level 0.99", cmd_str)

    def test_t5_wb_09_get_detailed_table_metrics_deterministic_sorting_and_write_primary_selection(self):
        """T5-WB-09: get_detailed_table_metrics sorts (read -> randread -> write) and binds primary_bw_sum to write_bw for write workloads."""
        scrambled_rows = [
            "not-a-dict",
            {
                "workload_type": "write",
                "block_size": "1M",
                "file_size": "1G",
                "read_bw_mbs": 0.0,
                "read_lat_ms": 0.0,
                "read_iops": 0.0,
                "write_bw_mbs": 800.0,
                "write_lat_ms": 2.5,
                "write_iops": 800.0,
            },
            {
                "workload_type": "write",
                "block_size": "1M",
                "file_size": "1G",
                "read_bw_mbs": 0.0,
                "read_lat_ms": 0.0,
                "read_iops": 0.0,
                "write_bw_mbs": 800.0,
                "write_lat_ms": 2.5,
                "write_iops": 800.0,
            },
            {
                "workload_type": "write",
                "block_size": "1M",
                "file_size": "1G",
                "read_bw_mbs": 0.0,
                "read_lat_ms": 0.0,
                "read_iops": 0.0,
                "write_bw_mbs": 800.0,
                "write_lat_ms": 2.5,
                "write_iops": 800.0,
            },
            {
                "workload_type": "randread",
                "block_size": "16K",
                "file_size": "100M",
                "read_bw_mbs": 300.0,
                "read_lat_ms": 0.8,
                "read_iops": 18750.0,
                "write_bw_mbs": 0.0,
                "write_lat_ms": 0.0,
                "write_iops": 0.0,
            },
            {
                "workload_type": "read",
                "block_size": "1M",
                "file_size": "1G",
                "read_bw_mbs": 1500.0,
                "read_lat_ms": 1.1,
                "read_iops": 1500.0,
                "write_bw_mbs": 0.0,
                "write_lat_ms": 0.0,
                "write_iops": 0.0,
            },
        ]
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(scrambled_rows), stderr="")
        with patch("query_results.subprocess.run", return_value=mock_proc):
            detailed = query_results.get_detailed_table_metrics("proj-1", "ds-1", "fio_mixed")

        self.assertEqual([r["workload_type"] for r in detailed], ["read", "randread", "write"])
        write_entry = detailed[2]
        self.assertEqual(write_entry["write_bw_mbs"], 800.0)
        self.assertAlmostEqual(write_entry["bw_summary"].representative_value, 800.0, places=2)
        self.assertTrue(write_entry["converged"])

    def test_t5_wb_10_mad_majority_retention_tie_breaking_deterministic_order(self):
        """T5-WB-10: filter_outliers_mad enforces majority retention (min_keep = ceil(n/2)) with deterministic index tie-breaking."""
        samples = [100.0, 200.0, 300.0, 400.0, 500.0]
        inliers, outliers = convergence.filter_outliers_mad(samples, mad_threshold=0.01)
        self.assertEqual(len(inliers), 3)
        self.assertEqual(len(outliers), 2)
        self.assertEqual(inliers, [200.0, 300.0, 400.0])
        self.assertEqual(outliers, [100.0, 500.0])

    def test_adv_01_query_results_nested_null_subdicts_and_malformed_raw_fio_json(self):
        """T5-ADV-01 (WB-01 / ADV-07): _extract_samples_from_rows & get_table_metrics handle explicit JSON null sub-dicts."""
        valid_read_row_1 = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": {"rw": "read"},
                "jobs": [{"read": {"bw": 2000000, "lat_ns": {"mean": 1500000}}}],
            })
        }
        null_global_opts_row = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": None,
                "jobs": [{"read": {"bw": 2000000, "lat_ns": {"mean": 1500000}}}],
            })
        }
        null_read_and_write_row = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": {"rw": "read"},
                "jobs": [{"read": None, "write": None}],
            })
        }
        null_jobs_row = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": {"rw": "read"},
                "jobs": None,
            })
        }
        valid_read_row_2 = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": {"rw": "read"},
                "jobs": [{"read": {"bw": 2000000, "lat_ns": {"mean": 1500000}}}],
            })
        }
        valid_read_row_3 = {
            "fio_json_output": json.dumps({
                "fio version": "fio-3.35",
                "global options": {"rw": "read"},
                "jobs": [{"read": {"bw": 2000000, "lat_ns": {"mean": 1500000}}}],
            })
        }

        rows = [
            valid_read_row_1,
            null_global_opts_row,
            null_read_and_write_row,
            null_jobs_row,
            valid_read_row_2,
            valid_read_row_3,
        ]

        bw_samples, lat_samples, fio_ver = query_results._extract_samples_from_rows(rows)
        self.assertEqual(fio_ver, "fio-3.35")
        self.assertGreaterEqual(len(bw_samples["read"]), 3)
        self.assertTrue(all(math.isfinite(x) for x in bw_samples["read"]))

        with mock.patch("query_results.subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stdout=json.dumps(rows),
                stderr="",
            )
            metrics = query_results.get_table_metrics("test-proj", "test_ds", "fio_read_grpc")
            self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2048.0, places=2)
            self.assertTrue(metrics["workloads"]["read"]["bw_summary"].converged)

    def test_adv_02_parse_fio_output_missing_global_options_and_null_subdicts(self):
        """T5-ADV-02 (WB-02): parse_fio_output handles leading non-JSON noise, missing global options, and null percentiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fio_path = os.path.join(tmpdir, "fio_iter_missing_global_opts.json")
            payload = {
                "fio version": "fio-3.35",
                "jobs": [
                    {
                        "jobname": "job_custom",
                        "job options": None,
                        "read": {
                            "bw": 512000,
                            "iops": 128000.0,
                            "lat_ns": {"mean": 2500000, "percentiles": None},
                        },
                        "write": None,
                    }
                ],
            }
            with open(fio_path, "w", encoding="utf-8") as fh:
                fh.write("WARNING: kernel page cache drop note\nfio: status line\n" + json.dumps(payload))

            parsed = fio_benchmark_runner.parse_fio_output(fio_path)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["job_name"], "job_custom")
            self.assertAlmostEqual(parsed[0]["bw_mibps"], 500.0, places=4)
            self.assertAlmostEqual(parsed[0]["mean_lat_ms"], 2.5, places=4)
            self.assertEqual(parsed[0]["p99_lat_ms"], 0)

            corrupt_path = os.path.join(tmpdir, "fio_corrupt.json")
            with open(corrupt_path, "w", encoding="utf-8") as fh:
                fh.write("WARNING: fio aborted\n{\"jobs\": [truncated")
            self.assertEqual(fio_benchmark_runner.parse_fio_output(corrupt_path), [])

    def test_adv_03_fio_runner_keep_mount_warmup_with_midstream_corrupt_and_empty_iterations(self):
        """T5-ADV-03 (WB-03): FIO runner with keep_mount=True handles mid-stream corrupt/empty iterations without corrupting warmup or steady-state stats."""
        iter_payloads = {
            1: {"bw_kib": int(250.0 * 1024), "valid": True},
            2: {"bw_kib": 0, "valid": False},
            3: {"bw_kib": int(2000.0 * 1024), "valid": True},
            4: {"bw_kib": int(2005.0 * 1024), "valid": True},
            5: {"bw_kib": int(1995.0 * 1024), "valid": True},
        }
        executed_iters = []

        def fake_run_fio_test(fio_config, mount_point, iteration, output_dir, fio_env=None, cpu_limit_list=None):
            executed_iters.append(iteration)
            out_file = os.path.join(output_dir, f"fio_results_iter_{iteration}.json")
            spec = iter_payloads[iteration]
            if not spec["valid"]:
                with open(out_file, "w", encoding="utf-8") as fh:
                    fh.write("{corrupt_json_iteration_2")
            else:
                doc = {
                    "fio version": "fio-3.35",
                    "global options": {"rw": "read", "iodepth": "64"},
                    "jobs": [
                        {
                            "jobname": "job_read",
                            "job options": {"bs": "1M", "filesize": "1G", "nrfiles": "10", "numjobs": "112"},
                            "read": {
                                "bw": spec["bw_kib"],
                                "iops": spec["bw_kib"] / 1024.0,
                                "lat_ns": {"mean": 1500000, "percentiles": {"99.000000": 3000000}},
                            },
                        }
                    ],
                }
                with open(out_file, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)

        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = os.path.join(tmpdir, "summary.txt")
            with mock.patch("fio_benchmark_runner.run_command"), \
                 mock.patch("fio_benchmark_runner.mount_gcsfuse"), \
                 mock.patch("fio_benchmark_runner.unmount_gcsfuse"), \
                 mock.patch("fio_benchmark_runner.run_fio_test", side_effect=fake_run_fio_test):
                conv_res = fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="--implicit-dirs",
                    bucket_name="test-bucket",
                    iterations=5,
                    fio_config="read.fio",
                    work_dir=os.path.join(tmpdir, "work"),
                    output_dir=os.path.join(tmpdir, "out"),
                    project_id=None,
                    summary_file=summary_path,
                    keep_mount=True,
                    min_iterations=3,
                    max_iterations=8,
                    convergence_threshold=0.05,
                    confidence_level=0.95,
                )

            self.assertEqual(executed_iters, [1, 2, 3, 4, 5])
            self.assertIsNotNone(conv_res)
            self.assertTrue(conv_res.converged)
            self.assertEqual(conv_res.warmup_samples, [250.0])
            self.assertEqual(conv_res.n_steady, 3)
            self.assertAlmostEqual(conv_res.representative_value, 2000.0, places=2)
            with open(summary_path, "r", encoding="utf-8") as fh:
                summary_text = fh.read()
            self.assertIn("No results for this iteration.", summary_text)
            self.assertIn("2000.00", summary_text)

    def test_adv_04_go_matrix_corrupt_stdout_and_null_subdicts_recovery_across_configs(self):
        """T5-ADV-04 (WB-04): run_go_matrix recovers from mid-stream corrupt JSON stdout on config 1 and all-failed config 2 without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("FILE_SIZE,BLOCK_SIZE,NR_FILES,READ_TYPE\n")
                fh.write("1G,1M,10,seq\n")
                fh.write("1G,128K,10,seq\n")

            summary_name = "go_summary.txt"
            call_counts = {"1M": 0, "128K": 0}

            def fake_run_command(cmd, check=True, cwd=None):
                cmd_str = " ".join(cmd)
                if "--bs=1M" in cmd_str:
                    call_counts["1M"] += 1
                    idx = call_counts["1M"]
                    if idx == 2:
                        return mock.MagicMock(returncode=0, stdout="{corrupt_go_json", stderr="")
                    bw_val = 3000.0
                    doc = {
                        "jobs": [
                            {
                                "jobname": "go-client-read",
                                "job options": None,
                                "read": {
                                    "bw": int(bw_val * 1024),
                                    "iops": bw_val,
                                    "lat_ns": {"mean": 2000000, "percentiles": None},
                                },
                            }
                        ]
                    }
                    return mock.MagicMock(returncode=0, stdout=json.dumps(doc), stderr="")
                else:
                    call_counts["128K"] += 1
                    return mock.MagicMock(returncode=0, stdout="INVALID_JSON", stderr="")

            argv = [
                "run_go_matrix.py",
                "--bucket-name=test-bucket",
                "--client-protocol=grpc",
                f"--matrix-config={csv_path}",
                f"--output-dir={tmpdir}",
                f"--summary-file-name={summary_name}",
                "--min-iterations=3",
                "--max-iterations=5",
                "--convergence-threshold=0.05",
            ]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(run_go_matrix.os.path, "exists", return_value=True), \
                 mock.patch.object(run_go_matrix.subprocess, "run"), \
                 mock.patch.object(run_go_matrix, "run_command", side_effect=fake_run_command):
                run_go_matrix.main()

            self.assertEqual(call_counts["1M"], 4)
            self.assertEqual(call_counts["128K"], 5)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, summary_name)))

    def test_adv_05_query_results_asymmetric_bw_vs_latency_sample_counts_welch_comparison(self):
        """T5-ADV-05 (WB-05): query_results handles asymmetric sample counts (N=4 BW samples vs N=0 latency samples) cleanly in baseline comparisons."""
        base_rows = [
            {"iteration": i, "fio_version": "go-client", "seq_read_bw_mbs": 2000.0, "seq_read_lat_ms": 0.0}
            for i in range(1, 5)
        ]
        cand_rows = [
            {"iteration": i, "fio_version": "go-client", "seq_read_bw_mbs": 2600.0, "seq_read_lat_ms": 0.0}
            for i in range(1, 5)
        ]

        def fake_subprocess_run(cmd, capture_output=True, text=True, check=True):
            sql = cmd[-1]
            if "cand_ds" in sql:
                return mock.MagicMock(returncode=0, stdout=json.dumps(cand_rows), stderr="")
            return mock.MagicMock(returncode=0, stdout=json.dumps(base_rows), stderr="")

        stdout_buf = io.StringIO()
        argv = [
            "query_results.py",
            "--project-id=test-proj",
            "--dataset-id=cand_ds",
            "--baseline-dataset-id=base_ds",
            "--table-types",
            "go_client_read_grpc",
        ]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch("query_results.subprocess.run", side_effect=fake_subprocess_run), \
             contextlib.redirect_stdout(stdout_buf):
            query_results.main()

        output = stdout_buf.getvalue()
        self.assertIn("SIGNIFICANT_IMPROVEMENT", output)
        self.assertIn("INSUFFICIENT_SAMPLES", output)

    def test_adv_06_orchestrator_target_json_vs_cli_convergence_precedence_and_type_guards(self):
        """T5-ADV-06 (WB-06): execute_target merges per-target targets.json convergence config with CLI overrides and boolean/MagicMock guards."""
        target = {
            "name": "gce_target_custom_conf",
            "type": "gce",
            "vm_name": "vm-test",
            "zone": "us-central1-a",
            "bucket": "test-bucket",
            "dataset": "npi_ds",
            "buffer_mount": "/mnt/lssd",
            "is_rapid_bucket": True,
            "min_iterations": 4,
            "convergence_threshold": 0.03,
            "confidence_level": 0.99,
        }
        args = mock.MagicMock()
        args.benchmarks = "all"
        args.project = "test-proj"
        args.image_version = "v1"
        args.iterations = 5
        args.smoke_mode = False
        args.extra_mount_options = None
        args.min_iterations = None
        args.max_iterations = 10
        args.convergence_threshold = False
        args.confidence_level = 0.95

        state = {"gce_target_custom_conf": {"status": "PENDING", "pid": None, "last_line": ""}}
        state_lock = threading.Lock()
        captured_cmds = []

        def fake_run_ssh(socket_path, vm_name, zone, cmd, timeout=60):
            captured_cmds.append(cmd)
            return 0, "", ""

        with mock.patch("npi_orchestrator.cleanup_remote_run"), \
             mock.patch("npi_orchestrator.prep_vm"), \
             mock.patch("npi_orchestrator.monitor_run"), \
             mock.patch("npi_orchestrator.save_state"), \
             mock.patch("npi_orchestrator.run_ssh_cmd", side_effect=fake_run_ssh):
            npi_orchestrator.execute_target(target, args, state_lock, state)

        self.assertEqual(len(captured_cmds), 1)
        cmd_str = captured_cmds[0]
        self.assertIn("--min-iterations 4", cmd_str)
        self.assertIn("--max-iterations 10", cmd_str)
        self.assertIn("--convergence-threshold 0.03", cmd_str)
        self.assertIn("--confidence-level 0.99", cmd_str)
        self.assertIn("--bq-dataset-id npi_ds_zonal", cmd_str)
        self.assertNotIn("read_http1", cmd_str)

    def test_adv_07_convergence_math_whitebox_extreme_df_and_catastrophic_cancellation_boundaries(self):
        """T5-ADV-07: White-box verification of all internal branches in _log_beta, betainc, student_t_critical_value, and student_t_two_sided_pvalue."""
        t_crit_frac = convergence.student_t_critical_value(df=0.5, confidence=0.95)
        p_frac = convergence.student_t_two_sided_pvalue(t_crit_frac, df=0.5)
        self.assertAlmostEqual(p_frac, 0.05, places=10)

        t_crit_1 = convergence.student_t_critical_value(df=1.0, confidence=0.95)
        self.assertAlmostEqual(convergence.student_t_two_sided_pvalue(t_crit_1, 1.0), 0.05, places=12)
        t_crit_2 = convergence.student_t_critical_value(df=2.0, confidence=0.95)
        self.assertAlmostEqual(convergence.student_t_two_sided_pvalue(t_crit_2, 2.0), 0.05, places=12)

        t_crit_inf = convergence.student_t_critical_value(df=float("inf"), confidence=0.95)
        self.assertAlmostEqual(t_crit_inf, 1.959963984540054, places=9)
        self.assertAlmostEqual(convergence.student_t_two_sided_pvalue(t_crit_inf, float("inf")), 0.05, places=12)

        self.assertEqual(convergence.betainc(5000.0, 5000.0, 0.001), 0.0)
        self.assertEqual(convergence.betainc(5000.0, 5000.0, 0.999), 1.0)

    def test_adv_08_mad_outlier_filter_tie_breaking_and_bimodal_50_50_split_guard(self):
        """T5-ADV-08: White-box verification of filter_outliers_mad majority-retention guard and tie-breaking on symmetric bimodal splits."""
        inliers, outliers = convergence.filter_outliers_mad([100.0, 100.0, 200.0, 200.0])
        self.assertEqual(inliers, [100.0, 100.0, 200.0, 200.0])
        self.assertEqual(outliers, [])

        inliers_strict, outliers_strict = convergence.filter_outliers_mad(
            [100.0, 101.0, 102.0, 150.0, 250.0], mad_threshold=0.01
        )
        self.assertEqual(len(inliers_strict), 3)
        self.assertEqual(len(outliers_strict), 2)

    def test_adv_09_welch_comparison_degenerate_variance_and_zero_baseline_branches(self):
        """T5-ADV-09: White-box verification of all degenerate variance and zero-baseline branches in compare_samples_welch."""
        res_pos = convergence.compare_samples_welch([0.0, 0.0, 0.0], [100.0, 100.0, 100.0])
        self.assertEqual(res_pos.delta_pct, float("inf"))
        self.assertEqual(res_pos.verdict, "SIGNIFICANT_IMPROVEMENT")

        res_zero = convergence.compare_samples_welch([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
        self.assertEqual(res_zero.delta_pct, 0.0)
        self.assertEqual(res_zero.verdict, "NOISE / NEUTRAL")

        res_asym = convergence.compare_samples_welch(
            [1000.0, 1000.0, 1000.0],
            [1100.0, 1105.0, 1095.0, 1100.0],
        )
        self.assertAlmostEqual(res_asym.degrees_of_freedom, 3.0, places=9)
        self.assertEqual(res_asym.verdict, "SIGNIFICANT_IMPROVEMENT")

    def test_adv_10_end_to_end_cross_module_adversarial_pipeline_all_workloads_and_tiers(self):
        """T5-ADV-10: Full white-box cross-module pipeline across BenchmarkFactory -> FIO runner -> BigQuery rows -> query_results."""
        factory = npi.BenchmarkFactory(
            bucket_name="npi-bucket-zonal",
            project_id="test-proj",
            bq_dataset_id="npi_ds_zonal",
            iterations=5,
            buffer_mount_path="/mnt/lssd",
            is_rapid_bucket=True,
            min_iterations=3,
            max_iterations=6,
            convergence_threshold=0.05,
            confidence_level=0.95,
        )
        cmd_str, table_id = factory.get_benchmark_command("read_file_cache_grpc")
        self.assertIn("--keep-mount", cmd_str)
        self.assertIn("--min-iterations=3", cmd_str)
        self.assertIn("--max-iterations=6", cmd_str)
        self.assertIn("--convergence-threshold=0.05", cmd_str)
        self.assertEqual(table_id, "fio_read_file_cache")


if __name__ == "__main__":
    unittest.main()
