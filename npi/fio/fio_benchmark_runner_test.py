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

if __name__ == '__main__':
    unittest.main()
