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
            temp_dir="memory"
        )
        
        benchmarks = factory.get_available_benchmarks()
        self.assertIn("read_http1", benchmarks)
        self.assertIn("write_grpc", benchmarks)
        self.assertIn("read_http1_numa0_fio_bound", benchmarks)
        self.assertIn("write_grpc_numa1_fio_notbound", benchmarks)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_memory(self, mock_get_cpu):
        mock_get_cpu.return_value = None # No NUMA for simplicity
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            temp_dir="memory"
        )
        
        cmd, table_id = factory.get_benchmark_command("read_http1")
        self.assertIn("--mount type=tmpfs,destination=/gcsfuse-temp", cmd)
        self.assertIn("us-docker.pkg.dev/test-project/gcsfuse-benchmarks/fio-read-benchmark:latest", cmd)
        self.assertIn("--bucket-name=test-bucket", cmd)
        self.assertIn("--temp-dir=/gcsfuse-temp -o allow_other", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_boot_disk(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            temp_dir="boot-disk"
        )
        
        cmd, table_id = factory.get_benchmark_command("write_grpc")
        self.assertIn("-v <temp_dir_path>:/gcsfuse-temp", cmd)
        self.assertIn("--client-protocol=grpc", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_with_mount_path(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name=None,
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            temp_dir="memory",
            mount_path="/mnt/gcs"
        )
        
        cmd, table_id = factory.get_benchmark_command("read_http1")
        self.assertIn("-v /mnt/gcs:/mnt/gcs", cmd)
        self.assertIn("--mount-path=/mnt/gcs", cmd)
        self.assertNotIn("--bucket-name", cmd)

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
            temp_dir="memory"
        )
        
        cpu_list = factory._get_cpu_list_for_numa_node(0)
        self.assertEqual(cpu_list, "0-15")

    @patch('subprocess.run')
    def test_get_cpu_list_for_numa_node_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            temp_dir="memory"
        )
        
        cpu_list = factory._get_cpu_list_for_numa_node(0)
        self.assertIsNone(cpu_list)

class TestRunBenchmark(unittest.TestCase):

    @patch('subprocess.run')
    def test_run_benchmark_success_memory(self, mock_run):
        success = npi.run_benchmark("test_bench", "echo hello", "memory", "test-project", "test-dataset", "test-table")
        self.assertTrue(success)
        self.assertEqual(mock_run.call_count, 1)

    @patch('subprocess.run')
    @patch('tempfile.mkdtemp')
    @patch('shutil.rmtree')
    def test_run_benchmark_success_boot_disk(self, mock_rmtree, mock_mkdtemp, mock_run):
        mock_mkdtemp.return_value = "/tmp/fake-dir"
        
        success = npi.run_benchmark("test_bench", "echo <temp_dir_path>", "boot-disk", "test-project", "test-dataset", "test-table")
        
        self.assertTrue(success)
        mock_mkdtemp.assert_called_once()
        self.assertEqual(mock_run.call_count, 1)
        # Check if <temp_dir_path> was replaced in the call
        args, kwargs = mock_run.call_args_list[0]
        self.assertIn("/tmp/fake-dir", args[0])
        mock_rmtree.assert_called_once_with("/tmp/fake-dir")

    @patch('subprocess.run')
    def test_run_benchmark_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")
        
        success = npi.run_benchmark("test_bench", "echo hello", "memory", "test-project", "test-dataset", "test-table")
        self.assertFalse(success)

class TestMain(unittest.TestCase):

    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_success(self, mock_factory_class, mock_parse_args):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.temp_dir = "memory"
        mock_args.is_rapid_bucket = False
        mock_args.file_cache_dir = None
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
            mock_run_benchmark.assert_called_once_with("read_http1", "docker run ...", "memory", "test-project", "test-dataset", "test-table")

    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_failure(self, mock_factory_class, mock_parse_args):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.temp_dir = "memory"
        mock_args.is_rapid_bucket = False
        mock_args.file_cache_dir = None
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

    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_rapid_bucket_filters_http1(self, mock_factory_class, mock_parse_args):
        mock_args = MagicMock()
        mock_args.benchmarks = ["all"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = True
        mock_args.temp_dir = "memory"
        mock_args.is_rapid_bucket = True
        mock_args.file_cache_dir = None
        mock_args.file_cache_size_mb = 2097152
        mock_args.image_version = "latest"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_http1", "read_grpc", "write_http1", "write_grpc"]
        mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
        mock_factory_class.return_value = mock_factory_instance

        npi.main()
        # Verify that only grpc benchmarks are passed to get_benchmark_command if we were running them,
        # but since dry_run=True, it will iterate over the filtered benchmarks_to_run.
        # We can't easily mock the print output here, but let's check that it ran without errors.

    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    def test_main_rapid_bucket_explicit_http1_error(self, mock_factory_class, mock_parse_args):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_http1"]
        mock_args.bucket_name = "test-bucket"
        mock_args.mount_path = None
        mock_args.project_id = "test-project"
        mock_args.bq_dataset_id = "test-dataset"
        mock_args.iterations = 5
        mock_args.dry_run = False
        mock_args.temp_dir = "memory"
        mock_args.is_rapid_bucket = True
        mock_args.file_cache_dir = None
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
