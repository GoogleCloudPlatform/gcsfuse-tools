#!/usr/bin/env python3
"""Unit tests for Go-client matrix runner adaptive convergence (go-client/run_go_matrix.py)."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GO_CLIENT_DIR = os.path.join(REPO_ROOT, "go-client")


def _load_run_go_matrix():
    spec = importlib.util.spec_from_file_location(
        "run_go_matrix", os.path.join(GO_CLIENT_DIR, "run_go_matrix.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_go_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod


run_go_matrix = _load_run_go_matrix()


def _make_go_json(bw_mibps, lat_ms=10.0, bs="1M", filesize="1G", protocol="http1"):
    return json.dumps({
        "global options": {"iodepth": "1", "rw": "read"},
        "jobs": [
            {
                "jobname": "go-client-read",
                "job options": {
                    "bs": bs,
                    "filesize": filesize,
                    "nrfiles": "10",
                    "numjobs": "128",
                    "client_protocol": protocol,
                },
                "read": {
                    "bw": bw_mibps * 1024.0,
                    "iops": bw_mibps,
                    "lat_ns": {
                        "mean": int(lat_ms * 1_000_000.0),
                        "99.000000": int(lat_ms * 1_500_000.0),
                        "99.500000": int(lat_ms * 1_800_000.0),
                        "99.900000": int(lat_ms * 2_000_000.0),
                        "percentiles": {"99.000000": int(lat_ms * 1_500_000.0)},
                    },
                },
            }
        ],
    })


class _InMemoryBQClient:
    def __init__(self):
        self.inserted_rows = {}

    def dataset(self, dataset_id):
        ds = MagicMock()
        ds.table.return_value = MagicMock()
        return ds

    def get_dataset(self, dataset_ref):
        return dataset_ref

    def get_table(self, table_ref):
        return table_ref

    def query(self, sql):
        job = MagicMock()
        job.result.return_value = None
        return job

    def insert_rows_json(self, full_table_id, rows):
        self.inserted_rows.setdefault(full_table_id, []).extend(rows)
        return []


class TestGoClientMatrixAdaptiveConvergence(unittest.TestCase):

    def test_sys_path_fallback_imports_convergence_from_go_client_cwd(self):
        proc = subprocess.run(
            [sys.executable, "-c", "import run_go_matrix; import convergence"],
            cwd=GO_CLIENT_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_early_convergence_single_config_stops_at_min_iterations(self):
        stream = [3000.0, 3010.0, 2990.0, 3005.0, 2995.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            call_idx = {"n": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                call_idx["n"] += 1
                res = MagicMock()
                res.stdout = _make_go_json(stream[call_idx["n"] - 1])
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "3",
                        "--max-iterations", "8",
                        "--convergence-threshold", "0.05",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertEqual(call_idx["n"], 3)

    def test_independent_per_config_convergence_stopping_multi_row_csv(self):
        cfg_a = [2500.0, 2505.0, 2495.0, 2500.0, 2500.0, 2500.0]
        cfg_b = [600.0, 1400.0, 700.0, 1300.0, 800.0, 1200.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write(
                    "READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n"
                    "seq,1G,1M,10\n"
                    "seq,1G,128K,10\n"
                )

            counts = {"1M": 0, "128K": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                joined = " ".join(cmd)
                res = MagicMock()
                if "--bs=1M" in joined:
                    counts["1M"] += 1
                    res.stdout = _make_go_json(cfg_a[counts["1M"] - 1], bs="1M")
                else:
                    counts["128K"] += 1
                    res.stdout = _make_go_json(cfg_b[counts["128K"] - 1], bs="128K")
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "3",
                        "--max-iterations", "6",
                        "--convergence-threshold", "0.04",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertEqual(counts["1M"], 3)
            self.assertEqual(counts["128K"], 6)

    def test_transient_outlier_stall_immunity(self):
        stream = [3200.0, 3200.0, 400.0, 3200.0, 3200.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            call_idx = {"n": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                call_idx["n"] += 1
                res = MagicMock()
                res.stdout = _make_go_json(stream[call_idx["n"] - 1])
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "4",
                        "--max-iterations", "8",
                        "--convergence-threshold", "0.05",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertEqual(call_idx["n"], 4)

    def test_high_variance_stream_terminates_cleanly_at_max_iterations(self):
        stream = [1000.0, 3000.0, 1100.0, 2900.0, 1050.0, 2950.0]
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            call_idx = {"n": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                call_idx["n"] += 1
                res = MagicMock()
                res.stdout = _make_go_json(stream[call_idx["n"] - 1])
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "3",
                        "--max-iterations", "5",
                        "--convergence-threshold", "0.02",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertEqual(call_idx["n"], 5)

    def test_legacy_fixed_iterations_when_convergence_flags_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            call_idx = {"n": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                call_idx["n"] += 1
                res = MagicMock()
                res.stdout = _make_go_json(2000.0)
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--iterations", "4",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertEqual(call_idx["n"], 4)

    def test_malformed_json_iteration_resilience(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            call_idx = {"n": 0}

            def fake_run_cmd(cmd, check=True, cwd=None):
                call_idx["n"] += 1
                res = MagicMock()
                if call_idx["n"] == 2:
                    res.stdout = "NOT VALID JSON"
                else:
                    res.stdout = _make_go_json(2000.0)
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "3",
                        "--max-iterations", "5",
                        "--convergence-threshold", "0.05",
                    ],
                ),
            ):
                run_go_matrix.main()

            self.assertGreaterEqual(call_idx["n"], 4)

    def test_bq_upload_preserves_exact_6_column_schema(self):
        harness = _InMemoryBQClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, check=True, cwd=None):
                res = MagicMock()
                res.stdout = _make_go_json(3000.0)
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(run_go_matrix.bigquery, "Client", return_value=harness),
                patch.object(
                    sys,
                    "argv",
                    [
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
                    ],
                ),
            ):
                run_go_matrix.main()

            rows = harness.inserted_rows.get("test-proj.test_ds.go_client_read_grpc", [])
            self.assertGreaterEqual(len(rows), 3)
            for row in rows:
                self.assertEqual(
                    set(row.keys()),
                    {
                        "run_timestamp",
                        "iteration",
                        "gcsfuse_flags",
                        "fio_env",
                        "cpu_limit_list",
                        "fio_json_output",
                    },
                )

    def test_summary_file_written_with_statistical_metrics_and_empty_results_guard(self):
        with self.assertLogs(level="WARNING") as cm:
            ret = run_go_matrix.print_summary([], summary_file=None)
        self.assertIsNone(ret)
        self.assertTrue(any("No results to summarize" in msg for msg in cm.output))

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            def fake_run_cmd(cmd, check=True, cwd=None):
                res = MagicMock()
                res.stdout = _make_go_json(3000.0)
                return res

            with (
                patch.object(os.path, "exists", return_value=True),
                patch.object(subprocess, "run"),
                patch.object(run_go_matrix, "run_command", side_effect=fake_run_cmd),
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--summary-file-name", "summary.txt",
                        "--min-iterations", "3",
                        "--max-iterations", "5",
                        "--convergence-threshold", "0.05",
                    ],
                ),
            ):
                run_go_matrix.main()

            summary_path = os.path.join(tmpdir, "summary.txt")
            self.assertTrue(os.path.isfile(summary_path))
            with open(summary_path, "r") as f:
                text = f.read()
            self.assertIn("3000", text)
            self.assertIn("--- Statistical Convergence Summary ---", text)

    def test_invalid_min_greater_than_max_iterations_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nseq,1G,1M,10\n")

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name", "test-b",
                        "--matrix-config", csv_path,
                        "--output-dir", tmpdir,
                        "--min-iterations", "6",
                        "--max-iterations", "3",
                    ],
                ),
                self.assertRaises(ValueError),
            ):
                run_go_matrix.main()


if __name__ == "__main__":
    unittest.main()
