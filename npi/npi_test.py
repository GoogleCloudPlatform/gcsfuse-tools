import unittest
from unittest.mock import patch, MagicMock
import subprocess
import npi

class TestBenchmarkFactory(unittest.TestCase):

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_init_and_get_available_benchmarks(self, mock_get_cpu):
        mock_get_cpu.side_effect = lambda node_id: "0-3" if node_id == 0 else "4-7"
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer"
        )
        
        benchmarks = factory.get_available_benchmarks()
        self.assertIn("read_http1", benchmarks)
        self.assertIn("write_grpc", benchmarks)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_standard(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer"
        )
        
        cmd, table_id = factory.get_benchmark_command("read_http1")
        self.assertIn("-v /mnt/buffer:/gcsfuse-buffer", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("us-docker.pkg.dev/test-project/gcsfuse-benchmarks/fio-read-benchmark:latest", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_file_cache(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            file_cache_size_mb=1024
        )
        
        cmd, table_id = factory.get_benchmark_command("read_file_cache_grpc")
        self.assertIn("-v /mnt/buffer:/gcsfuse-buffer", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("--file-cache-dir=/gcsfuse-buffer/file-cache", cmd)
        self.assertIn("--file-cache-max-size-mb=1024", cmd)

    @patch('subprocess.run')
    def test_get_cpu_list_for_numa_node_success(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout='{"lscpu": [{"field": "NUMA node0 CPU(s):", "data": "0-15"}]}'
        )
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer"
        )
        
        cpu_list = factory._get_cpu_list_for_numa_node(0)
        self.assertEqual(cpu_list, "0-15")

class TestRunBenchmark(unittest.TestCase):

    @patch('subprocess.run')
    def test_run_benchmark_success(self, mock_run):
        success = npi.run_benchmark("test_bench", "echo hello", "test-project", "test-dataset", "test-table")
        self.assertTrue(success)
        self.assertEqual(mock_run.call_count, 1)

    @patch('subprocess.run')
    def test_run_benchmark_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")
        success = npi.run_benchmark("test_bench", "echo hello", "test-project", "test-dataset", "test-table")
        self.assertFalse(success)

class TestMain(unittest.TestCase):

    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_success(self, mock_factory_class, mock_parse_args, mock_makedirs):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.is_rapid_bucket = False
        mock_args.buffer_mount_path = "/mnt/buffer"
        mock_args.file_cache_size_mb = 2097152
        mock_args.image_version = "latest"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_http1", "write_grpc"]
        mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
        mock_factory_class.return_value = mock_factory_instance

        with patch('npi.run_benchmark', return_value=True) as mock_run_benchmark:
            npi.main()
            mock_factory_class.assert_called_once()
            mock_run_benchmark.assert_called_once_with("read_http1", "docker run ...", "test-project", "test-dataset", "test-table")

    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_failure(self, mock_factory_class, mock_parse_args, mock_makedirs):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.is_rapid_bucket = False
        mock_args.buffer_mount_path = "/mnt/buffer"
        mock_args.file_cache_size_mb = 2097152
        mock_args.image_version = "latest"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_http1", "write_grpc"]
        mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
        mock_factory_class.return_value = mock_factory_instance

        with patch('npi.run_benchmark', return_value=False) as mock_run_benchmark:
            with self.assertRaises(SystemExit) as cm:
                npi.main()
            self.assertEqual(cm.exception.code, 1)

    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_rapid_bucket_filters_http1(self, mock_factory_class, mock_parse_args, mock_makedirs):
        mock_args = MagicMock()
        mock_args.benchmarks = ["all"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = True
        mock_args.is_rapid_bucket = True
        mock_args.buffer_mount_path = "/mnt/buffer"
        mock_args.file_cache_size_mb = 2097152
        mock_args.image_version = "latest"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_http1", "read_grpc", "write_http1", "write_grpc"]
        mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
        mock_factory_class.return_value = mock_factory_instance

        npi.main()

    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_rapid_bucket_explicit_http1_error(self, mock_factory_class, mock_parse_args, mock_makedirs):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.is_rapid_bucket = True
        mock_args.buffer_mount_path = "/mnt/buffer"
        mock_args.file_cache_size_mb = 2097152
        mock_args.image_version = "latest"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_http1", "read_grpc"]
        mock_factory_class.return_value = mock_factory_instance

        with self.assertRaises(SystemExit):
            npi.main()

if __name__ == '__main__':
    unittest.main()
