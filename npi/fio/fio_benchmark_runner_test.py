import unittest
from unittest.mock import patch, MagicMock
import os
import shutil
import shlex
import sys

# Add the directory containing fio_benchmark_runner.py to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fio_benchmark_runner
import run_fio_benchmark
import run_fio_matrix

class TestClearCacheDir(unittest.TestCase):

    @patch('os.path.exists')
    @patch('shutil.rmtree')
    def test_clear_cache_dir_success(self, mock_rmtree, mock_exists):
        mock_exists.return_value = True
        
        flags = "--cache-dir=/tmp/cache"
        fio_benchmark_runner.clear_cache_dir(flags)
        
        mock_rmtree.assert_called_once_with("/tmp/cache")

    @patch('os.path.exists')
    def test_clear_cache_dir_no_flag(self, mock_exists):
        fio_benchmark_runner.clear_cache_dir("--other-flag=value")
        mock_exists.assert_not_called()

    @patch('os.path.exists')
    def test_clear_cache_dir_not_exists(self, mock_exists):
        mock_exists.return_value = False
        fio_benchmark_runner.clear_cache_dir("--cache-dir=/tmp/cache")
        mock_exists.assert_called_once_with("/tmp/cache")


class TestFioTemplatesAndDirectEnv(unittest.TestCase):

    @patch('os.makedirs')
    @patch('fio_benchmark_runner.print_summary')
    @patch('fio_benchmark_runner.parse_fio_output', return_value=[])
    @patch('fio_benchmark_runner.run_fio_test')
    @patch('fio_benchmark_runner.precreate_benchmark_directories')
    @patch('fio_benchmark_runner.run_command')
    def test_run_benchmark_propagates_direct_env(
        self, mock_run_cmd, mock_precreate, mock_run_fio, mock_parse, mock_summary, mock_makedirs
    ):
        with patch.dict(os.environ, {"DIRECT": "0", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False):
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

    @patch('os.makedirs')
    @patch('fio_benchmark_runner.print_summary')
    @patch('fio_benchmark_runner.parse_fio_output', return_value=[])
    @patch('fio_benchmark_runner.run_fio_test')
    @patch('fio_benchmark_runner.precreate_benchmark_directories')
    @patch('fio_benchmark_runner.run_command')
    def test_run_benchmark_defaults_direct_to_1(
        self, mock_run_cmd, mock_precreate, mock_run_fio, mock_parse, mock_summary, mock_makedirs
    ):
        env_without_direct = {
            k: v for k, v in os.environ.items()
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

    def test_resolve_fio_params_fallbacks_and_validation(self):
        # Empty / whitespace cells fall back cleanly while preserving 0 for DIRECT
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params({"DIRECT": "", "NUMJOBS": "  "}, {"DIRECT": "", "NUMJOBS": ""}),
            ("1", "112"),
        )
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params({"DIRECT": "  ", "NUMJOBS": ""}, {"DIRECT": "0", "NUMJOBS": "48"}),
            ("0", "48"),
        )
        self.assertEqual(
            fio_benchmark_runner.resolve_fio_params({"DIRECT": 0, "NUM_JOBS": "16"}, {"DIRECT": "1", "NUMJOBS": "112"}),
            ("0", "16"),
        )
        for bad_overrides, bad_env in [
            ({"DIRECT": "2"}, {}),
            ({}, {"DIRECT": "yes"}),
            ({"DIRECT": True}, {}),
            ({"NUMJOBS": "0"}, {"NUMJOBS": "112"}),
            ({"NUMJOBS": "-4"}, {}),
            ({"NUMJOBS": "abc"}, {}),
            ({"NUMJOBS": ""}, {"NUMJOBS": "0"}),
        ]:
            with self.assertRaises(ValueError):
                fio_benchmark_runner.resolve_fio_params(bad_overrides, bad_env)

    @patch('os.makedirs')
    @patch('fio_benchmark_runner.subprocess.run')
    @patch('os.path.exists', return_value=True)
    def test_precreate_directories_legacy_num_jobs_and_rejects_invalid_before_delete(
        self, mock_exists, mock_subproc, mock_makedirs
    ):
        # 1. Invalid NUMJOBS fails BEFORE concurrent_delete.py (subprocess.run) executes
        with patch.dict(os.environ, {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False):
            with self.assertRaises(ValueError):
                fio_benchmark_runner.precreate_benchmark_directories(
                    "/mnt/data",
                    {"FILE_SIZE": "1G", "BLOCK_SIZE": "1M", "NR_FILES": "1", "NUMJOBS": "0"},
                    "/write.fio",
                )
        mock_subproc.assert_not_called()

        # 2. Legacy NUM_JOBS="4" creates exactly 4 job directories matching resolve_fio_params
        with patch.dict(os.environ, {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False):
            fio_benchmark_runner.precreate_benchmark_directories(
                "/mnt/data",
                {"FILE_SIZE": "1G", "BLOCK_SIZE": "1M", "NR_FILES": "1", "NUM_JOBS": "4"},
                "/write.fio",
            )
        mock_subproc.assert_called_once()
        # 1 nested_dir + 4 job_N dirs = 5 makedirs calls
        self.assertEqual(mock_makedirs.call_count, 5)

    @patch('fio_benchmark_runner.mount_gcsfuse')
    def test_run_benchmark_rejects_invalid_params_before_mounting(self, mock_mount):
        with patch.dict(os.environ, {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False):
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
        mock_mount.assert_not_called()


class TestRunFioMatrixAndBenchmarkEntrypoints(unittest.TestCase):

    def test_run_fio_matrix_rejects_invalid_params_before_bq_truncation(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES,NUMJOBS,DIRECT\nread,1G,1M,1,,\n")
            f.flush()
            with patch.object(fio_benchmark_runner, "_BQ_SUPPORTED", True), \
                 patch.object(fio_benchmark_runner, "bigquery", create=True) as mock_bq, \
                 patch.object(fio_benchmark_runner, "truncate_bq_table") as mock_trunc, \
                 patch.object(fio_benchmark_runner, "run_benchmark") as mock_run_bench:
                # 1. --numjobs=0 rejected by argparse
                with patch("sys.argv", [
                    "run_fio_matrix.py", "--mount-path=/data", "--fio-template=/read.fio",
                    f"--matrix-config={f.name}", "--project-id=p", "--bq-dataset-id=d",
                    "--bq-table-id=t", "--numjobs=0"
                ]):
                    with self.assertRaises(SystemExit) as cm:
                        run_fio_matrix.main()
                    self.assertNotEqual(cm.exception.code, 0)
                mock_trunc.assert_not_called()
                mock_bq.Client.assert_not_called()

                # 2. Invalid DIRECT in os.environ rejected before truncate_bq_table
                with patch.dict(os.environ, {"DIRECT": "invalid", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False), \
                     patch("sys.argv", [
                         "run_fio_matrix.py", "--mount-path=/data", "--fio-template=/read.fio",
                         f"--matrix-config={f.name}", "--project-id=p", "--bq-dataset-id=d",
                         "--bq-table-id=t"
                     ]):
                    with self.assertRaises(SystemExit) as cm2:
                        run_fio_matrix.main()
                    self.assertEqual(cm2.exception.code, 1)
                mock_trunc.assert_not_called()

                # 3. Blank CSV cells fall back cleanly to os.environ DIRECT=0, NUMJOBS=64
                with patch.dict(os.environ, {"DIRECT": "0", "NUMJOBS": "64", "NUM_JOBS": ""}, clear=False), \
                     patch("sys.argv", [
                         "run_fio_matrix.py", "--mount-path=/data", "--fio-template=/read.fio",
                         f"--matrix-config={f.name}"
                     ]):
                    run_fio_matrix.main()
                mock_run_bench.assert_called_once()
                _, kwargs = mock_run_bench.call_args
                self.assertEqual(kwargs["fio_env"]["DIRECT"], "0")
                self.assertEqual(kwargs["fio_env"]["NUMJOBS"], "64")

    def test_run_fio_matrix_handles_trailing_comma_extra_csv_fields(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv") as f:
            f.write("READ_TYPE,FILE_SIZE,BLOCK_SIZE,NR_FILES\nread,1G,1M,10,\n")
            f.flush()
            with patch.object(fio_benchmark_runner, "_BQ_SUPPORTED", True), \
                 patch.object(fio_benchmark_runner, "bigquery", create=True), \
                 patch.object(fio_benchmark_runner, "truncate_bq_table") as mock_trunc, \
                 patch.object(fio_benchmark_runner, "run_benchmark") as mock_run_bench, \
                 patch.dict(os.environ, {"DIRECT": "1", "NUMJOBS": "112", "NUM_JOBS": ""}, clear=False), \
                 patch("sys.argv", [
                     "run_fio_matrix.py", "--mount-path=/data", "--fio-template=/read.fio",
                     f"--matrix-config={f.name}", "--project-id=p", "--bq-dataset-id=d",
                     "--bq-table-id=t"
                 ]):
                run_fio_matrix.main()
            mock_trunc.assert_called_once()
            mock_run_bench.assert_called_once()
            _, kwargs = mock_run_bench.call_args
            self.assertTrue(
                kwargs["output_dir"].endswith("BLOCK_SIZE_1M_FILE_SIZE_1G_NR_FILES_10_READ_TYPE_read")
            )
            self.assertNotIn(None, kwargs["fio_env"])

    def test_run_fio_benchmark_rejects_invalid_env_before_bq_truncation(self):
        with patch.object(fio_benchmark_runner, "_BQ_SUPPORTED", True), \
             patch.object(fio_benchmark_runner, "bigquery", create=True), \
             patch.object(fio_benchmark_runner, "truncate_bq_table") as mock_trunc, \
             patch.dict(os.environ, {"DIRECT": "1", "NUMJOBS": "0", "NUM_JOBS": ""}, clear=False), \
             patch("sys.argv", [
                 "run_fio_benchmark.py", "--mount-path=/data", "--fio-config=/read.fio",
                 "--project-id=p", "--bq-dataset-id=d", "--bq-table-id=t"
             ]):
            with self.assertRaises(SystemExit) as cm:
                run_fio_benchmark.main()
            self.assertEqual(cm.exception.code, 1)
        mock_trunc.assert_not_called()


if __name__ == '__main__':
    unittest.main()
