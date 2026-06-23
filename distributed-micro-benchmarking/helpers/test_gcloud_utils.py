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

"""Unit tests for gcloud_utils module"""

import unittest
from unittest.mock import patch, MagicMock
import subprocess
import gcloud_utils


class TestRunGcloudCommand(unittest.TestCase):
    """Test run_gcloud_command function"""
    
    @patch('gcloud_utils.subprocess.run')
    def test_successful_command(self, mock_run):
        """Test successful command execution"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success"
        mock_run.return_value = mock_result
        
        cmd = ['gcloud', 'storage', 'ls', 'gs://bucket/']
        result = gcloud_utils.run_gcloud_command(cmd, retries=1)
        
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "success")
        mock_run.assert_called_once()
    
    @patch('gcloud_utils.subprocess.run')
    @patch('gcloud_utils.time.sleep')
    def test_retry_logic(self, mock_sleep, mock_run):
        """Test retry logic on failures"""
        mock_result_fail = MagicMock()
        mock_result_fail.returncode = 1
        mock_result_fail.stderr = "error"
        
        mock_result_success = MagicMock()
        mock_result_success.returncode = 0
        
        # Fail first, then succeed
        mock_run.side_effect = [mock_result_fail, mock_result_success]
        
        cmd = ['gcloud', 'storage', 'ls', 'gs://bucket/']
        result = gcloud_utils.run_gcloud_command(cmd, retries=2, retry_delay=1)
        
        self.assertEqual(result.returncode, 0)
        self.assertEqual(mock_run.call_count, 2)
        mock_sleep.assert_called_once_with(1)
    
    @patch('gcloud_utils.subprocess.run')
    def test_check_raises_exception(self, mock_run):
        """Test that check=True raises exception on failure"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "command failed"
        mock_run.return_value = mock_result
        
        cmd = ['gcloud', 'storage', 'ls', 'gs://bucket/']
        
        with self.assertRaises(Exception) as ctx:
            gcloud_utils.run_gcloud_command(cmd, retries=1, check=True)
        
        self.assertIn("Command failed", str(ctx.exception))


class TestGcloudStorageCp(unittest.TestCase):
    """Test gcloud_storage_cp function"""
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_simple_copy(self, mock_run):
        """Test simple file copy"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gcloud_utils.gcloud_storage_cp('local.txt', 'gs://bucket/remote.txt')
        
        expected_cmd = ['gcloud', 'storage', 'cp', 'local.txt', 'gs://bucket/remote.txt']
        mock_run.assert_called_once_with(expected_cmd, retries=3, check=True)
        self.assertEqual(result.returncode, 0)
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_recursive_copy(self, mock_run):
        """Test recursive directory copy"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        gcloud_utils.gcloud_storage_cp('local_dir/', 'gs://bucket/remote_dir/', recursive=True)
        
        expected_cmd = ['gcloud', 'storage', 'cp', '-r', 'local_dir/', 'gs://bucket/remote_dir/']
        mock_run.assert_called_once_with(expected_cmd, retries=3, check=True)


class TestGcloudStorageLs(unittest.TestCase):
    """Test gcloud_storage_ls function"""
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_list_objects(self, mock_run):
        """Test listing GCS objects"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "gs://bucket/file1.txt\ngs://bucket/file2.txt\n"
        mock_run.return_value = mock_result
        
        result = gcloud_utils.gcloud_storage_ls('gs://bucket/*')
        
        expected_cmd = ['gcloud', 'storage', 'ls', 'gs://bucket/*']
        mock_run.assert_called_once_with(expected_cmd, retries=1, check=False)
        self.assertEqual(result.returncode, 0)


class TestGcloudComputeSsh(unittest.TestCase):
    """Test gcloud_compute_ssh function"""
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_ssh_no_command(self, mock_run):
        """Test SSH without command"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        gcloud_utils.gcloud_compute_ssh('vm-1', 'us-central1-a', 'my-project')
        
        expected_cmd = [
            'gcloud', 'compute', 'ssh', 'vm-1',
            '--zone=us-central1-a',
            '--project=my-project',
            '--internal-ip'
        ]
        mock_run.assert_called_once_with(expected_cmd, retries=1, check=True)
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_ssh_with_command(self, mock_run):
        """Test SSH with command execution"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        gcloud_utils.gcloud_compute_ssh('vm-1', 'us-central1-a', 'my-project', command='ls -la')
        
        expected_cmd = [
            'gcloud', 'compute', 'ssh', 'vm-1',
            '--zone=us-central1-a',
            '--project=my-project',
            '--internal-ip',
            '--command', 'ls -la'
        ]
        mock_run.assert_called_once()
        actual_cmd = mock_run.call_args[0][0]
        self.assertEqual(actual_cmd, expected_cmd)


class TestGcloudComputeScp(unittest.TestCase):
    """Test gcloud_compute_scp function"""
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_scp_to_vm(self, mock_run):
        """Test copying file to VM"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        gcloud_utils.gcloud_compute_scp('local.txt', 'vm-1:/tmp/remote.txt', 'us-central1-a', 'my-project')
        
        expected_cmd = [
            'gcloud', 'compute', 'scp', 'local.txt', 'vm-1:/tmp/remote.txt',
            '--zone=us-central1-a',
            '--project=my-project',
            '--internal-ip'
        ]
        mock_run.assert_called_once_with(expected_cmd, retries=1, check=True)


class TestGcloudComputeInstanceGroupList(unittest.TestCase):
    """Test gcloud_compute_instance_group_list function"""
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_list_running_vms(self, mock_run):
        """Test listing running VMs"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "vm-1\nvm-2\nvm-3\n"
        mock_run.return_value = mock_result
        
        vms = gcloud_utils.gcloud_compute_instance_group_list('my-ig', 'us-central1-a', 'my-project')
        
        expected_cmd = [
            'gcloud', 'compute', 'instance-groups', 'managed', 'list-instances',
            'my-ig',
            '--zone=us-central1-a',
            '--project=my-project',
            '--filter=STATUS=RUNNING',
            '--format=value(NAME)'
        ]
        mock_run.assert_called_once_with(expected_cmd, retries=1, check=True)
        self.assertEqual(vms, ['vm-1', 'vm-2', 'vm-3'])
    
    @patch('gcloud_utils.run_gcloud_command')
    def test_list_with_custom_filter(self, mock_run):
        """Test listing VMs with custom status filter"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "vm-1\n"
        mock_run.return_value = mock_result
        
        vms = gcloud_utils.gcloud_compute_instance_group_list(
            'my-ig', 'us-central1-a', 'my-project', filter_status='STOPPED'
        )
        
        call_args = mock_run.call_args[0][0]
        self.assertIn('--filter=STATUS=STOPPED', call_args)
        self.assertEqual(vms, ['vm-1'])


if __name__ == '__main__':
    unittest.main()
