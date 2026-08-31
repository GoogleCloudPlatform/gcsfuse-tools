import unittest
from unittest.mock import patch, MagicMock
import subprocess
import os
import getpass
import json
import npi
import query_results


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
        self.assertIn("go_read_http1", benchmarks)
        self.assertIn("go_read_grpc", benchmarks)

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
        self.assertIn("-e NUMJOBS=112", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("us-docker.pkg.dev/test-project/gcsfuse-benchmarks/fio-read-benchmark:latest", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_rapid_bucket(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            is_rapid_bucket=True
        )
        
        cmd, table_id = factory.get_benchmark_command("read_grpc")
        self.assertIn("-e NUMJOBS=48", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_numjobs_override(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            numjobs=64
        )
        
        cmd, table_id = factory.get_benchmark_command("read_http1")
        self.assertIn("-e NUMJOBS=64", cmd)

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
        self.assertEqual(table_id, "fio_read_file_cache")
        self.assertIn("-v /mnt/buffer:/gcsfuse-buffer", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("--cache-dir=/gcsfuse-buffer/file-cache", cmd)
        self.assertIn("--file-cache-max-size-mb=1024", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_go_read(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer"
        )
        
        cmd, table_id = factory.get_benchmark_command("go_read_http1")
        self.assertEqual(table_id, "go_client_read_http1")
        self.assertIn("us-docker.pkg.dev/test-project/gcsfuse-benchmarks/go-client-read-benchmark:latest", cmd)
        self.assertIn("--client-protocol=http1", cmd)

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
    @patch('npi.verify_permissions', return_value=True)
    def test_main_success(self, mock_verify_perms, mock_factory_class, mock_parse_args, mock_makedirs):
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
    @patch('npi.verify_permissions', return_value=True)
    def test_main_failure(self, mock_verify_perms, mock_factory_class, mock_parse_args, mock_makedirs):
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

    @patch('shutil.rmtree')
    @patch('os.unlink')
    @patch('os.listdir')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    @patch('npi.verify_permissions', return_value=True)
    def test_main_clears_buffer_mount_path_when_not_empty(self, mock_verify_perms, mock_factory_class, mock_parse_args, mock_makedirs, mock_exists, mock_listdir, mock_unlink, mock_rmtree):
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

        mock_exists.return_value = True
        mock_listdir.return_value = ["file1.txt", "dir1"]
        
        with patch('os.path.isfile', side_effect=lambda p: "file1.txt" in p), \
             patch('os.path.islink', return_value=False), \
             patch('os.path.isdir', side_effect=lambda p: "dir1" in p):
            
            mock_factory_instance = MagicMock()
            mock_factory_instance.get_available_benchmarks.return_value = ["read_http1"]
            mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
            mock_factory_class.return_value = mock_factory_instance
            
            with patch('npi.run_benchmark', return_value=True):
                npi.main()
                
                mock_unlink.assert_called_once_with("/mnt/buffer/file1.txt")
                mock_rmtree.assert_called_once_with("/mnt/buffer/dir1")


class TestVerifyPermissions(unittest.TestCase):

    @patch('urllib.request.urlopen')
    @patch('subprocess.run')
    def test_verify_permissions_success(self, mock_run, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"https://www.googleapis.com/auth/cloud-platform\n"
        mock_urlopen.return_value.__enter__.return_value = mock_response
        mock_run.return_value = MagicMock(returncode=0)
        ok = npi.verify_permissions("test-project", "test-dataset", "test-bucket")
        self.assertTrue(ok)

    @patch('urllib.request.urlopen')
    @patch('subprocess.run')
    def test_verify_permissions_failure(self, mock_run, mock_urlopen):
        mock_urlopen.side_effect = Exception("Metadata server unreachable")
        mock_run.return_value = MagicMock(returncode=1, stderr="Permission denied")
        ok = npi.verify_permissions("test-project", "test-dataset", "test-bucket")
        self.assertFalse(ok)


import npi_orchestrator

class TestOrchestrator(unittest.TestCase):

    def test_benchmark_expansion_and_rapid_filtering(self):
        target = {
            "name": "rapid_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "gs://rapid-bucket",
            "dataset": "test_dataset",
            "buffer_mount": "/mnt/buffer",
            "is_rapid_bucket": True,
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "all"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False

        state = {"rapid_target": {"status": "PENDING"}}
        state_lock = MagicMock()

        captured_cmds = []

        def mock_ssh(socket_path, vm_name, zone, cmd, timeout=60):
            captured_cmds.append(cmd)
            return (0, "", "")

        with patch('npi_orchestrator.cleanup_remote_run'), \
             patch('npi_orchestrator.prep_vm'), \
             patch('npi_orchestrator.run_ssh_cmd', side_effect=mock_ssh), \
             patch('npi_orchestrator.monitor_run'):
            npi_orchestrator.execute_target(target, args, state_lock, state)
            
            # Check the triggered benchmark command contains active benchmarks read_grpc, write_grpc, host_info
            triggered = [c for c in captured_cmds if "npi.py" in c]
            self.assertEqual(len(triggered), 1)
            self.assertIn("read_grpc", triggered[0])
            self.assertIn("write_grpc", triggered[0])
            self.assertIn("host_info", triggered[0])
            self.assertNotIn("read_http1", triggered[0])
            self.assertNotIn("write_http1", triggered[0])

    def test_dataset_suffixing_zonal_and_regional(self):
        rapid_target = {
            "name": "t1", "type": "gce", "vm_name": "v1", "zone": "z1", "bucket": "b1",
            "dataset": "my_dataset_regional", "buffer_mount": "/mnt/buffer", "is_rapid_bucket": True
        }
        reg_target = {
            "name": "t2", "type": "gce", "vm_name": "v2", "zone": "z2", "bucket": "b2",
            "dataset": "my_dataset_zonal", "buffer_mount": "/mnt/buffer", "is_rapid_bucket": False
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False

        state = {"t1": {"status": "PENDING"}, "t2": {"status": "PENDING"}}
        state_lock = MagicMock()

        captured_cmds = []

        def mock_ssh(socket_path, vm_name, zone, cmd, timeout=60):
            captured_cmds.append(cmd)
            return (0, "", "")

        with patch('npi_orchestrator.cleanup_remote_run'), \
             patch('npi_orchestrator.prep_vm'), \
             patch('npi_orchestrator.run_ssh_cmd', side_effect=mock_ssh), \
             patch('npi_orchestrator.monitor_run'):
            
            npi_orchestrator.execute_target(rapid_target, args, state_lock, state)
            npi_orchestrator.execute_target(reg_target, args, state_lock, state)

            triggered = [c for c in captured_cmds if "npi.py" in c]
            self.assertEqual(len(triggered), 2)
            self.assertIn("--bq-dataset-id my_dataset_zonal", triggered[0])
            self.assertIn("--bq-dataset-id my_dataset_regional", triggered[1])

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_validate_gke_nodes_local(self, mock_sub_run, mock_which):
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Cluster credentials fetched"),
            MagicMock(returncode=0, stdout="node1 node2"),
            MagicMock(returncode=0, stdout="tpu-node1")
        ]
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": True
        }
        npi_orchestrator.validate_gke_nodes(target)
        self.assertEqual(mock_sub_run.call_count, 3)
        cmd1 = mock_sub_run.call_args_list[0][0][0]
        self.assertEqual(cmd1[0], "gcloud")
        self.assertEqual(cmd1[1], "container")
        self.assertEqual(cmd1[2], "clusters")
        self.assertEqual(cmd1[3], "get-credentials")


class TestSSHUserResolution(unittest.TestCase):

    @patch.dict(os.environ, {"SSH_USER": "explicit_user"}, clear=False)
    def test_resolve_ssh_user_from_env(self):
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "explicit_user")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_from_gcloud_account(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="kislayk@google.com\n")
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "kislayk_google_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_from_gcloud_complex_email(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="john.doe.dev@company.corp.com\n")
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "john_doe_dev_company_corp_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_with_plus_tag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="user+tag@domain.com\n")
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "user+tag_domain_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_gcloud_error_fallback(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        with patch('os.environ.get', side_effect=lambda k, default=None: "localuser" if k in ("USER", "USERNAME") else default), \
             patch('getpass.getuser', return_value="localuser"):
            resolved = npi_orchestrator.resolve_ssh_user()
            self.assertEqual(resolved, "localuser_google_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_gcloud_unset_fallback(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="(unset)\n")
        with patch('os.environ.get', side_effect=lambda k, default=None: "myuser_google_com" if k in ("USER", "USERNAME") else default), \
             patch('getpass.getuser', return_value="myuser_google_com"):
            resolved = npi_orchestrator.resolve_ssh_user()
            self.assertEqual(resolved, "myuser_google_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_multiline_gcloud_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="WARNING: Updates are available for gcloud.\nJohn.Doe@google.com\n"
        )
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "john_doe_google_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_uppercase_email(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="User.Name@Domain.Com\n")
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "user_name_domain_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_local_fallback_with_dots(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        with patch.dict(os.environ, {"USER": "john.doe"}, clear=True):
            resolved = npi_orchestrator.resolve_ssh_user()
            self.assertEqual(resolved, "john_doe_google_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_key_error_fallback(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        with patch('getpass.getuser', side_effect=KeyError("getpwuid(): uid not found")):
            resolved = npi_orchestrator.resolve_ssh_user()
            self.assertEqual(resolved, "user_google_com")


class TestSelfHealingPreflight(unittest.TestCase):

    @patch('npi_orchestrator.sync_file_to_remote')
    @patch('npi_orchestrator.run_ssh_cmd')
    @patch('npi_orchestrator.detect_remote_raid0_mount', return_value=None)
    def test_prep_vm_syncs_and_executes_prep_vm_sh(self, mock_detect, mock_ssh, mock_sync):
        mock_ssh.return_value = (0, "", "")
        target = {
            "name": "test_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "buffer_mount": "/mnt/lssd"
        }
        socket_path = "/tmp/test.sock"

        npi_orchestrator.prep_vm(target, socket_path)

        # Verify prep_vm.sh was synced
        synced_files = [call_args[0][3] for call_args in mock_sync.call_args_list]
        self.assertTrue(any("prep_vm.sh" in f for f in synced_files))

        # Verify prep_vm.sh was executed on remote VM
        ssh_cmds = [call_args[0][3] for call_args in mock_ssh.call_args_list]
        self.assertTrue(any("bash ~/gcsfuse-tools/npi/prep_vm.sh gce /mnt/lssd" in cmd for cmd in ssh_cmds))

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_validate_gke_nodes_missing_kubectl(self, mock_sub_run, mock_which):
        # Simulate kubectl missing even after prep_vm.sh attempt
        mock_which.side_effect = lambda cmd: None if cmd == "kubectl" else "/usr/bin/gke-gcloud-auth-plugin"
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a"
        }
        with self.assertRaises(RuntimeError) as cm:
            npi_orchestrator.validate_gke_nodes(target)
        self.assertIn("kubectl", str(cm.exception))

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_validate_gke_nodes_missing_auth_plugin(self, mock_sub_run, mock_which):
        # Simulate auth plugin missing even after prep_vm.sh attempt
        mock_which.side_effect = lambda cmd: "/usr/bin/kubectl" if cmd == "kubectl" else None
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a"
        }
        with self.assertRaises(RuntimeError) as cm:
            npi_orchestrator.validate_gke_nodes(target)
        self.assertIn("gke-gcloud-auth-plugin", str(cm.exception))

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_validate_gke_nodes_success(self, mock_sub_run, mock_which):
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Credentials fetched"),
            MagicMock(returncode=0, stdout="node1"),
            MagicMock(returncode=0, stdout="tpu-node1")
        ]
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": True
        }
        npi_orchestrator.validate_gke_nodes(target)
        self.assertEqual(mock_sub_run.call_count, 3)


class TestMilestone3Remediation(unittest.TestCase):
    """Unit tests for the 8 specific Milestone 3 remediation items."""

    def test_go_version_parsing_logic(self):
        """Item 1: Test Go version extraction supporting Go >= 2.0 and prerelease tags."""
        test_cases = [
            ("go version go1.24rc1 linux/amd64", False),
            ("go version go2.0.0 linux/amd64", False),
            ("go version go1.25.1 linux/amd64", False),
            ("go version go1.24.0 linux/amd64", False),
            ("go version go1.23.5 linux/amd64", True),
            ("go version devel linux/amd64", True),
        ]
        bash_template = """
        GO_VER_STR=$(echo "{version_line}" | awk '{{print $3}}' | sed 's/go//')
        MAJOR=$(echo "$GO_VER_STR" | cut -d. -f1 | sed 's/[^0-9].*//')
        MINOR_NUM=$(echo "$GO_VER_STR" | cut -d. -f2 | sed 's/[^0-9].*//')
        NEED_GO_INSTALL=true
        if [ "$MAJOR" -gt 1 ] 2>/dev/null || {{ [ "$MAJOR" -eq 1 ] 2>/dev/null && [ "$MINOR_NUM" -ge 24 ] 2>/dev/null; }}; then
          NEED_GO_INSTALL=false
        fi
        echo "$NEED_GO_INSTALL"
        """
        for ver_str, expected_need_install in test_cases:
            script = bash_template.format(version_line=ver_str)
            res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
            output = res.stdout.strip()
            expected_str = "true" if expected_need_install else "false"
            self.assertEqual(output, expected_str, f"Failed for version: {ver_str}")

    def test_prep_vm_user_expansion(self):
        """Item 2: Verify prep_vm.sh uses ${USER:-$(whoami)}."""
        prep_path = os.path.join(npi_orchestrator.REPO_DIR, "prep_vm.sh")
        with open(prep_path, "r") as f:
            content = f.read()
        self.assertIn("${USER:-$(whoami)}", content)

    def test_run_conformance_goimports_quoted(self):
        """Item 3: Verify $HOME/go/bin/goimports is quoted in run_conformance.sh."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            content = f.read()
        self.assertIn('"$HOME/go/bin/goimports"', content)

    def test_go_download_tarball_integrity_check(self):
        """Item 4: Verify tarball integrity checks exist before rm -rf /usr/local/go."""
        for filename in ["prep_vm.sh", "run_conformance.sh"]:
            filepath = os.path.join(npi_orchestrator.REPO_DIR, filename)
            with open(filepath, "r") as f:
                content = f.read()
            self.assertIn("tar -tzf", content)

    def test_run_conformance_cd_failure_writes_conformance_exit(self):
        """Item 5: Verify cd TARGET_DIR failure handling in run_conformance.sh."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            content = f.read()
        self.assertIn('TARGET_DIR="${GCSFUSE_DIR:-$HOME/gcsfuse}"', content)
        self.assertIn('cd "$TARGET_DIR"', content)
        self.assertIn("echo 1 > ~/conformance.exit", content)

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_multiline_df_output(self, mock_ssh):
        """Item 6: Test get_disk_utilization with multiline wrapped df -P output."""
        multiline_df_output = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/mapper/vg00-lv_very_long_device_name_here\n"
            "                 104857600  41943040  62914560      40% /mnt/lssd\n"
        )
        mock_ssh.return_value = (0, multiline_df_output, "")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/lssd")
        self.assertEqual(util, 40)

    @patch.dict(os.environ, {"USER": "  john.doe  \n"}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_strips_whitespace_from_user(self, mock_run):
        """Item 7: Test resolve_ssh_user strips whitespace from USER environment variable."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "john_doe_google_com")

    @patch('shutil.which', side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch('subprocess.run')
    def test_validate_gke_nodes_is_tpu_string_false(self, mock_sub_run, mock_which):
        """Item 8: Test is_tpu: 'false' string truthiness in validate_gke_nodes."""
        # 0 TPU nodes, 1 CPU node
        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Credentials fetched"),
            MagicMock(returncode=0, stdout="cpu-node-1"),
            MagicMock(returncode=0, stdout="")
        ]
        target_false = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": "false"
        }
        # Should NOT raise error because is_tpu is False
        npi_orchestrator.validate_gke_nodes(target_false)

        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Credentials fetched"),
            MagicMock(returncode=0, stdout="cpu-node-1"),
            MagicMock(returncode=0, stdout="")
        ]
        target_true = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": "true"
        }
        # Should raise error because is_tpu is True and tpu_count is 0
        with self.assertRaises(RuntimeError) as cm:
            npi_orchestrator.validate_gke_nodes(target_true)
        self.assertIn("at least one TPU node", str(cm.exception))

    @patch.dict(os.environ, {"SSH_USER": "  explicit_user_with_space \n\t "}, clear=False)
    def test_resolve_ssh_user_strips_env_whitespace(self):
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "explicit_user_with_space")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_ignores_gcloud_warning_lines_with_at(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="WARNING: account admin@company.com is deprecated, please re-authenticate\nuser.name@domain.com\n"
        )
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "user_name_domain_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_local_fallback_with_email(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        with patch.dict(os.environ, {"USER": "alice.bob@company.com"}, clear=True):
            resolved = npi_orchestrator.resolve_ssh_user()
            self.assertEqual(resolved, "alice_bob_company_com")
            self.assertNotIn("@", resolved)

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_log_functions_use_shlex_quote(self, mock_ssh):
        mock_ssh.return_value = (0, "line", "")
        npi_orchestrator.get_last_log_line("sock", "vm", "zone", "/tmp/path with spaces; rm -rf /")
        self.assertIn("'/tmp/path with spaces; rm -rf /'", mock_ssh.call_args[0][3])

        mock_ssh.return_value = (0, "100 200", "")
        npi_orchestrator.get_log_file_stat("sock", "vm", "zone", "/tmp/path with spaces; rm -rf /")
        self.assertIn("'/tmp/path with spaces; rm -rf /'", mock_ssh.call_args[0][3])

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_ssh_error(self, mock_ssh):
        mock_ssh.return_value = (255, "", "SSH connection failed")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/lssd")
        self.assertEqual(util, -1)

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_mount_path_has_percent(self, mock_ssh):
        df_output = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/sda1 10485760 4194304 6291456 40% /mnt/buffer_90%\n"
        )
        mock_ssh.return_value = (0, df_output, "")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/buffer_90%")
        self.assertEqual(util, 40)

    @patch('shutil.which', side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch('subprocess.run')
    def test_validate_gke_nodes_timeout(self, mock_run, mock_which):
        mock_run.side_effect = subprocess.TimeoutExpired("gcloud", 30)
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a"
        }
        with self.assertRaises(RuntimeError) as cm:
            npi_orchestrator.validate_gke_nodes(target)
        self.assertIn("failed to get credentials", str(cm.exception).lower())


class TestWorkerM34Fixes(unittest.TestCase):
    """Unit tests covering the 6 specific edge-case fixes implemented by Worker M3_4."""

    @patch.dict(os.environ, {"USER": "localuser"}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_strict_gcloud_email(self, mock_run):
        # Multi-word output containing email should be rejected by strict regex
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Your active account is: user@example.com\n"
        )
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "localuser_google_com")

        # Line with warning text before email should be rejected
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="WARNING: account user@example.com active\n  valid.user@domain.com  \n"
        )
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "valid_user_domain_com")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_resolve_ssh_user_fallback_non_google_emails(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gcloud")
        # Non-Google email with @ symbol -> replace @ and . with _, do NOT append _google_com
        with patch.dict(os.environ, {"USER": "alice.bob@company.com"}, clear=True):
            self.assertEqual(npi_orchestrator.resolve_ssh_user(), "alice_bob_company_com")
        
        # Local unix username -> append _google_com
        with patch.dict(os.environ, {"USER": "bob"}, clear=True):
            self.assertEqual(npi_orchestrator.resolve_ssh_user(), "bob_google_com")

        # Username already ending with _google_com -> do not double append
        with patch.dict(os.environ, {"USER": "charlie_google_com"}, clear=True):
            self.assertEqual(npi_orchestrator.resolve_ssh_user(), "charlie_google_com")

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_header_only(self, mock_ssh):
        # Header line only (no data rows) -> return -1
        mock_ssh.return_value = (0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n", "")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/lssd")
        self.assertEqual(util, -1)

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_percent_starts_with_slash(self, mock_ssh):
        # Token starting with / ending with % -> return -1 if no valid token
        mock_ssh.return_value = (0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 50 50 /50% /mnt\n", "")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/lssd")
        self.assertEqual(util, -1)

    @patch('shutil.which', side_effect=lambda cmd: f"/usr/bin/{cmd}")
    @patch('subprocess.run')
    def test_validate_gke_nodes_filter_stdout(self, mock_sub_run, mock_which):
        # out_cpu contains error/warning strings -> counted as 0 valid node names
        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Credentials fetched"),
            MagicMock(returncode=0, stdout="No resources found"),
            MagicMock(returncode=0, stdout="")
        ]
        target = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": False
        }
        with self.assertRaises(RuntimeError) as cm:
            npi_orchestrator.validate_gke_nodes(target)
        self.assertIn("at least one CPU compute node", str(cm.exception))

        # out_cpu has valid node names
        mock_sub_run.side_effect = [
            MagicMock(returncode=0, stdout="Credentials fetched"),
            MagicMock(returncode=0, stdout="gke-node-1 gke-node-2"),
            MagicMock(returncode=0, stdout="gke-tpu-node-1")
        ]
        target_valid = {
            "name": "gke_target",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "is_tpu": True
        }
        npi_orchestrator.validate_gke_nodes(target_valid)

    def test_run_conformance_cleanup_process_matching_and_signals(self):
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            content = f.read()
        self.assertIn("kill_tree() {", content)
        self.assertIn("kill_tree \"$MAKE_PID\"", content)
        self.assertIn("pkill -9 -P $$ 2>/dev/null || true", content)
        self.assertIn("pkill -9 -x gcsfuse 2>/dev/null || true", content)
        self.assertIn("awk '{print $2}' /proc/mounts", content)
        self.assertIn("sort -r", content)
        self.assertIn("printf '%b", content)
        self.assertIn('fusermount -u "$m_decoded" 2>/dev/null || umount -l "$m_decoded" 2>/dev/null || true', content)
        self.assertIn("trap cleanup EXIT", content)
        self.assertIn("trap 'exit 1' INT TERM", content)

    def test_prep_vm_apt_get_fallback(self):
        prep_path = os.path.join(npi_orchestrator.REPO_DIR, "prep_vm.sh")
        with open(prep_path, "r") as f:
            content = f.read()
        self.assertIn("sudo apt-get install -y kubectl google-cloud-sdk-gke-gcloud-auth-plugin 2>/dev/null || true", content)


class TestGKENodeNameValidation(unittest.TestCase):

    def test_is_valid_gke_node_name(self):
        from npi_orchestrator import is_valid_gke_node_name

        # 1. Node pool names containing "resource" are valid
        self.assertTrue(is_valid_gke_node_name("gke-cluster-resource-pool-ab12cd34"))

        # 2. "No resources found in default namespace." tokens are invalid
        for token in "No resources found in default namespace.".split():
            self.assertFalse(is_valid_gke_node_name(token), f"Token should be invalid: {token}")

        # 3. "WARNING: The v1beta1 API is deprecated" tokens are invalid
        for token in "WARNING: The v1beta1 API is deprecated".split():
            self.assertFalse(is_valid_gke_node_name(token), f"Token should be invalid: {token}")

        # 4. "Unable to connect to the server: TLS handshake timeout" tokens are invalid
        for token in "Unable to connect to the server: TLS handshake timeout".split():
            self.assertFalse(is_valid_gke_node_name(token), f"Token should be invalid: {token}")

    def test_count_gke_nodes(self):
        import json
        from npi_orchestrator import count_gke_nodes

        self.assertEqual(count_gke_nodes("No nodes found in default namespace."), 0)
        self.assertEqual(count_gke_nodes("No resources found in prod namespace."), 0)
        self.assertEqual(count_gke_nodes("Error from server (NotFound): nodes not found"), 0)
        self.assertEqual(count_gke_nodes(json.dumps({"items": [{"metadata": {"name": "gke-node-1"}}]})), 1)
        self.assertEqual(count_gke_nodes("gke-node-1 gke-node-2"), 2)
        self.assertEqual(count_gke_nodes("gke-cluster-resource-pool-ab12cd34"), 1)
        self.assertEqual(count_gke_nodes("WARNING: gke-gcloud-auth-plugin is deprecated\ngke-node-1 gke-node-2"), 2)
        self.assertEqual(count_gke_nodes("nodes not found"), 0)

    def test_count_gke_nodes_tabular(self):
        from npi_orchestrator import count_gke_nodes
        tabular_output = "NAME STATUS ROLES AGE VERSION \n node-1 Ready <none> 5d v1.28.3"
        self.assertEqual(count_gke_nodes(tabular_output), 1)

    def test_count_gke_nodes_case_insensitive_warning(self):
        from npi_orchestrator import count_gke_nodes
        warning_output = "Warning: gke-gcloud-auth-plugin is deprecated \n gke-node-1 gke-node-2"
        self.assertEqual(count_gke_nodes(warning_output), 2)

    def test_is_valid_gke_node_name_specific(self):
        from npi_orchestrator import is_valid_gke_node_name
        self.assertFalse(is_valid_gke_node_name("not"))
        self.assertTrue(is_valid_gke_node_name("gke-node-1"))


class TestWorkerM310Remediation(unittest.TestCase):
    """Explicit unit tests verifying Worker M3_10 defect remediation guarantees."""

    def test_run_conformance_kill_tree_and_mounts(self):
        """Verify kill_tree function and /proc/mounts lookup in run_conformance.sh."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            content = f.read()
        self.assertIn("kill_tree() {", content)
        self.assertIn("kill_tree \"$MAKE_PID\"", content)
        self.assertIn("awk '{print $2}' /proc/mounts", content)
        self.assertIn('fusermount -u "$m_decoded" 2>/dev/null || umount -l "$m_decoded" 2>/dev/null || true', content)


class TestWorkerM311Remediation(unittest.TestCase):
    """Explicit unit tests verifying Worker M3_11 defect remediation guarantees for run_conformance.sh cleanup unmount loop."""

    def test_unmount_loop_sorting_decoding_and_pwd_skip(self):
        test_script = """
        REAL_PWD="/tmp/gcsfuse/test_dir"
        MOUNTS=(
            "/tmp/gcsfuse/parent"
            "/tmp/gcsfuse/parent/child"
            "/tmp/gcsfuse/path\\040with\\040spaces"
            "/tmp/gcsfuse/test_dir"
            "/tmp/gcsfuse/test_dir/subdir"
        )
        
        UNMOUNTED=()
        SKIPPED=()
        
        SORTED_MOUNTS=$(printf "%s\n" "${MOUNTS[@]}" | sort -r)
        
        for m in $SORTED_MOUNTS; do
            m_decoded=$(printf '%b\n' "$m")
            if [ "$m_decoded" = "$REAL_PWD" ] || [[ "$REAL_PWD" == "$m_decoded"/* ]]; then
                SKIPPED+=("$m_decoded")
                continue
            fi
            UNMOUNTED+=("$m_decoded")
        done
        
        echo "UNMOUNTED: ${UNMOUNTED[*]}"
        echo "SKIPPED: ${SKIPPED[*]}"
        """
        res = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=True)
        stdout = res.stdout
        
        self.assertIn("UNMOUNTED: /tmp/gcsfuse/test_dir/subdir /tmp/gcsfuse/path with spaces /tmp/gcsfuse/parent/child /tmp/gcsfuse/parent", stdout)
        self.assertIn("SKIPPED: /tmp/gcsfuse/test_dir", stdout)


    def test_run_conformance_go_binary_path_check(self):
        """Verify command -v go check before falling back to /usr/local/go/bin/go."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            content = f.read()
        self.assertIn("command -v go", content)
        self.assertIn("/usr/local/go/bin/go", content)

    @patch.dict(os.environ, {"SSH_USER": "  Test.User@Domain.Com \n"}, clear=False)
    def test_resolve_ssh_user_sanitizes_env(self):
        """Verify resolve_ssh_user sanitizes SSH_USER env var."""
        resolved = npi_orchestrator.resolve_ssh_user()
        self.assertEqual(resolved, "test_user_domain_com")

    @patch('npi_orchestrator.run_ssh_cmd')
    def test_get_disk_utilization_float_capacity_and_device_name_percent(self, mock_ssh):
        """Verify get_disk_utilization supports float capacity and ignores device name ending in %."""
        fake_df_output = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "100% 104857600 45000000 59857600 43.5% /mnt/lssd\n"
        )
        mock_ssh.return_value = (0, fake_df_output, "")
        util = npi_orchestrator.get_disk_utilization("sock", "vm", "zone", "/mnt/lssd")
        self.assertEqual(util, 43)

    def test_count_gke_nodes_kubectl_o_name_and_json_prefix_and_tabular(self):
        """Verify count_gke_nodes handles -o name, JSON prefix log, and tabular parsing."""
        # 1. -o name output with node/ prefix
        name_out = "node/gke-node-1\nnode/gke-node-2"
        self.assertEqual(npi_orchestrator.count_gke_nodes(name_out), 2)

        # 2. JSON output preceded by info log
        json_out = '[INFO] Log line...\n{"items": [{"metadata": {"name": "node1"}}, {"metadata": {"name": "node2"}}]}'
        self.assertEqual(npi_orchestrator.count_gke_nodes(json_out), 2)

        # 3. Tabular output ignores NAME line and evaluates tokens[0]
        tab_out = "NAME STATUS ROLES AGE VERSION\ngke-node-1 Ready <none> 5d v1.28.3"
        self.assertEqual(npi_orchestrator.count_gke_nodes(tab_out), 1)

    def test_is_valid_gke_node_name_edge_case_filters(self):
        """Verify is_valid_gke_node_name enforces hyphen, dot, or digit and filters non-node tokens."""
        self.assertTrue(npi_orchestrator.is_valid_gke_node_name("gke-node-1"))
        self.assertFalse(npi_orchestrator.is_valid_gke_node_name("v1.28.3-gke.1"))
        self.assertFalse(npi_orchestrator.is_valid_gke_node_name("5d"))
        self.assertFalse(npi_orchestrator.is_valid_gke_node_name("amd64"))
        self.assertFalse(npi_orchestrator.is_valid_gke_node_name("Ready-1"))
        self.assertFalse(npi_orchestrator.is_valid_gke_node_name("10.240.0.5"))


class TestWorkerM42Remediation(unittest.TestCase):
    """Explicit unit tests verifying Worker M4_2 defect remediation guarantees."""

    def test_run_conformance_initializes_exit_code_zero(self):
        """Verify run_conformance.sh explicitly initializes EXIT_CODE=0 near top of script."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        with open(run_conf_path, "r") as f:
            lines = f.readlines()
        
        header_lines = [line.strip() for line in lines[:10]]
        self.assertIn("EXIT_CODE=0", header_lines, "EXIT_CODE=0 must be initialized near top of run_conformance.sh")

    def test_run_conformance_exit_code_env_bleed_prevention(self):
        """Verify bash execution of run_conformance.sh top-level logic resets inherited EXIT_CODE environment variable."""
        run_conf_path = os.path.join(npi_orchestrator.REPO_DIR, "run_conformance.sh")
        cmd = f'EXIT_CODE=1 bash -c \'eval "$({os.environ.get("SHELL", "/bin/bash")} -c "head -n 6 {run_conf_path}")"; echo "$EXIT_CODE"\''
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        self.assertEqual(res.stdout.strip(), "0")


class TestWorkerM43Fixes(unittest.TestCase):
    """Explicit unit tests verifying Worker M4_3 defect remediation guarantees."""

    @patch.dict(os.environ, {"PROJECT_ID": "custom-env-project"}, clear=False)
    def test_get_gcp_project_id_env_override(self):
        """Verify get_gcp_project_id prefers PROJECT_ID environment variable."""
        self.assertEqual(npi_orchestrator.get_gcp_project_id(), "custom-env-project")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_get_gcp_project_id_gcloud_config(self, mock_run):
        """Verify get_gcp_project_id falls back to gcloud config when env var is unset."""
        mock_run.return_value = MagicMock(returncode=0, stdout="gcloud-project-789\n")
        self.assertEqual(npi_orchestrator.get_gcp_project_id(), "gcloud-project-789")

    @patch.dict(os.environ, {}, clear=True)
    @patch('subprocess.run')
    def test_get_gcp_project_id_default_fallback(self, mock_run):
        """Verify get_gcp_project_id falls back to DEFAULT_PROJECT_ID when gcloud config is unset or fails."""
        mock_run.return_value = MagicMock(returncode=0, stdout="(unset)\n")
        self.assertEqual(npi_orchestrator.get_gcp_project_id(), npi_orchestrator.DEFAULT_PROJECT_ID)
        self.assertEqual(npi_orchestrator.get_gcp_project_id(), "gcsfuse-npi")

    def test_raid0_script_early_mount_check_presence(self):
        """Verify raid0-script.sh contains early mount check right after set -e."""
        raid0_path = os.path.join(npi_orchestrator.REPO_DIR, "raid0-script.sh")
        with open(raid0_path, "r") as f:
            content = f.read()
        self.assertIn('MOUNT_POINT="${MOUNT_POINT:-', content)
        self.assertIn('mountpoint -q "$MOUNT_POINT" 2>/dev/null', content)
        self.assertIn('Buffer mountpoint $MOUNT_POINT is already mounted. Nothing to do.', content)


class TestValidateColocation(unittest.TestCase):
    """Unit tests for validate_colocation function in npi_orchestrator."""

    @patch('subprocess.run')
    def test_validate_colocation_success_regional(self, mock_run):
        mock_meta = {
            "hierarchicalNamespace": {"enabled": True},
            "location": "us-central1",
            "locationType": "region"
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_meta))
        target = {"bucket": "gs://test-bucket", "zone": "us-central1-a", "is_rapid_bucket": False}
        npi_orchestrator.validate_colocation(target, "test-project")

    @patch('subprocess.run')
    def test_validate_colocation_failure_hns_disabled(self, mock_run):
        mock_meta = {
            "hierarchicalNamespace": {"enabled": False},
            "location": "us-central1",
            "locationType": "region"
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_meta))
        target = {"bucket": "gs://test-bucket", "zone": "us-central1-a", "is_rapid_bucket": False}
        with self.assertRaises(ValueError) as ctx:
            npi_orchestrator.validate_colocation(target, "test-project")
        self.assertIn("HNS", str(ctx.exception))

    @patch('subprocess.run')
    def test_validate_colocation_success_rapid(self, mock_run):
        mock_meta = {
            "hierarchicalNamespace": {"enabled": True},
            "locationType": "zone",
            "dataLocations": ["us-central1-a"]
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_meta))
        target = {"bucket": "gs://rapid-bucket", "zone": "us-central1-a", "is_rapid_bucket": True}
        npi_orchestrator.validate_colocation(target, "test-project")

    @patch('subprocess.run')
    def test_validate_colocation_failure_rapid_zone_mismatch(self, mock_run):
        mock_meta = {
            "hierarchicalNamespace": {"enabled": True},
            "locationType": "zone",
            "dataLocations": ["us-central1-a"]
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_meta))
        target = {"bucket": "gs://rapid-bucket", "zone": "us-central1-b", "is_rapid_bucket": True}
        with self.assertRaises(ValueError) as ctx:
            npi_orchestrator.validate_colocation(target, "test-project")
        self.assertIn("Colocation Error", str(ctx.exception))


