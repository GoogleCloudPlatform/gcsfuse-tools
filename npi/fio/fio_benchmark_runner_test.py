import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

_FIO_DIR = os.path.dirname(os.path.abspath(__file__))
if _FIO_DIR not in sys.path:
    sys.path.insert(0, _FIO_DIR)

import fio_benchmark_runner
import run_fio_benchmark
import run_fio_matrix


class TestClearCacheDir(unittest.TestCase):

    @patch("os.path.exists")
    @patch("shutil.rmtree")
    def test_clear_cache_dir_success(self, mock_rmtree, mock_exists):
        mock_exists.return_value = True

        flags = "--cache-dir=/tmp/cache"
        fio_benchmark_runner.clear_cache_dir(flags)

        mock_rmtree.assert_called_once_with("/tmp/cache")

    @patch("os.path.exists")
    def test_clear_cache_dir_no_flag(self, mock_exists):
        fio_benchmark_runner.clear_cache_dir("--other-flag=value")
        mock_exists.assert_not_called()

    @patch("os.path.exists")
    def test_clear_cache_dir_not_exists(self, mock_exists):
        mock_exists.return_value = False
        fio_benchmark_runner.clear_cache_dir("--cache-dir=/tmp/cache")
        mock_exists.assert_called_once_with("/tmp/cache")


