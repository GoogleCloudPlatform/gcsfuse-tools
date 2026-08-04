import os
import sys
import unittest
import tempfile
import csv

curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)

from threshold_analyzer import (
    build_stats_query,
    calculate_derived_stats,
    analyze_and_print_thresholds,
)


class TestThresholdAnalyzer(unittest.TestCase):
    def test_build_stats_query_periodic(self):
        workloads = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"}
        ]
        query = build_stats_query(
            workloads,
            days=14,
            commit="abc1234",
            source="periodic",
            project_id="test-project",
        )
        self.assertIn("periodic_benchmarks.kokoro_run_*", query)
        self.assertIn("commit = 'abc1234'", query)
        self.assertIn("io_type = 'read'", query)
        self.assertIn("MIN(bw) as min_bw", query)
        self.assertIn("VAR_SAMP(bw)", query)
        self.assertIn("STDDEV_SAMP(bw)", query)

    def test_build_stats_query_release(self):
        workloads = [
            {"io_type": "write", "file_size": "1g", "block_size": "1m", "num_jobs": "48", "config": "grpc", "direct": "0"}
        ]
        query = build_stats_query(
            workloads,
            release_version="3.9.0",
            source="release",
        )
        self.assertIn("gcsfuse_release_performance_metrics.v*", query)
        self.assertIn("release_version = '3.9.0'", query)
        self.assertIn("config = 'grpc'", query)

    def test_build_stats_query_all(self):
        workloads = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"}
        ]
        query = build_stats_query(workloads, days=30, source="all")
        self.assertIn("periodic_benchmarks.kokoro_run_*", query)
        self.assertIn("gcsfuse_release_performance_metrics.v*", query)
        self.assertIn("UNION ALL", query)

    def test_calculate_derived_stats(self):
        row = {
            "sample_count": 25,
            "mean_bw": 100.0,
            "min_bw": 88.0,      # -12% drop
            "max_bw": 110.0,     # +10% peak
            "stddev_bw": 5.0,    # CV = 5% -> 2*CV = 10%
            "variance_bw": 25.0,
        }
        stats = calculate_derived_stats(row, min_floor=5.0)
        self.assertEqual(stats["sample_count"], 25)
        self.assertEqual(stats["mean_bw"], 100.0)
        self.assertEqual(stats["min_bw"], 88.0)
        self.assertEqual(stats["max_bw"], 110.0)
        self.assertEqual(stats["stddev_bw"], 5.0)
        self.assertEqual(stats["variance_bw"], 25.0)
        self.assertAlmostEqual(stats["cv_pct"], 5.0)
        self.assertAlmostEqual(stats["min_diff_pct"], -12.0)
        self.assertAlmostEqual(stats["max_diff_pct"], 10.0)
        # Suggested threshold should be max(abs(-12.0), 2*5.0, 5.0) -> 12.0
        self.assertAlmostEqual(stats["suggested_threshold_pct"], 12.0)

    def test_calculate_derived_stats_min_floor(self):
        row = {
            "sample_count": 10,
            "mean_bw": 100.0,
            "min_bw": 99.0,      # -1% drop
            "max_bw": 101.0,     # +1% peak
            "stddev_bw": 1.0,    # CV = 1% -> 2*CV = 2%
            "variance_bw": 1.0,
        }
        stats = calculate_derived_stats(row, min_floor=6.5)
        # Since calculated variance/drop is < 6.5%, floor should take precedence
        self.assertAlmostEqual(stats["suggested_threshold_pct"], 6.5)

    def test_analyze_and_print_thresholds_with_csv_export(self):
        workloads = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"},
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"},
        ]
        data = [
            {
                "io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0",
                "sample_count": 30, "mean_bw": 200.0, "min_bw": 180.0, "max_bw": 220.0, "stddev_bw": 10.0, "variance_bw": 100.0
            },
            {
                "io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0",
                "sample_count": 30, "mean_bw": 100.0, "min_bw": 85.0, "max_bw": 110.0, "stddev_bw": 6.0, "variance_bw": 36.0
            },
        ]

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            overall_suggested = analyze_and_print_thresholds(
                data,
                workloads,
                min_floor=5.0,
                output_csv=tmp_path,
            )
            # For read: min drop is -10%, CV is 5% -> suggested 10%
            # For write: min drop is -15%, CV is 6% -> suggested 15%
            # Overall should be 15.0%
            self.assertAlmostEqual(overall_suggested, 15.0)

            # Verify exported CSV content
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, 'r', encoding='utf-8') as f:
                reader = list(csv.DictReader(f))
                self.assertEqual(len(reader), 2)
                self.assertEqual(reader[0]["workload"], "read_1m_1m_48_http1_0")
                self.assertEqual(reader[1]["workload"], "write_1m_1m_48_http1_0")
                self.assertEqual(float(reader[1]["suggested_threshold_pct"]), 15.0)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == '__main__':
    unittest.main()
