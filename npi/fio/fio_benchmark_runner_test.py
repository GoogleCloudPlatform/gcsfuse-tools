import unittest
from unittest.mock import patch, MagicMock
import os
import shutil
import shlex
import sys

# Add the directory containing fio_benchmark_runner.py to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fio_benchmark_runner

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
        with patch.dict(os.environ, {"DIRECT": "0", "NUMJOBS": "112"}, clear=False):
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
        env_without_direct = {k: v for k, v in os.environ.items() if k not in ("DIRECT", "NUMJOBS")}
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


if __name__ == '__main__':
    unittest.main()
