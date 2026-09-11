import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)

import perf_gating as pg
from perf_gating import (
    parse_version, 
    get_minor_baselines, 
    get_patch_baselines, 
    determine_baselines, 
    evaluate_performance,
    build_query,
    get_daily_avg_timestamp,
    load_workloads,
    get_sql_filter,
    get_workload_key,
)


class TestPerfGating(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("3.9.0"), (3, 9, 0))
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("invalid.string"), (0, 0, 0))

    def test_get_minor_baselines_full(self):
        curr_ver = (3, 9, 0)
        all_versions = [
            ((3, 7, 0), "3.7.0"),
            ((3, 8, 0), "3.8.0"),
            ((3, 8, 1), "3.8.1"),
            ((3, 8, 2), "3.8.2"),
            ((3, 9, 0), "3.9.0")
        ]
        baselines, ts_version = get_minor_baselines(curr_ver, all_versions)
        # Should include: prev minor (3.8.0), latest patch of prev minor (3.8.2), and the one before (3.7.0)
        self.assertListEqual(baselines, ["3.8.0", "3.8.2", "3.7.0"])
        self.assertEqual(ts_version, "3.8.0")

    def test_get_minor_baselines_missing_data(self):
        curr_ver = (3, 9, 0)
        all_versions = [
            ((3, 8, 0), "3.8.0"),
            ((3, 9, 0), "3.9.0")
        ]
        baselines, ts_version = get_minor_baselines(curr_ver, all_versions)
        # No patches exist, and only 1 previous minor
        self.assertListEqual(baselines, ["3.8.0"])
        self.assertEqual(ts_version, "3.8.0")

    def test_get_patch_baselines(self):
        curr_ver = (3, 9, 2)
        all_versions = [
            ((3, 9, 0), "3.9.0"),
            ((3, 9, 1), "3.9.1"),
            ((3, 9, 2), "3.9.2")
        ]
        baselines = get_patch_baselines(curr_ver, all_versions)
        # Should include last 2 patch releases
        self.assertListEqual(baselines, ["3.9.1", "3.9.0"])

    def test_get_patch_baselines_fallback(self):
        curr_ver = (3, 9, 1)
        all_versions = [
            ((3, 9, 0), "3.9.0"),
            ((3, 9, 1), "3.9.1")
        ]
        baselines = get_patch_baselines(curr_ver, all_versions)
        # Only 1 previous patch version available (which is the .0 fallback)
        self.assertListEqual(baselines, ["3.9.0"])

    def test_determine_baselines_major_release(self):
        curr_ver = (4, 0, 0)
        all_versions = [
            ((3, 9, 0), "3.9.0"),
            ((4, 0, 0), "4.0.0")
        ]
        baselines, ts_version, use_daily_avg = determine_baselines(curr_ver, all_versions)
        # Major releases should have NO baselines and NO daily avg
        self.assertListEqual(baselines, [])
        self.assertIsNone(ts_version)
        self.assertFalse(use_daily_avg)

    @patch.object(pg, 'bq_query')
    @patch.object(pg, 'datetime')
    def test_get_daily_avg_timestamp_capped(self, mock_datetime, mock_bq_query):
        # Mock current time to 2026-08-01
        mock_now = datetime(2026, 8, 1, 0, 0, 0)
        mock_datetime.utcnow.return_value = mock_now
        
        # Scenario: The release was 100 days ago (older than 60 day cap)
        old_release_date = "2026-04-01 12:00:00"
        mock_bq_query.return_value = [{"run_timestamp": old_release_date}]
        
        timestamp = get_daily_avg_timestamp("3.8.0", use_daily_avg=True)
        
        # 60 days before 2026-08-01 is 2026-06-02. 
        sixty_days_ago = (mock_now - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        
        # The timestamp should be aggressively capped to 60 days ago
        self.assertEqual(timestamp, sixty_days_ago)

    def test_load_workloads(self):
        workloads = load_workloads()
        self.assertEqual(len(workloads), 8)
        first_key = get_workload_key(workloads[0])
        self.assertEqual(first_key, "read_1m_1m_48_http1_0")

    def test_get_sql_filter(self):
        custom_workloads = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"},
            {"io_type": "write", "file_size": "1g", "block_size": "1m", "num_jobs": "48", "config": "grpc", "direct": "0"}
        ]
        sql_filter = get_sql_filter(custom_workloads)
        self.assertIn("io_type IN ('read', 'write')", sql_filter)
        self.assertIn("file_size IN ('1m', '1g')", sql_filter)
        self.assertIn("block_size = '1m'", sql_filter)
        self.assertIn("config IN ('http1', 'grpc')", sql_filter)

    def test_build_query_contains_filters(self):
        query = build_query("3.9.0", ["3.8.0", "3.7.0"], "2026-06-01 00:00:00", use_daily_avg=True)
        # Verify query structurally contains our strict constraints
        self.assertIn("io_type IN ('read', 'write')", query)
        self.assertIn("file_size IN ('1m', '1g')", query)
        self.assertIn("block_size = '1m'", query)
        self.assertIn("num_jobs = '48'", query)
        self.assertIn("config IN ('http1', 'grpc')", query)
        self.assertIn("direct = '0'", query)
        # Verify baselines are injected
        self.assertIn("'3.8.0','3.7.0'", query)

    def test_evaluate_performance_pass_speedup(self):
        data = [
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "current", "avg_bw": "120.0"},
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "3.8.0", "avg_bw": "100.0"},
        ]
        # Current is 120, which is +20% from baseline 100. It should PASS because we only fail on regressions.
        failed = evaluate_performance(data, ["3.8.0"], threshold=10.0)
        self.assertFalse(failed)

    def test_evaluate_performance_fail_regression(self):
        data = [
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "current", "avg_bw": "80.0"},
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "3.8.0", "avg_bw": "100.0"},
        ]
        # Current is 80, which is -20% from baseline 100. It should FAIL because -20% is worse than -10%.
        failed = evaluate_performance(data, ["3.8.0"], threshold=10.0)
        self.assertTrue(failed)

    def test_evaluate_performance_with_workloads_list(self):
        workloads = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"},
            {"io_type": "write", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0"},
        ]
        data = [
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "current", "avg_bw": "105.0"},
            {"io_type": "read", "file_size": "1m", "block_size": "1m", "num_jobs": "48", "config": "http1", "direct": "0", "source": "3.8.0", "avg_bw": "100.0"},
        ]
        # write_1m_1m_48_http1_0 has no data in 'current', should gracefully report N/A and not fail gating
        failed = evaluate_performance(data, ["3.8.0"], threshold=10.0, workloads=workloads)
        self.assertFalse(failed)


if __name__ == '__main__':
    unittest.main()