class TestFioTemplatesAndDirectEnv(unittest.TestCase):

    def test_real_fio_templates_parameterize_direct_and_numjobs(self):
        read_fio_path = os.path.join(_FIO_DIR, "read.fio")
        write_fio_path = os.path.join(_FIO_DIR, "write.fio")

        with open(read_fio_path, "r", encoding="utf-8") as f:
            read_content = f.read()
        with open(write_fio_path, "r", encoding="utf-8") as f:
            write_content = f.read()

        for path, content in (
            (read_fio_path, read_content),
            (write_fio_path, write_content),
        ):
            self.assertIn("direct=${DIRECT}", content, path)
            self.assertIn("numjobs=${NUMJOBS}", content, path)
            self.assertNotIn("direct=1", content, path)
            self.assertNotIn("direct=0", content, path)

        self.assertIn("rw=${READ_TYPE}", read_content)
        self.assertIn(
            "filename_format=write/${FILE_SIZE}/${BLOCK_SIZE}/${NR_FILES}/job_$jobnum/experiment.$filenum",
            write_content,
        )

        fio_bin = shutil.which("fio")
        if fio_bin:
            parse_env = os.environ.copy()
            parse_env.update(
                {
                    "DIRECT": "0",
                    "NUMJOBS": "112",
                    "DIR": "/tmp",
                    "READ_TYPE": "read",
                    "FILE_SIZE": "1G",
                    "BLOCK_SIZE": "1M",
                    "NR_FILES": "1",
                }
            )
            for tpl_path in (read_fio_path, write_fio_path):
                res = subprocess.run(
                    [fio_bin, "--parse-only", tpl_path],
                    capture_output=True,
                    text=True,
                    env=parse_env,
                    check=False,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"fio --parse-only failed on {tpl_path}: {res.stderr}",
                )

    @patch("os.makedirs")
    @patch("fio_benchmark_runner.print_summary")
    @patch("fio_benchmark_runner.parse_fio_output", return_value=[])
    @patch("fio_benchmark_runner.run_fio_test")
    @patch("fio_benchmark_runner.precreate_benchmark_directories")
    @patch("fio_benchmark_runner.run_command")
    def test_run_benchmark_propagates_direct_and_legacy_num_jobs_env(
        self,
        mock_run_cmd,
        mock_precreate,
        mock_run_fio,
        mock_parse,
        mock_summary,
        mock_makedirs,
    ):
        with patch.dict(
            os.environ,
            {"DIRECT": "0", "NUMJOBS": "112", "NUM_JOBS": "999"},
            clear=False,
        ):
            fio_benchmark_runner.run_benchmark(
                gcsfuse_flags="",
                bucket_name=None,
                iterations=1,
                fio_config="/read.fio",
                work_dir="/tmp/work",
                output_dir="/tmp/out",
                project_id=None,
                mount_path="/mnt/data",
            )
        _, kwargs = mock_run_fio.call_args
        self.assertEqual(kwargs["fio_env"]["DIRECT"], "0")
        self.assertEqual(kwargs["fio_env"]["NUMJOBS"], "112")
        self.assertEqual(kwargs["fio_env"]["NUM_JOBS"], "112")

    @patch("os.makedirs")
    @patch("fio_benchmark_runner.print_summary")
    @patch("fio_benchmark_runner.parse_fio_output", return_value=[])
    @patch("fio_benchmark_runner.run_fio_test")
    @patch("fio_benchmark_runner.precreate_benchmark_directories")
    @patch("fio_benchmark_runner.run_command")
    def test_run_benchmark_defaults_direct_to_1(
        self,
        mock_run_cmd,
        mock_precreate,
        mock_run_fio,
        mock_parse,
        mock_summary,
        mock_makedirs,
    ):
        env_without_direct = {
            k: v
            for k, v in os.environ.items()
            if k not in ("DIRECT", "NUMJOBS", "NUM_JOBS")
        }
        with patch.dict(os.environ, env_without_direct, clear=True):
            fio_benchmark_runner.run_benchmark(
                gcsfuse_flags="",
                bucket_name=None,
                iterations=1,
                fio_config="/read.fio",
                work_dir="/tmp/work",
                output_dir="/tmp/out",
                project_id=None,
                mount_path="/mnt/data",
            )
        _, kwargs = mock_run_fio.call_args
        self.assertEqual(kwargs["fio_env"]["DIRECT"], "1")
        self.assertEqual(kwargs["fio_env"]["NUMJOBS"], "112")
        self.assertEqual(kwargs["fio_env"]["NUM_JOBS"], "112")

    def test_resolve_fio_params_fallbacks_and_validation(self):
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params(
                {"DIRECT": "", "NUMJOBS": "  "}, {"DIRECT": "", "NUMJOBS": ""}
            ),
            ("1", "112"),
        )
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params(
                {"DIRECT": "  ", "NUMJOBS": ""}, {"DIRECT": "0", "NUMJOBS": "48"}
            ),
            ("0", "48"),
        )
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params(
                {"DIRECT": 0, "NUM_JOBS": "16"},
                {"DIRECT": "1", "NUMJOBS": "112"},
            ),
            ("0", "16"),
        )
        for bad_overrides, bad_env in [
            ({"DIRECT": "2"}, {}),
            ({}, {"DIRECT": "yes"}),
            ({"DIRECT": True}, {}),
            ({"DIRECT": [1]}, {}),
            ({"NUMJOBS": "0"}, {"NUMJOBS": "112"}),
            ({"NUMJOBS": "-4"}, {}),
            ({"NUMJOBS": "abc"}, {}),
            ({"NUMJOBS": ""}, {"NUMJOBS": "0"}),
        ]:
            with self.assertRaises(ValueError):
                fio_benchmark_runner.resolve_fio_params(bad_overrides, bad_env)

    @patch("os.makedirs")
    @patch("fio_benchmark_runner.subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_precreate_directories_zero_indexed_and_rejects_invalid_before_delete(
        self, mock_exists, mock_subproc, mock_makedirs
    ):
        # 1. Invalid NUMJOBS fails BEFORE concurrent_delete.py executes
        with patch.dict(
            os.environ,
            {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                fio_benchmark_runner.precreate_benchmark_directories(
                    "/mnt/data",
                    {
                        "FILE_SIZE": "1G",
                        "BLOCK_SIZE": "1M",
                        "NR_FILES": "1",
                        "NUMJOBS": "0",
                    },
                    "/write.fio",
                )
        mock_subproc.assert_not_called()

        # 2. Legacy NUM_JOBS="4" creates exact 0-indexed job_0 .. job_3 (never job_4)
        with patch.dict(
            os.environ,
            {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
            clear=False,
        ):
            fio_benchmark_runner.precreate_benchmark_directories(
                "/mnt/data",
                {
                    "FILE_SIZE": " 1G ",
                    "BLOCK_SIZE": " 1M ",
                    "NR_FILES": " 1 ",
                    "NUM_JOBS": "4",
                },
                "/write.fio",
            )
        mock_subproc.assert_called_once()
        expected_base = "/mnt/data/write/1G/1M/1"
        self.assertEqual(
            mock_makedirs.call_args_list,
            [
                call(expected_base, exist_ok=True),
                call(f"{expected_base}/job_0", exist_ok=True),
                call(f"{expected_base}/job_1", exist_ok=True),
                call(f"{expected_base}/job_2", exist_ok=True),
                call(f"{expected_base}/job_3", exist_ok=True),
            ],
        )

        # 3. FILE_SIZE/BLOCK_SIZE/NR_FILES resolved from os.environ when fio_env only carries NUMJOBS/DIRECT
        mock_subproc.reset_mock()
        mock_makedirs.reset_mock()
        with patch.dict(
            os.environ,
            {
                "FILE_SIZE": " 2G ",
                "BLOCK_SIZE": " 4M ",
                "NR_FILES": " 8 ",
                "DIRECT": "1",
                "NUMJOBS": "112",
                "NUM_JOBS": "",
            },
            clear=False,
        ):
            fio_benchmark_runner.precreate_benchmark_directories(
                "/mnt/data",
                {"NUMJOBS": "4", "DIRECT": "0"},
                "/write.fio",
            )
        mock_subproc.assert_called_once()
        expected_env_base = "/mnt/data/write/2G/4M/8"
        self.assertEqual(
            mock_makedirs.call_args_list,
            [
                call(expected_env_base, exist_ok=True),
                call(f"{expected_env_base}/job_0", exist_ok=True),
                call(f"{expected_env_base}/job_1", exist_ok=True),
                call(f"{expected_env_base}/job_2", exist_ok=True),
                call(f"{expected_env_base}/job_3", exist_ok=True),
            ],
        )

        # 4. Missing/blank dimensions across both fio_env and os.environ return early cleanly
        mock_subproc.reset_mock()
        mock_makedirs.reset_mock()
        with patch.dict(
            os.environ,
            {"FILE_SIZE": "2G", "BLOCK_SIZE": "  ", "NR_FILES": "8"},
            clear=False,
        ):
            fio_benchmark_runner.precreate_benchmark_directories(
                "/mnt/data",
                {"NUMJOBS": "4", "DIRECT": "0"},
                "/write.fio",
            )
        mock_subproc.assert_not_called()
        mock_makedirs.assert_not_called()

    @patch("fio_benchmark_runner.mount_gcsfuse")
    def test_run_benchmark_rejects_invalid_params_and_bind_fio_without_sys_exit(
        self, mock_mount
    ):
        with patch.dict(
            os.environ,
            {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="",
                    bucket_name="my-bucket",
                    iterations=1,
                    fio_config="/read.fio",
                    work_dir="/tmp/work",
                    output_dir="/tmp/out",
                    project_id=None,
                    fio_env={"DIRECT": "invalid"},
                )
            with self.assertRaises(ValueError):
                fio_benchmark_runner.run_benchmark(
                    gcsfuse_flags="",
                    bucket_name="my-bucket",
                    iterations=1,
                    fio_config="/read.fio",
                    work_dir="/tmp/work",
                    output_dir="/tmp/out",
                    project_id=None,
                    bind_fio=True,
                    cpu_limit_list=None,
                )
        mock_mount.assert_not_called()

    @patch("os.path.ismount", return_value=False)
    @patch("time.sleep")
    @patch("os.makedirs")
    @patch("fio_benchmark_runner.run_command")
    def test_mount_gcsfuse_raises_runtime_error_when_not_mounted(
        self, mock_run_cmd, mock_makedirs, mock_sleep, mock_ismount
    ):
        with self.assertRaises(RuntimeError):
            fio_benchmark_runner.mount_gcsfuse(
                "/gcsfuse/gcsfuse", "", "my-bucket", "/mnt/point"
            )


class TestRunFioMatrixAndBenchmarkEntrypoints(unittest.TestCase):

    def test_run_fio_matrix_rejects_invalid_params_before_bq_truncation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write(
                "READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES,NUMJOBS,DIRECT\n"
                "read,1G,1M,1,,\n"
            )
            f.flush()
            with patch.object(
                fio_benchmark_runner, "_BQ_SUPPORTED", True
            ), patch.object(
                fio_benchmark_runner, "bigquery", create=True
            ) as mock_bq, patch.object(
                fio_benchmark_runner, "truncate_bq_table"
            ) as mock_trunc, patch.object(
                fio_benchmark_runner, "run_benchmark"
            ) as mock_run_bench:
                # 1. Invalid CLI flags (--numjobs=0, --iterations=0, --direct=2, --bind-fio without --cpu-limit-list)
                for bad_extra in (
                    ["--numjobs=0"],
                    ["--iterations=0"],
                    ["--direct=2"],
                    ["--bind-fio"],
                    ["--num=8"],
                ):
                    with patch(
                        "sys.argv",
                        [
                            "run_fio_matrix.py",
                            "--mount-path=/data",
                            "--fio-template=/read.fio",
                            f"--matrix-config={f.name}",
                            "--project-id=p",
                            "--bq-dataset-id=d",
                            "--bq-table-id=t",
                        ]
                        + bad_extra,
                    ):
                        with self.assertRaises(SystemExit) as cm:
                            run_fio_matrix.main()
                        self.assertNotEqual(cm.exception.code, 0)
                mock_trunc.assert_not_called()
                mock_bq.Client.assert_not_called()

                # 2. Invalid DIRECT in os.environ rejected before truncate_bq_table
                with patch.dict(
                    os.environ,
                    {"DIRECT": "invalid", "NUMJOBS": "112", "NUM_JOBS": ""},
                    clear=False,
                ), patch(
                    "sys.argv",
                    [
                        "run_fio_matrix.py",
                        "--mount-path=/data",
                        "--fio-template=/read.fio",
                        f"--matrix-config={f.name}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    with self.assertRaises(SystemExit) as cm2:
                        run_fio_matrix.main()
                    self.assertEqual(cm2.exception.code, 1)
                mock_trunc.assert_not_called()

                # 3. Blank CSV cells fall back cleanly to os.environ DIRECT=0, NUMJOBS=64
                #    and omit blank optional columns from output_dir name
                with patch.dict(
                    os.environ,
                    {"DIRECT": "0", "NUMJOBS": "64", "NUM_JOBS": ""},
                    clear=False,
                ), patch(
                    "sys.argv",
                    [
                        "run_fio_matrix.py",
                        "--mount-path=/data",
                        "--fio-template=/read.fio",
                        f"--matrix-config={f.name}",
                    ],
                ):
                    run_fio_matrix.main()
                mock_run_bench.assert_called_once()
                _, kwargs = mock_run_bench.call_args
                self.assertEqual(kwargs["fio_env"]["DIRECT"], "0")
                self.assertEqual(kwargs["fio_env"]["NUMJOBS"], "64")
                self.assertTrue(
                    kwargs["output_dir"].endswith(
                        "BLOCK_SIZE_1M_FILE_SIZE_1G_NR_FILES_1_READ_TYPE_read"
                    )
                )

    def test_run_fio_matrix_cli_overrides_csv_and_normalizes_logs_and_dirs(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write(
                " READ_TYPE , FILE_SIZE , BLOCK_SIZE , NR_FILES , NUM_JOBS , DIRECT \n"
                " read , 1G , 1M , 10 , 32 , 1 \n"
                " randread , 1G , 64K , 10 \n"
            )
            f.flush()
            with patch.object(
                fio_benchmark_runner, "run_benchmark"
            ) as mock_run_bench, patch.dict(
                os.environ,
                {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
                clear=False,
            ), self.assertLogs(level="INFO") as captured_logs, patch(
                "sys.argv",
                [
                    "run_fio_matrix.py",
                    "--mount-path=/data",
                    "--fio-template=/read.fio",
                    f"--matrix-config={f.name}",
                    "--numjobs=64",
                    "--direct=0",
                ],
            ):
                run_fio_matrix.main()

            self.assertEqual(mock_run_bench.call_count, 2)
            first_kwargs = mock_run_bench.call_args_list[0].kwargs
            second_kwargs = mock_run_bench.call_args_list[1].kwargs

            for kw in (first_kwargs, second_kwargs):
                self.assertEqual(kw["fio_env"]["DIRECT"], "0")
                self.assertEqual(kw["fio_env"]["NUMJOBS"], "64")
                self.assertNotIn("NUM_JOBS", kw["fio_env"])

            self.assertTrue(
                first_kwargs["output_dir"].endswith(
                    "BLOCK_SIZE_1M_DIRECT_0_FILE_SIZE_1G_NR_FILES_10_NUMJOBS_64_READ_TYPE_read"
                )
            )
            self.assertTrue(
                second_kwargs["output_dir"].endswith(
                    "BLOCK_SIZE_64K_DIRECT_0_FILE_SIZE_1G_NR_FILES_10_NUMJOBS_64_READ_TYPE_randread"
                )
            )
            logs_text = "\n".join(captured_logs.output)
            self.assertIn(
                "Configuration: BLOCK_SIZE=1M, DIRECT=0, FILE_SIZE=1G, NR_FILES=10, NUMJOBS=64, READ_TYPE=read",
                logs_text,
            )

    def test_run_fio_matrix_header_only_and_malformed_csv_skip_bq_truncation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            header_only = os.path.join(tmpdir, "header_only.csv")
            with open(header_only, "w", encoding="utf-8") as f:
                f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n")

            empty_csv = os.path.join(tmpdir, "empty.csv")
            with open(empty_csv, "w", encoding="utf-8"):
                pass

            missing_read_type = os.path.join(tmpdir, "missing_read_type.csv")
            with open(missing_read_type, "w", encoding="utf-8") as f:
                f.write("FILE_SIZE,BLOCK_SIZE,NR_FILES\n1G,1M,10\n")

            with patch.object(
                fio_benchmark_runner, "_BQ_SUPPORTED", True
            ), patch.object(
                fio_benchmark_runner, "bigquery", create=True
            ) as mock_bq, patch.object(
                fio_benchmark_runner, "truncate_bq_table"
            ) as mock_trunc, patch.object(
                fio_benchmark_runner, "run_benchmark"
            ) as mock_run_bench:
                # 1. Header-only 0-row CSV returns early without truncating BQ
                with patch.dict(
                    os.environ,
                    {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
                    clear=False,
                ), patch(
                    "sys.argv",
                    [
                        "run_fio_matrix.py",
                        "--mount-path=/data",
                        "--fio-template=/read.fio",
                        f"--matrix-config={header_only}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    run_fio_matrix.main()

                # 2. Header-only 0-row CSV with invalid env exits 1 without truncating BQ
                with patch.dict(
                    os.environ,
                    {"DIRECT": "9", "NUMJOBS": "112", "NUM_JOBS": ""},
                    clear=False,
                ), patch(
                    "sys.argv",
                    [
                        "run_fio_matrix.py",
                        "--mount-path=/data",
                        "--fio-template=/read.fio",
                        f"--matrix-config={header_only}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    with self.assertRaises(SystemExit) as cm_env:
                        run_fio_matrix.main()
                    self.assertEqual(cm_env.exception.code, 1)

                # 3. Empty 0-byte CSV and missing READ_TYPE column exit 1 before BQ truncation
                for bad_csv in (empty_csv, missing_read_type):
                    with patch.dict(
                        os.environ,
                        {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
                        clear=False,
                    ), patch(
                        "sys.argv",
                        [
                            "run_fio_matrix.py",
                            "--mount-path=/data",
                            "--fio-template=/read.fio",
                            f"--matrix-config={bad_csv}",
                            "--project-id=p",
                            "--bq-dataset-id=d",
                            "--bq-table-id=t",
                        ],
                    ):
                        with self.assertRaises(SystemExit) as cm_bad:
                            run_fio_matrix.main()
                        self.assertEqual(cm_bad.exception.code, 1)

                mock_trunc.assert_not_called()
                mock_bq.Client.assert_not_called()
                mock_run_bench.assert_not_called()

    def test_run_fio_matrix_handles_trailing_comma_extra_csv_fields(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write(
                "READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\n"
                "read,1G,1M,10,\n"
                "randread,1G,64K,10,\n"
            )
            f.flush()
            with patch.object(
                fio_benchmark_runner, "_BQ_SUPPORTED", True
            ), patch.object(
                fio_benchmark_runner, "bigquery", create=True
            ) as mock_bq, patch.object(
                fio_benchmark_runner, "truncate_bq_table"
            ) as mock_trunc, patch.object(
                fio_benchmark_runner, "run_benchmark"
            ) as mock_run_bench, patch.dict(
                os.environ,
                {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
                clear=False,
            ):
                # Partial BQ flags fail with exit code 2 before CSV loading or BQ truncation
                for partial_bq in (
                    ["--project-id=p"],
                    ["--project-id=p", "--bq-dataset-id=d"],
                    ["--bq-dataset-id=d", "--bq-table-id=t"],
                ):
                    with patch(
                        "sys.argv",
                        [
                            "run_fio_matrix.py",
                            "--mount-path=/data",
                            "--fio-template=/read.fio",
                            "--matrix-config=/nonexistent/not_loaded.csv",
                        ]
                        + partial_bq,
                    ), patch("sys.stderr", new_callable=io.StringIO):
                        with self.assertRaises(SystemExit) as cm_bq:
                            run_fio_matrix.main()
                        self.assertEqual(cm_bq.exception.code, 2)
                mock_bq.Client.assert_not_called()
                mock_trunc.assert_not_called()

                with patch(
                    "sys.argv",
                    [
                        "run_fio_matrix.py",
                        "--mount-path=/data",
                        "--fio-template=/read.fio",
                        f"--matrix-config={f.name}",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ],
                ):
                    run_fio_matrix.main()
            # Single bigquery.Client created & reused across both matrix rows!
            mock_bq.Client.assert_called_once_with(project="p")
            mock_trunc.assert_called_once()
            self.assertEqual(mock_run_bench.call_count, 2)
            first_kw = mock_run_bench.call_args_list[0].kwargs
            second_kw = mock_run_bench.call_args_list[1].kwargs
            self.assertIs(first_kw["bq_client"], mock_bq.Client.return_value)
            self.assertIs(second_kw["bq_client"], mock_bq.Client.return_value)
            self.assertIs(
                first_kw["verified_bq_tables"], second_kw["verified_bq_tables"]
            )
            self.assertTrue(
                first_kw["output_dir"].endswith(
                    "BLOCK_SIZE_1M_FILE_SIZE_1G_NR_FILES_10_READ_TYPE_read"
                )
            )
            self.assertNotIn(None, first_kw["fio_env"])

    def test_run_fio_benchmark_flags_validation_and_exception_handling(self):
        with patch.object(
            fio_benchmark_runner, "_BQ_SUPPORTED", True
        ), patch.object(
            fio_benchmark_runner, "bigquery", create=True
        ), patch.object(
            fio_benchmark_runner, "truncate_bq_table"
        ) as mock_trunc:
            # 1. Invalid env, invalid CLI flags, and partial BQ flags fail before truncate_bq_table
            with patch.dict(
                os.environ,
                {"DIRECT": "1", "NUMJOBS": "0", "NUM_JOBS": ""},
                clear=False,
            ), patch(
                "sys.argv",
                [
                    "run_fio_benchmark.py",
                    "--mount-path=/data",
                    "--fio-config=/read.fio",
                    "--project-id=p",
                    "--bq-dataset-id=d",
                    "--bq-table-id=t",
                ],
            ):
                with self.assertRaises(SystemExit) as cm:
                    run_fio_benchmark.main()
                self.assertEqual(cm.exception.code, 1)

            for bad_extra in (
                ["--iterations=0"],
                ["--numjobs=0"],
                ["--direct=2"],
                ["--bind-fio"],
            ):
                with patch(
                    "sys.argv",
                    [
                        "run_fio_benchmark.py",
                        "--mount-path=/data",
                        "--fio-config=/read.fio",
                        "--project-id=p",
                        "--bq-dataset-id=d",
                        "--bq-table-id=t",
                    ]
                    + bad_extra,
                ), patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as cm_flag:
                        run_fio_benchmark.main()
                    self.assertNotEqual(cm_flag.exception.code, 0)

            for partial_bq in (
                ["--bq-dataset-id=d"],
                ["--bq-table-id=t"],
                ["--project-id=p", "--bq-dataset-id=d"],
                ["--bq-dataset-id=d", "--bq-table-id=t"],
            ):
                with patch(
                    "sys.argv",
                    [
                        "run_fio_benchmark.py",
                        "--mount-path=/data",
                        "--fio-config=/read.fio",
                    ]
                    + partial_bq,
                ), patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as cm_partial:
                        run_fio_benchmark.main()
                    self.assertEqual(cm_partial.exception.code, 2)

            mock_trunc.assert_not_called()

        # 2. Valid --numjobs and --direct CLI flags succeed WITHOUT --project-id (and with --project-id alone)
        for extra_proj in ([], ["--project-id=p"]):
            with patch.object(
                fio_benchmark_runner, "run_benchmark"
            ) as mock_run_bench, patch.dict(
                os.environ,
                {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
                clear=False,
            ), patch(
                "sys.argv",
                [
                    "run_fio_benchmark.py",
                    "--mount-path=/data",
                    "--fio-config=/read.fio",
                    "--numjobs=48",
                    "--direct=0",
                ]
                + extra_proj,
            ):
                run_fio_benchmark.main()
            mock_run_bench.assert_called_once()
            self.assertEqual(
                mock_run_bench.call_args.kwargs["fio_env"],
                {"NUMJOBS": "48", "DIRECT": "0"},
            )
            self.assertIsNone(mock_run_bench.call_args.kwargs["bq_client"])

        # 3. RuntimeError from run_benchmark is logged and converted to SystemExit(1)
        with patch.object(
            fio_benchmark_runner,
            "run_benchmark",
            side_effect=RuntimeError("mount failed"),
        ), patch.dict(
            os.environ,
            {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""},
            clear=False,
        ), patch(
            "sys.argv",
            [
                "run_fio_benchmark.py",
                "--bucket-name=b",
                "--fio-config=/read.fio",
                "--project-id=p",
            ],
        ):
            with self.assertRaises(SystemExit) as cm_err:
                run_fio_benchmark.main()
            self.assertEqual(cm_err.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
