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

"""Unit tests for job_generator module"""

import unittest
from unittest.mock import mock_open, patch
import job_generator


class TestLoadCSVWithId(unittest.TestCase):
    """Test _load_csv_with_id helper function"""
    
    @patch('builtins.open', mock_open(read_data='col1,col2\nval1,val2\nval3,val4\n'))
    def test_load_csv_with_id(self):
        """Test loading CSV and adding ID field"""
        result = job_generator._load_csv_with_id('test.csv', 'my_id')
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['my_id'], 1)
        self.assertEqual(result[1]['my_id'], 2)
        self.assertEqual(result[0]['col1'], 'val1')


class TestLoadTestCases(unittest.TestCase):
    """Test load_test_cases function"""
    
    @patch('builtins.open', mock_open(read_data='io_type,threads,file_size\nread,4,1GB\nwrite,8,2GB\n'))
    def test_load_test_cases(self):
        """Test loading test cases with auto-generated test_id"""
        test_cases = job_generator.load_test_cases('test_cases.csv')
        
        self.assertEqual(len(test_cases), 2)
        self.assertEqual(test_cases[0]['test_id'], 1)
        self.assertEqual(test_cases[1]['test_id'], 2)
        self.assertEqual(test_cases[0]['io_type'], 'read')
        self.assertEqual(test_cases[1]['threads'], '8')


class TestLoadConfigs(unittest.TestCase):
    """Test load_configs function"""
    
    @patch('builtins.open', mock_open(read_data='commit,mount_args,label\nabc123,--arg1,config1\ndef456,--arg2,config2\n'))
    def test_load_configs(self):
        """Test loading configs with auto-generated config_id"""
        configs = job_generator.load_configs('configs.csv')
        
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0]['config_id'], 1)
        self.assertEqual(configs[1]['config_id'], 2)
        self.assertEqual(configs[0]['commit'], 'abc123')
        self.assertEqual(configs[1]['label'], 'config2')


class TestGenerateTestMatrix(unittest.TestCase):
    """Test generate_test_matrix function"""
    
    def test_generate_test_matrix(self):
        """Test cartesian product generation"""
        test_cases = [
            {'test_id': 1, 'io_type': 'read'},
            {'test_id': 2, 'io_type': 'write'}
        ]
        configs = [
            {'config_id': 1, 'commit': 'abc', 'mount_args': '--arg1', 'label': 'cfg1'},
            {'config_id': 2, 'commit': 'def', 'mount_args': '--arg2', 'label': 'cfg2'}
        ]
        
        matrix = job_generator.generate_test_matrix(test_cases, configs)
        
        # Should be 2 configs × 2 tests = 4 entries
        self.assertEqual(len(matrix), 4)
        
        # Check first entry
        self.assertEqual(matrix[0]['matrix_id'], 1)
        self.assertEqual(matrix[0]['config_id'], 1)
        self.assertEqual(matrix[0]['test_id'], 1)
        self.assertEqual(matrix[0]['commit'], 'abc')
        self.assertEqual(matrix[0]['io_type'], 'read')
        
        # Check last entry
        self.assertEqual(matrix[3]['matrix_id'], 4)
        self.assertEqual(matrix[3]['config_id'], 2)
        self.assertEqual(matrix[3]['test_id'], 2)
        self.assertEqual(matrix[3]['commit'], 'def')
        self.assertEqual(matrix[3]['io_type'], 'write')


class TestDistributeTests(unittest.TestCase):
    """Test distribute_tests function"""
    
    def test_distribute_evenly(self):
        """Test even distribution of tests across VMs"""
        test_cases = [{'test_id': i} for i in range(1, 11)]  # 10 tests
        vms = ['vm-1', 'vm-2', 'vm-3', 'vm-4', 'vm-5']  # 5 VMs
        
        distribution = job_generator.distribute_tests(test_cases, vms)
        
        self.assertEqual(len(distribution), 5)
        # Each VM should get 2 tests (10 / 5)
        for vm in vms:
            self.assertEqual(len(distribution[vm]), 2)
    
    def test_distribute_with_remainder(self):
        """Test distribution when tests don't divide evenly"""
        test_cases = [{'test_id': i} for i in range(1, 8)]  # 7 tests
        vms = ['vm-1', 'vm-2', 'vm-3']  # 3 VMs
        
        distribution = job_generator.distribute_tests(test_cases, vms)
        
        # First 1 VM gets 3 tests (7 % 3 = 1), others get 2
        self.assertEqual(len(distribution['vm-1']), 3)
        self.assertEqual(len(distribution['vm-2']), 2)
        self.assertEqual(len(distribution['vm-3']), 2)
    
    def test_distribute_all_tests_assigned(self):
        """Test that all tests are assigned to VMs"""
        test_cases = [{'test_id': i} for i in range(1, 13)]  # 12 tests
        vms = ['vm-1', 'vm-2', 'vm-3', 'vm-4']  # 4 VMs
        
        distribution = job_generator.distribute_tests(test_cases, vms)
        
        total_assigned = sum(len(tests) for tests in distribution.values())
        self.assertEqual(total_assigned, 12)


class TestCreateJobSpec(unittest.TestCase):
    """Test create_job_spec function"""
    
    def test_single_config_mode(self):
        """Test job spec generation in single-config mode"""
        test_entries = [
            {'test_id': 1, 'io_type': 'read'},
            {'test_id': 2, 'io_type': 'write'}
        ]
        
        job_spec = job_generator.create_job_spec(
            vm_name='vm-1',
            benchmark_id='bench-123',
            test_entries=test_entries,
            bucket='test-bucket',
            artifacts_bucket='artifacts-bucket',
            iterations=3,
            mode='single-config'
        )
        
        self.assertEqual(job_spec['vm_name'], 'vm-1')
        self.assertEqual(job_spec['benchmark_id'], 'bench-123')
        self.assertEqual(job_spec['total_tests'], 2)
        self.assertEqual(job_spec['total_runs'], 6)  # 2 tests × 3 iterations
        self.assertEqual(job_spec['iterations'], 3)
        self.assertIn('test_ids', job_spec)
        self.assertEqual(job_spec['test_ids'], [1, 2])
    
    def test_multi_config_mode(self):
        """Test job spec generation in multi-config mode"""
        test_entries = [
            {'test_id': 1, 'matrix_id': 5, 'config_id': 1},
            {'test_id': 2, 'matrix_id': 6, 'config_id': 1}
        ]
        
        job_spec = job_generator.create_job_spec(
            vm_name='vm-1',
            benchmark_id='bench-456',
            test_entries=test_entries,
            bucket='test-bucket',
            artifacts_bucket='artifacts-bucket',
            iterations=2,
            mode='multi-config'
        )
        
        self.assertEqual(job_spec['total_tests'], 2)
        self.assertEqual(job_spec['total_runs'], 4)  # 2 tests × 2 iterations
        self.assertIn('test_entries', job_spec)
        self.assertNotIn('test_ids', job_spec)
        self.assertEqual(len(job_spec['test_entries']), 2)


if __name__ == '__main__':
    unittest.main()