class TestQueryResults(unittest.TestCase):
    """Unit tests for query_results.py get_table_metrics."""

    @patch('subprocess.run')
    def test_get_table_metrics_fio_table(self, mock_run):
        mock_stdout = json.dumps([{
            "fio_version": "fio-3.36",
            "seq_read_bw_mbs": 2500.5,
            "rand_read_bw_mbs": 1200.0,
            "write_bw_mbs": 450.25,
            "seq_read_lat_ms": 1.2,
            "rand_read_lat_ms": 2.5,
            "write_lat_ms": 3.0
        }])
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)

        metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_http1")
        self.assertEqual(metrics["fio_version"], "fio-3.36")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 2500.5)
        self.assertAlmostEqual(metrics["rand_read_bw_mbs"], 1200.0)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 450.25)

        cmd = mock_run.call_args[0][0]
        query = cmd[-1]
        self.assertIn("UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs))", query)

    @patch('subprocess.run')
    def test_get_table_metrics_go_client_table(self, mock_run):
        mock_stdout = json.dumps([{
            "fio_version": "go-client",
            "seq_read_bw_mbs": 3200.75,
            "rand_read_bw_mbs": 0.0,
            "write_bw_mbs": 0.0,
            "seq_read_lat_ms": 0.0,
            "rand_read_lat_ms": 0.0,
            "write_lat_ms": 0.0
        }])
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)

        metrics = query_results.get_table_metrics("test-proj", "test-ds", "go_client_read_grpc")
        self.assertEqual(metrics["fio_version"], "go-client")
        self.assertAlmostEqual(metrics["seq_read_bw_mbs"], 3200.75)
        self.assertAlmostEqual(metrics["write_bw_mbs"], 0.0)

        cmd = mock_run.call_args[0][0]
        query = cmd[-1]
        self.assertIn("read_bw_mbps", query)
        self.assertNotIn("UNNEST", query)

    @patch('subprocess.run')
    def test_get_table_metrics_called_process_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "bq", stderr="Table not found")

        metrics = query_results.get_table_metrics("test-proj", "test-ds", "nonexistent_table")
        self.assertEqual(metrics, {
            "seq_read_bw_mbs": 0.0,
            "rand_read_bw_mbs": 0.0,
            "write_bw_mbs": 0.0,
            "seq_read_lat_ms": 0.0,
            "rand_read_lat_ms": 0.0,
            "write_lat_ms": 0.0,
            "fio_version": "N/A"
        })

    @patch('subprocess.run')
    def test_get_table_metrics_empty_result(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")

        metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_http1")
        self.assertEqual(metrics, {
            "seq_read_bw_mbs": 0.0,
            "rand_read_bw_mbs": 0.0,
            "write_bw_mbs": 0.0,
            "seq_read_lat_ms": 0.0,
            "rand_read_lat_ms": 0.0,
            "write_lat_ms": 0.0,
            "fio_version": "N/A"
        })

    @patch('subprocess.run')
    def test_get_table_metrics_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="invalid json string")

        metrics = query_results.get_table_metrics("test-proj", "test-ds", "fio_read_http1")
        self.assertEqual(metrics, {
            "seq_read_bw_mbs": 0.0,
            "rand_read_bw_mbs": 0.0,
            "write_bw_mbs": 0.0,
            "seq_read_lat_ms": 0.0,
            "rand_read_lat_ms": 0.0,
            "write_lat_ms": 0.0,
            "fio_version": "N/A"
        })


