#!/usr/bin/env python3
"""Unit tests for go-client/run_go_matrix.py."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import run_go_matrix


class TestRunGoMatrix(unittest.TestCase):

    def test_resolve_numjobs_precedence_whitespace_and_validation(self):
        rm = run_go_matrix
        self.assertEqual(
            rm.resolve_numjobs(64, {"NUMJOBS": "32"}, {"NUMJOBS": "16"}), 64
        )
        self.assertEqual(
            rm.resolve_numjobs(None, {"NUMJOBS": "32"}, {"NUMJOBS": "112"}), 32
        )
        self.assertEqual(
            rm.resolve_numjobs(
                None, {"NUMJOBS": "", "NUM_JOBS": "24"}, {"NUMJOBS": "112"}
            ),
            24,
        )
        self.assertEqual(
            rm.resolve_numjobs(None, {"NUMJOBS": "  "}, {"NUMJOBS": "48"}), 48
        )
        self.assertEqual(
            rm.resolve_numjobs(None, {"NUMJOBS": ""}, {"NUMJOBS": "  "}), 112
        )
        self.assertEqual(rm.resolve_numjobs(None, {}, {}), 112)

        for bad_cli in (0, -1, False, True, 1.5, "abc"):
            with self.assertRaises(ValueError) as ctx:
                rm.resolve_numjobs(bad_cli, {}, {})
            self.assertIn("numjobs", str(ctx.exception).lower())

        for bad_cfg, bad_env in [
            ({"NUMJOBS": "0"}, {}),
            ({"NUMJOBS": "-2"}, {}),
            ({"NUMJOBS": "abc"}, {"NUMJOBS": "112"}),
            ({"NUM_JOBS": True}, {}),
            ({"NUMJOBS": ""}, {"NUMJOBS": "0"}),
        ]:
            with self.assertRaises(ValueError):
                rm.resolve_numjobs(None, bad_cfg, bad_env)

    def test_ensure_go_binary_uses_script_relative_path(self):
        rm = run_go_matrix
        self.assertEqual(
            rm.GO_BIN_PATH, os.path.join(rm.SCRIPT_DIR, "go-benchmark-client")
        )
        with tempfile.TemporaryDirectory() as other_cwd:
            prev_cwd = os.getcwd()
            os.chdir(other_cwd)
            try:
                fake_bin = os.path.join(other_cwd, "existing-bin")
                with open(fake_bin, "w", encoding="utf-8") as f:
                    f.write("")
                with patch.object(rm, "GO_BIN_PATH", fake_bin), patch.object(
                    rm, "run_command"
                ) as mock_cmd:
                    self.assertEqual(rm._ensure_go_binary(), fake_bin)
                    mock_cmd.assert_not_called()

                missing_bin = os.path.join(other_cwd, "missing-bin")
                with patch.object(rm, "GO_BIN_PATH", missing_bin), patch.object(
                    rm, "run_command"
                ) as mock_cmd:
                    self.assertEqual(rm._ensure_go_binary(), missing_bin)
                    mock_cmd.assert_called_once_with(
                        ["go", "build", "-o", missing_bin, "main.go"],
                        cwd=rm.SCRIPT_DIR,
                    )
            finally:
                os.chdir(prev_cwd)

    def test_main_invalid_params_exit_before_bq_truncation(self):
        rm = run_go_matrix
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nread,1G,1M,10\n")
            f.flush()
            with patch.object(rm, "_BQ_SUPPORTED", True), patch.object(
                rm, "bigquery", create=True
            ) as mock_bq, patch.object(rm, "truncate_bq_table") as mock_trunc:
                for bad_flag in (
                    "--numjobs=0",
                    "--iterations=0",
                    "--direct=2",
                    "--num=8",
                ):
                    with patch(
                        "sys.argv",
                        [
                            "run_go_matrix.py",
                            "--bucket-name=b",
                            f"--matrix-config={f.name}",
                            "--project-id=p",
                            "--bq-dataset-id=d",
                            "--bq-table-id=t",
                            bad_flag,
                        ],
                    ):
                        with self.assertRaises(SystemExit) as cm:
                            rm.main()
                        self.assertNotEqual(cm.exception.code, 0)

                with patch.dict(
                    os.environ, {"NUMJOBS": "0", "NUM_JOBS": ""}, clear=False
                ), patch(
                    "sys.argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name=b",
                        f"--matrix-config={f.name}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    with self.assertRaises(SystemExit) as cm2:
                        rm.main()
                    self.assertEqual(cm2.exception.code, 1)

                mock_trunc.assert_not_called()
                mock_bq.Client.assert_not_called()

    def test_main_header_only_and_empty_csv_skip_bq_truncation(self):
        rm = run_go_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            header_only = os.path.join(tmpdir, "header_only.csv")
            with open(header_only, "w", encoding="utf-8") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n")

            empty_csv = os.path.join(tmpdir, "empty.csv")
            with open(empty_csv, "w", encoding="utf-8"):
                pass

            short_bad_csv = os.path.join(tmpdir, "short_bad.csv")
            with open(short_bad_csv, "w", encoding="utf-8") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nread,1G\n")

            with patch.object(rm, "_BQ_SUPPORTED", True), patch.object(
                rm, "bigquery", create=True
            ) as mock_bq, patch.object(
                rm, "truncate_bq_table"
            ) as mock_trunc, patch.object(
                rm, "_ensure_go_binary"
            ) as mock_bin:
                # 1. Header-only 0-row CSV with valid env returns early without truncating BQ
                with patch.dict(
                    os.environ, {"NUMJOBS": "112", "NUM_JOBS": ""}, clear=False
                ), patch(
                    "sys.argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name=b",
                        f"--matrix-config={header_only}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    rm.main()

                # 2. Header-only 0-row CSV with invalid env exits 1 without truncating BQ
                with patch.dict(
                    os.environ, {"NUMJOBS": "0", "NUM_JOBS": ""}, clear=False
                ), patch(
                    "sys.argv",
                    [
                        "run_go_matrix.py",
                        "--bucket-name=b",
                        f"--matrix-config={header_only}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    with self.assertRaises(SystemExit) as cm_env:
                        rm.main()
                    self.assertEqual(cm_env.exception.code, 1)

                # 3. 0-byte empty CSV and short missing-required-cell CSV exit 1 before BQ truncation
                for bad_csv in (empty_csv, short_bad_csv):
                    with patch.dict(
                        os.environ,
                        {"NUMJOBS": "112", "NUM_JOBS": ""},
                        clear=False,
                    ), patch(
                        "sys.argv",
                        [
                            "run_go_matrix.py",
                            "--bucket-name=b",
                            f"--matrix-config={bad_csv}",
                            "--project-id=p",
                            "--bq-dataset-id=d",
                            "--bq-table-id=t",
                        ],
                    ):
                        with self.assertRaises(SystemExit) as cm_csv:
                            rm.main()
                        self.assertEqual(cm_csv.exception.code, 1)

                mock_trunc.assert_not_called()
                mock_bq.Client.assert_not_called()
                mock_bin.assert_not_called()

    def test_main_cli_overrides_csv_whitespace_and_ignores_direct_flag(self):
        rm = run_go_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            out_dir = os.path.join(tmpdir, "out")
            # Row 1 has legacy NUM_JOBS=32 + whitespace; Row 2 is a short row missing optional NUM_JOBS
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(
                    " READ_TYPE , FILE_SIZE , BLOCK_SIZE , NR_FILES , NUM_JOBS \n"
                    " read , 1G , 1M , 10 , 32 \n"
                    " read , 1G , 4M , 5 \n"
                )

            executed_cmds = []

            def fake_run_command(cmd, check=True, cwd=None):
                executed_cmds.append((cmd, check, cwd))
                return MagicMock(stdout='{"jobs": []}')

            with patch.dict(
                os.environ, {"NUMJOBS": "112", "NUM_JOBS": ""}, clear=False
            ), patch.object(
                rm, "_ensure_go_binary", return_value="/fake/go-benchmark-client"
            ), patch.object(
                rm, "run_command", side_effect=fake_run_command
            ), patch.object(
                rm, "print_summary"
            ), self.assertLogs(level="INFO") as captured_logs, patch(
                "sys.argv",
                [
                    "run_go_matrix.py",
                    "--bucket-name=b",
                    f"--matrix-config={csv_path}",
                    f"--output-dir={out_dir}",
                    "--numjobs=64",
                    "--direct=0",
                ],
            ):
                rm.main()

            bench_calls = [
                (cmd, cwd)
                for (cmd, _, cwd) in executed_cmds
                if cmd[0] == "/fake/go-benchmark-client"
            ]
            self.assertEqual(len(bench_calls), 2)
            for cmd, cwd in bench_calls:
                self.assertEqual(cwd, rm.SCRIPT_DIR)
                self.assertIn("--numjobs=64", cmd)

            # Both directories reflect effective overridden NUMJOBS_64 (and normalize NUM_JOBS -> NUMJOBS)
            self.assertTrue(
                os.path.isdir(
                    os.path.join(
                        out_dir,
                        "BLOCK_SIZE_1M_FILE_SIZE_1G_NR_FILES_10_NUMJOBS_64_READ_TYPE_read",
                    )
                )
            )
            self.assertTrue(
                os.path.isdir(
                    os.path.join(
                        out_dir,
                        "BLOCK_SIZE_4M_FILE_SIZE_1G_NR_FILES_5_NUMJOBS_64_READ_TYPE_read",
                    )
                )
            )
            joined_logs = "\n".join(captured_logs.output)
            self.assertIn("NUMJOBS=64", joined_logs)
            self.assertNotIn("NUM_JOBS=", joined_logs)

    def test_main_propagates_benchmark_failure_exit_code(self):
        rm = run_go_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nread,1G,1M,10\n")

            def fake_run_command(cmd, check=True, cwd=None):
                if cmd[0] == "sh":
                    return MagicMock(stdout="")
                raise subprocess.CalledProcessError(1, cmd[0])

            with patch.dict(
                os.environ, {"NUMJOBS": "112", "NUM_JOBS": ""}, clear=False
            ), patch.object(
                rm, "_ensure_go_binary", return_value="/fake/go-benchmark-client"
            ), patch.object(
                rm, "run_command", side_effect=fake_run_command
            ), patch.object(
                rm, "print_summary"
            ), patch(
                "sys.argv",
                [
                    "run_go_matrix.py",
                    "--bucket-name=b",
                    f"--matrix-config={csv_path}",
                    f"--output-dir={os.path.join(tmpdir, 'out')}",
                ],
            ):
                with self.assertRaises(SystemExit) as cm:
                    rm.main()
                self.assertEqual(cm.exception.code, 1)

    def test_main_handles_trailing_comma_extra_csv_fields(self):
        rm = run_go_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "matrix.csv")
            out_dir = os.path.join(tmpdir, "out")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nread,1G,1M,10,\n")
            with patch.dict(
                os.environ, {"NUMJOBS": "112", "NUM_JOBS": ""}, clear=False
            ), patch.object(rm, "_BQ_SUPPORTED", True), patch.object(
                rm, "bigquery", create=True
            ), patch.object(
                rm, "truncate_bq_table"
            ) as mock_trunc, patch.object(
                rm, "upload_results_to_bq"
            ) as mock_upload, patch.object(
                rm, "_ensure_go_binary", return_value="/fake/go-benchmark-client"
            ), patch.object(
                rm, "run_command", return_value=MagicMock(stdout='{"jobs": []}')
            ) as mock_run_cmd, patch.object(
                rm, "print_summary"
            ), patch(
                "sys.argv",
                [
                    "run_go_matrix.py",
                    "--bucket-name=b",
                    f"--matrix-config={csv_path}",
                    f"--output-dir={out_dir}",
                    "--project-id=p",
                    "--bq-dataset-id=d",
                    "--bq-table-id=t",
                ],
            ):
                rm.main()
            mock_trunc.assert_called_once()
            # 1 cache-drop call + 1 benchmark call
            self.assertEqual(mock_run_cmd.call_count, 2)
            mock_upload.assert_called_once()
            expected_subdir = os.path.join(
                out_dir, "BLOCK_SIZE_1M_FILE_SIZE_1G_NR_FILES_10_READ_TYPE_read"
            )
            self.assertTrue(os.path.isdir(expected_subdir))


if __name__ == "__main__":
    unittest.main()
