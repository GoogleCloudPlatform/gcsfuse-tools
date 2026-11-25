# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for result_aggregator.py."""

import json
import os
import tempfile
import unittest

from helpers import result_aggregator


class ResultAggregatorTest(unittest.TestCase):

    def test_prefers_lat_ns_over_clat_ns_and_converts_ns_to_ms(self):
        job = {
            "read": {
                "bw": 9737211,
                "bw_bytes": 9970904923,
                "slat_ns": {"min": 1_000_000, "max": 9_000_000, "mean": 4_820_442.0, "stddev": 500_000.0},
                "clat_ns": {
                    "min": 500,
                    "max": 5000,
                    "mean": 1304.7,
                    "stddev": 200.0,
                    "percentile": {
                        "50.000000": 1200,
                        "90.000000": 2000,
                        "99.000000": 4000,
                    },
                },
                "lat_ns": {
                    "min": 1_001_000,
                    "max": 9_005_000,
                    "mean": 4_821_747.0,
                    "stddev": 500_200.0,
                    "percentile": {
                        "50.000000": 4_500_000,
                        "90.000000": 7_000_000,
                        "99.000000": 8_500_000,
                    },
                },
            }
        }

        metrics = result_aggregator._extract_latency_metrics(job, "read")

        self.assertIsNotNone(metrics)
        self.assertAlmostEqual(metrics["bw_mbps"], 9970.904923, places=6)
        self.assertAlmostEqual(metrics["min"], 1.001, places=6)
        self.assertAlmostEqual(metrics["max"], 9.005, places=6)
        self.assertAlmostEqual(metrics["mean"], 4.821747, places=6)
        self.assertAlmostEqual(metrics["stddev"], 0.5002, places=6)
        self.assertAlmostEqual(metrics["p50"], 4.5, places=6)
        self.assertAlmostEqual(metrics["p90"], 7.0, places=6)
        self.assertAlmostEqual(metrics["p99"], 8.5, places=6)

    def test_clat_ns_fallback_still_divides_nanoseconds_by_1e6(self):
        job = {
            "read": {
                "bw": 1000,
                "clat_ns": {
                    "min": 10_000_000,
                    "max": 100_000_000,
                    "mean": 66_196_000.0,
                    "stddev": 5_000_000.0,
                    "percentile": {"50.000000": 60_000_000},
                },
            }
        }

        metrics = result_aggregator._extract_latency_metrics(job, "read")

        self.assertIsNotNone(metrics)
        # Fallback bw (KiB/s) -> decimal MB/s: 1000 * 1024 / 1e6 = 1.024 MB/s
        self.assertAlmostEqual(metrics["bw_mbps"], 1.024, places=6)
        self.assertAlmostEqual(metrics["mean"], 66.196, places=6)
        self.assertAlmostEqual(metrics["p50"], 60.0, places=6)

    def test_lat_ns_without_percentiles_does_not_mix_clat_percentiles(self):
        job = {
            "read": {
                "bw_bytes": 1_000_000,
                "clat_ns": {
                    "mean": 2500.0,
                    "percentile": {"50.000000": 2400},
                },
                "lat_ns": {
                    "min": 50_000_000,
                    "max": 90_000_000,
                    "mean": 70_000_000.0,
                    "stddev": 5_000_000.0,
                },
            }
        }

        metrics = result_aggregator._extract_latency_metrics(job, "read")

        self.assertAlmostEqual(metrics["mean"], 70.0, places=6)
        self.assertNotIn("p50", metrics)
        self.assertNotIn("p90", metrics)
        self.assertNotIn("p99", metrics)

    def test_returns_none_for_zero_bandwidth_action(self):
        job = {"read": {"bw": 0, "bw_bytes": 0, "lat_ns": {"mean": 0}}}
        self.assertIsNone(result_aggregator._extract_latency_metrics(job, "read"))
        self.assertIsNone(result_aggregator._extract_latency_metrics(job, "write"))

    def test_parse_test_results_aggregates_min_max_avg_and_valid_iterations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            iter1 = {
                "jobs": [
                    {
                        "read": {
                            "bw_bytes": 1_000_000_000,
                            "lat_ns": {
                                "min": 2_000_000,
                                "max": 20_000_000,
                                "mean": 10_000_000.0,
                                "stddev": 2_000_000.0,
                                "percentile": {
                                    "50.000000": 9_000_000,
                                    "90.000000": 15_000_000,
                                    "99.000000": 19_000_000,
                                },
                            },
                        },
                        "write": {"bw_bytes": 0},
                    }
                ]
            }
            iter2 = {
                "jobs": [
                    {
                        "read": {
                            "bw_bytes": 2_000_000_000,
                            "lat_ns": {
                                "min": 4_000_000,
                                "max": 30_000_000,
                                "mean": 14_000_000.0,
                                "stddev": 4_000_000.0,
                                "percentile": {
                                    "50.000000": 11_000_000,
                                    "90.000000": 21_000_000,
                                    "99.000000": 27_000_000,
                                },
                            },
                        },
                        "write": {"bw_bytes": 0},
                    }
                ]
            }

            # Valid file 1 (with FIO warning prefix before '{')
            with open(os.path.join(tmpdir, "fio_output_1.json"), "w") as f:
                f.write("fio: warning text before json\n" + json.dumps(iter1))
            # Valid file 2
            with open(os.path.join(tmpdir, "fio_output_2.json"), "w") as f:
                json.dump(iter2, f)
            # Empty file (must be skipped and not counted in iterations)
            with open(os.path.join(tmpdir, "fio_output_3.json"), "w") as f:
                pass
            # Corrupt JSON file (must be skipped and not counted in iterations)
            with open(os.path.join(tmpdir, "fio_output_4.json"), "w") as f:
                f.write("{invalid json")

            res = result_aggregator.parse_test_results(tmpdir, {"matrix_id": 7, "params": {"bs": "1M"}})

            self.assertEqual(res["iterations"], 2)
            self.assertEqual(res["matrix_id"], 7)
            self.assertAlmostEqual(res["read_bw_mbps"], 1500.0, places=6)
            self.assertEqual(res["write_bw_mbps"], 0)
            self.assertAlmostEqual(res["read_lat_min_ms"], 2.0, places=6)
            self.assertAlmostEqual(res["read_lat_max_ms"], 30.0, places=6)
            self.assertAlmostEqual(res["read_lat_avg_ms"], 12.0, places=6)
            self.assertAlmostEqual(res["read_lat_stddev_ms"], 3.0, places=6)
            self.assertAlmostEqual(res["read_lat_p50_ms"], 10.0, places=6)
            self.assertAlmostEqual(res["read_lat_p90_ms"], 18.0, places=6)
            self.assertAlmostEqual(res["read_lat_p99_ms"], 23.0, places=6)


if __name__ == "__main__":
    unittest.main()