class TestBenchmarkFactoryExtraMountOptions(unittest.TestCase):

    def setUp(self):
        self.factory_default = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer"
        )

    def test_format_extra_mount_options_none_and_empty(self):
        self.assertEqual(self.factory_default._format_extra_mount_options(None), "")
        self.assertEqual(self.factory_default._format_extra_mount_options(""), "")
        self.assertEqual(self.factory_default._format_extra_mount_options("   \n\t  "), "")

    def test_format_extra_mount_options_single_flag(self):
        self.assertEqual(
            self.factory_default._format_extra_mount_options("implicit-dirs"),
            "--implicit-dirs"
        )
        self.assertEqual(
            self.factory_default._format_extra_mount_options("--implicit-dirs"),
            "--implicit-dirs"
        )

    def test_format_extra_mount_options_comma_separated_key_values(self):
        opts = "congestion-threshold=384,max-background=512"
        formatted = self.factory_default._format_extra_mount_options(opts)
        self.assertEqual(formatted, "--congestion-threshold=384 --max-background=512")

        opts_with_spaces = "congestion-threshold=384, max-background=512, implicit-dirs"
        formatted = self.factory_default._format_extra_mount_options(opts_with_spaces)
        self.assertEqual(formatted, "--congestion-threshold=384 --max-background=512 --implicit-dirs")

    def test_format_extra_mount_options_space_separated_key_values(self):
        opts = "congestion-threshold=384 max-background=512"
        formatted = self.factory_default._format_extra_mount_options(opts)
        self.assertEqual(formatted, "--congestion-threshold=384 --max-background=512")

    def test_format_extra_mount_options_prefixed_flags_no_double_hyphen(self):
        opts = "--custom-opt=val --debug_gcs"
        formatted = self.factory_default._format_extra_mount_options(opts)
        self.assertEqual(formatted, "--custom-opt=val --debug_gcs")

        opts_comma = "--custom-opt=val, --debug_gcs"
        formatted = self.factory_default._format_extra_mount_options(opts_comma)
        self.assertEqual(formatted, "--custom-opt=val --debug_gcs")

    def test_format_extra_mount_options_dash_o_flag_preserved(self):
        opts_space = "-o allow_other --custom-flag=1"
        formatted = self.factory_default._format_extra_mount_options(opts_space)
        self.assertEqual(formatted, "-o allow_other --custom-flag=1")

        opts_comma = "-o allow_other,congestion-threshold=384"
        formatted = self.factory_default._format_extra_mount_options(opts_comma)
        self.assertEqual(formatted, "-o allow_other --congestion-threshold=384")

        opts_comma_split = "-o,allow_other,congestion-threshold=384"
        formatted = self.factory_default._format_extra_mount_options(opts_comma_split)
        self.assertEqual(formatted, "-o allow_other --congestion-threshold=384")

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_standard_with_extra_mount_options(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            extra_mount_options="congestion-threshold=384,max-background=512"
        )
        cmd, table_id = factory.get_benchmark_command("read_grpc")
        self.assertEqual(table_id, "fio_read_grpc")
        self.assertIn("-v /mnt/buffer:/gcsfuse-buffer", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("-o allow_other", cmd)
        self.assertIn("--client-protocol=grpc", cmd)
        self.assertIn("--log-file=/gcsfuse-buffer/gcsfuse.log", cmd)
        self.assertIn("--log-format=json", cmd)
        self.assertIn("--congestion-threshold=384", cmd)
        self.assertIn("--max-background=512", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_standard_without_extra_mount_options(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            extra_mount_options=None
        )
        cmd, table_id = factory.get_benchmark_command("read_grpc")
        self.assertEqual(table_id, "fio_read_grpc")
        self.assertIn("-v /mnt/buffer:/gcsfuse-buffer", cmd)
        self.assertIn("--temp-dir=/gcsfuse-buffer/write", cmd)
        self.assertIn("--client-protocol=grpc", cmd)
        self.assertNotIn("--congestion-threshold", cmd)
        self.assertNotIn("--max-background", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_file_cache_with_extra_mount_options(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            file_cache_size_mb=4096,
            extra_mount_options="congestion-threshold=384"
        )
        cmd, table_id = factory.get_benchmark_command("read_file_cache_grpc")
        self.assertEqual(table_id, "fio_read_file_cache")
        self.assertIn("--cache-dir=/gcsfuse-buffer/file-cache", cmd)
        self.assertIn("--file-cache-max-size-mb=4096", cmd)
        self.assertIn("--metadata-cache-ttl-secs=-1", cmd)
        self.assertIn("--congestion-threshold=384", cmd)
        self.assertIn("--keep-mount", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_numa_bound_with_extra_mount_options(self, mock_get_cpu):
        mock_get_cpu.side_effect = lambda node_id: "0-15" if node_id == 0 else "16-31"
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=5,
            buffer_mount_path="/mnt/buffer",
            extra_mount_options="max-background=512"
        )
        cmd, table_id = factory.get_benchmark_command("read_grpc_numa0_fio_bound")
        self.assertEqual(table_id, "fio_read_grpc_numa0_fio_bound")
        self.assertIn("--cpu-limit-list=0-15", cmd)
        self.assertIn("--bind-fio", cmd)
        self.assertIn("--max-background=512", cmd)

    @patch('npi.BenchmarkFactory._get_cpu_list_for_numa_node')
    def test_get_benchmark_command_host_info_ignores_extra_mount_options(self, mock_get_cpu):
        mock_get_cpu.return_value = None
        factory = npi.BenchmarkFactory(
            bucket_name="test-bucket",
            project_id="test-project",
            bq_dataset_id="test-dataset",
            iterations=1,
            buffer_mount_path="/mnt/buffer",
            extra_mount_options="congestion-threshold=384"
        )
        cmd, table_id = factory.get_benchmark_command("host_info")
        self.assertEqual(table_id, "host_info")
        self.assertNotIn("--gcsfuse-flags", cmd)
        self.assertNotIn("--congestion-threshold", cmd)
        self.assertIn("host-info-collector", cmd)


class TestNpiMainExtraMountOptions(unittest.TestCase):

    @patch('os.makedirs')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('npi.BenchmarkFactory')
    @patch('npi.verify_permissions', return_value=True)
    def test_main_passes_extra_mount_options_to_factory(self, mock_verify_perms, mock_factory_class, mock_parse_args, mock_makedirs):
        mock_args = MagicMock()
        mock_args.benchmarks = ["read_grpc"]
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
        mock_args.smoke_mode = False
        mock_args.extra_mount_options = "congestion-threshold=384,max-background=512"
        mock_parse_args.return_value = mock_args

        mock_factory_instance = MagicMock()
        mock_factory_instance.get_available_benchmarks.return_value = ["read_grpc"]
        mock_factory_instance.get_benchmark_command.return_value = ("docker run ...", "test-table")
        mock_factory_class.return_value = mock_factory_instance

        with patch('npi.run_benchmark', return_value=True):
            npi.main()
            mock_factory_class.assert_called_once()
            _, kwargs = mock_factory_class.call_args
            self.assertEqual(kwargs.get("extra_mount_options"), "congestion-threshold=384,max-background=512")

    def test_main_cli_help_documents_extra_mount_options(self):
        res = subprocess.run(["python3", "npi.py", "--help"], capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(npi.__file__)))
        self.assertEqual(res.returncode, 0)
        self.assertIn("--extra-mount-options", res.stdout)


class TestOrchestratorExtraMountOptions(unittest.TestCase):

    def _run_execute_target(self, target, args):
        state = {target["name"]: {"status": "PENDING"}}
        state_lock = MagicMock()
        captured_cmds = []

        def mock_ssh(socket_path, vm_name, zone, cmd, timeout=60):
            captured_cmds.append(cmd)
            return (0, "", "")

        with patch('npi_orchestrator.cleanup_remote_run'), \
             patch('npi_orchestrator.prep_vm'), \
             patch('npi_orchestrator.run_ssh_cmd', side_effect=mock_ssh), \
             patch('npi_orchestrator.monitor_run'):
            npi_orchestrator.execute_target(target, args, state_lock, state)

        return captured_cmds

    def test_gce_target_with_target_level_extra_mount_options(self):
        target = {
            "name": "gce_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "buffer_mount": "/mnt/buffer",
            "extra_mount_options": "congestion-threshold=384",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = None

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=congestion-threshold=384", triggered[0])

    def test_gce_target_with_cli_extra_mount_options(self):
        target = {
            "name": "gce_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "buffer_mount": "/mnt/buffer",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = "max-background=512"

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=max-background=512", triggered[0])

    def test_gce_target_with_both_target_and_cli_extra_mount_options(self):
        target = {
            "name": "gce_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "buffer_mount": "/mnt/buffer",
            "extra_mount_options": "congestion-threshold=384",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = "max-background=512"

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=congestion-threshold=384,max-background=512", triggered[0])

    def test_gce_target_with_no_extra_mount_options(self):
        target = {
            "name": "gce_target",
            "type": "gce",
            "vm_name": "vm1",
            "zone": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "buffer_mount": "/mnt/buffer",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = None

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertNotIn("--extra-mount-options", triggered[0])

    def test_gke_target_with_target_level_extra_mount_options(self):
        target = {
            "name": "gke_target",
            "type": "gke",
            "vm_name": "gke-runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "extra_mount_options": "congestion-threshold=384",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = None

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi_gke.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=congestion-threshold=384", triggered[0])

    def test_gke_target_with_cli_extra_mount_options(self):
        target = {
            "name": "gke_target",
            "type": "gke",
            "vm_name": "gke-runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = "max-background=512"

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi_gke.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=max-background=512", triggered[0])

    def test_gke_target_with_both_target_and_cli_extra_mount_options(self):
        target = {
            "name": "gke_target",
            "type": "gke",
            "vm_name": "gke-runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "extra_mount_options": "congestion-threshold=384",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = "max-background=512"

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi_gke.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertIn("--extra-mount-options=congestion-threshold=384,max-background=512", triggered[0])

    def test_gke_target_with_no_extra_mount_options(self):
        target = {
            "name": "gke_target",
            "type": "gke",
            "vm_name": "gke-runner-vm",
            "zone": "us-central1-a",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "bucket": "gs://test-bucket",
            "dataset": "test_dataset",
            "has_ssd": True
        }
        args = MagicMock()
        args.benchmarks = "read_grpc"
        args.project = "test-project"
        args.image_version = "latest"
        args.iterations = 1
        args.smoke_mode = False
        args.extra_mount_options = None

        cmds = self._run_execute_target(target, args)
        triggered = [c for c in cmds if "npi_gke.py" in c]
        self.assertEqual(len(triggered), 1)
        self.assertNotIn("--extra-mount-options", triggered[0])

    def test_orchestrator_cli_help_documents_extra_mount_options(self):
        res = subprocess.run(["python3", "npi_orchestrator.py", "--help"], capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(npi_orchestrator.__file__)))
        self.assertEqual(res.returncode, 0)
        self.assertIn("--extra-mount-options", res.stdout)


if __name__ == '__main__':
    unittest.main()





