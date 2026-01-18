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

"""Unit tests for result_aggregator module"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, mock_open
import json

# Mock dependencies before importing
sys.path.insert(0, os.path.dirname(__file__))

# Create mock modules
mock_gcs = MagicMock()
mock_gcloud_utils = MagicMock()
sys.modules['gcs'] = mock_gcs
sys.modules['gcloud_utils'] = mock_gcloud_utils

# Patch the imports in result_aggregator before importing it
with patch.dict('sys.modules', {'gcs': mock_gcs, 'gcloud_utils': mock_gcloud_utils}):
    # Temporarily replace relative imports with absolute
    import importlib.util
    spec = importlib.util.spec_from_file_location("result_aggregator", 
                                                    os.path.join(os.path.dirname(__file__), "result_aggregator.py"))
    result_aggregator = importlib.util.module_from_spec(spec)
    
    # Mock the relative imports
    result_aggregator.gcs = mock_gcs
    result_aggregator.gcloud_utils = mock_gcloud_utils
    
    # Execute the module
    spec.loader.exec_module(result_aggregator)


class TestAvg(unittest.TestCase):
    """Test _avg helper function"""
    
    def test_avg_normal_list(self):
        """Test average of normal list"""
        result = result_aggregator._avg([10, 20, 30, 40])
        self.assertEqual(result, 25.0)
    
    def test_avg_single_value(self):
        """Test average of single value"""
        result = result_aggregator._avg([42])
        self.assertEqual(result, 42.0)
    
    def test_avg_empty_list(self):
        """Test average of empty list returns 0"""
        result = result_aggregator._avg([])
        self.assertEqual(result, 0)
    
    def test_avg_floats(self):
        """Test average with float values"""
        result = result_aggregator._avg([1.5, 2.5, 3.0])
        self.assertAlmostEqual(result, 2.333, places=3)


class TestExtractLatencyMetrics(unittest.TestCase):
    """Test _extract_latency_metrics helper function"""
    
    def test_extract_clat_ns_metrics(self):
        """Test extracting latency from clat_ns (microseconds)"""
        job = {
            'read': {
                'bw': 100000,
                'clat_ns': {
                    'min': 1000,      # 1000 µs = 1 ms
                    'max': 50000,     # 50000 µs = 50 ms
                    'mean': 10000,    # 10000 µs = 10 ms
                    'stddev': 5000,   # 5000 µs = 5 ms
                    'percentile': {
                        '50.000000': 9000,   # 9 ms
                        '90.000000': 20000,  # 20 ms
                        '99.000000': 45000   # 45 ms
                    }
                }
            }
        }
        
        result = result_aggregator._extract_latency_metrics(job)
        
        self.assertEqual(result['bw'], 100000)
        self.assertAlmostEqual(result['min'], 1.0, places=2)
        self.assertAlmostEqual(result['max'], 50.0, places=2)
        self.assertAlmostEqual(result['mean'], 10.0, places=2)
        self.assertAlmostEqual(result['stddev'], 5.0, places=2)
        self.assertAlmostEqual(result['p50'], 9.0, places=2)
        self.assertAlmostEqual(result['p90'], 20.0, places=2)
        self.assertAlmostEqual(result['p99'], 45.0, places=2)
    
    def test_extract_lat_ns_metrics(self):
        """Test extracting latency from lat_ns (nanoseconds)"""
        job = {
            'read': {
                'bw': 100000,
                'lat_ns': {
                    'min': 1000000,     # 1,000,000 ns = 1 ms
                    'max': 50000000,    # 50,000,000 ns = 50 ms
                    'mean': 10000000,   # 10,000,000 ns = 10 ms
                    'stddev': 5000000   # 5,000,000 ns = 5 ms
                }
            }
        }
        
        result = result_aggregator._extract_latency_metrics(job)
        
        self.assertEqual(result['bw'], 100000)
        self.assertAlmostEqual(result['min'], 1.0, places=2)
        self.assertAlmostEqual(result['max'], 50.0, places=2)
        self.assertAlmostEqual(result['mean'], 10.0, places=2)
        self.assertAlmostEqual(result['stddev'], 5.0, places=2)
    
    def test_extract_no_read_data(self):
        """Test extracting from job without read data"""
        job = {'write': {'bw': 50000}}
        
        result = result_aggregator._extract_latency_metrics(job)
        
        self.assertIsNone(result)
    
    def test_extract_no_latency_data(self):
        """Test extracting when only bandwidth is present"""
        job = {'read': {'bw': 100000}}
        
        result = result_aggregator._extract_latency_metrics(job)
        
        self.assertEqual(result['bw'], 100000)
        self.assertNotIn('min', result)
        self.assertNotIn('max', result)


class TestParseTestResults(unittest.TestCase):
    """Test parse_test_results function"""
    
    @patch('result_aggregator.glob.glob')
    @patch('builtins.open', new_callable=mock_open)
    def test_parse_single_config_results(self, mock_file, mock_glob):
        """Test parsing results in single-config mode"""
        mock_glob.return_value = ['/tmp/test-1/fio_output_1.json', '/tmp/test-1/fio_output_2.json']
        
        fio_data = {
            'jobs': [{
                'read': {
                    'bw': 100000,
                    'clat_ns': {
                        'min': 1000, 'max': 50000, 'mean': 10000, 'stddev': 5000,
                        'percentile': {'50.000000': 9000, '90.000000': 20000, '99.000000': 45000}
                    }
                },
                'write': {'bw': 50000}
            }]
        }
        
        mock_file.return_value.read.return_value = json.dumps(fio_data)
        
        test_info = {
            'test_id': 1,
            'status': 'success',
            'params': {'io_type': 'read', 'threads': '4'}
        }
        
        result = result_aggregator.parse_test_results('/tmp/test-1', test_info, mode='single-config')
        
        self.assertEqual(result['test_params'], test_info['params'])
        self.assertGreater(result['read_bw_mbps'], 0)
        self.assertGreater(result['write_bw_mbps'], 0)
        self.assertGreater(result['read_lat_min_ms'], 0)
        self.assertEqual(result['iterations'], 2)
        self.assertNotIn('matrix_id', result)
    
    @patch('result_aggregator.glob.glob')
    @patch('builtins.open', new_callable=mock_open)
    def test_parse_multi_config_results(self, mock_file, mock_glob):
        """Test parsing results in multi-config mode"""
        mock_glob.return_value = ['/tmp/test-5/fio_output_1.json']
        
        fio_data = {
            'jobs': [{
                'read': {'bw': 100000, 'clat_ns': {'min': 1000, 'max': 50000, 'mean': 10000}},
                'write': {'bw': 50000}
            }]
        }
        
        mock_file.return_value.read.return_value = json.dumps(fio_data)
        
        test_info = {
            'test_id': 1,
            'matrix_id': 5,
            'status': 'success',
            'params': {'config_label': 'cfg1', 'io_type': 'read'}
        }
        
        result = result_aggregator.parse_test_results('/tmp/test-5', test_info, mode='multi-config')
        
        self.assertEqual(result['matrix_id'], 5)
        self.assertEqual(result['test_id'], 1)
        self.assertEqual(result['iterations'], 1)
    
    @patch('result_aggregator.glob.glob')
    def test_parse_no_fio_files(self, mock_glob):
        """Test parsing when no FIO files found"""
        mock_glob.return_value = []
        
        test_info = {'test_id': 1, 'params': {}}
        result = result_aggregator.parse_test_results('/tmp/test-1', test_info)
        
        self.assertEqual(result['read_bw_mbps'], 0)
        self.assertEqual(result['write_bw_mbps'], 0)
        self.assertEqual(result['iterations'], 0)


class TestAggregateResults(unittest.TestCase):
    """Test aggregate_results function"""
    
    @patch('result_aggregator.gcloud_utils.gcloud_storage_cp')
    @patch('result_aggregator.tempfile.TemporaryDirectory')
    @patch('result_aggregator.os.path.exists')
    @patch('result_aggregator.os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    @patch('result_aggregator.parse_test_results')
    def test_aggregate_from_multiple_vms(self, mock_parse, mock_file, mock_makedirs, 
                                         mock_exists, mock_tmpdir, mock_gcloud_cp):
        """Test aggregating results from multiple VMs"""
        # Setup mocks
        mock_tmpdir.return_value.__enter__.return_value = '/tmp/test'
        mock_exists.return_value = True
        
        manifest = {
            'status': 'completed',
            'tests': [
                {'test_id': 1, 'status': 'success', 'params': {'io_type': 'read'}},
                {'test_id': 2, 'status': 'success', 'params': {'io_type': 'write'}}
            ]
        }
        mock_file.return_value.read.return_value = json.dumps(manifest)
        
        mock_parse.side_effect = [
            {'test_params': {}, 'read_bw_mbps': 100, 'write_bw_mbps': 50, 'iterations': 2},
            {'test_params': {}, 'read_bw_mbps': 120, 'write_bw_mbps': 60, 'iterations': 2}
        ]
        
        vms = ['vm-1', 'vm-2']
        result = result_aggregator.aggregate_results('bench-123', 'artifacts', vms, mode='single-config')
        
        # Should have 2 test results (one from each VM, but same test_id could be on both)
        self.assertGreater(len(result), 0)
    
    @patch('result_aggregator.gcloud_utils.gcloud_storage_cp')
    @patch('result_aggregator.tempfile.TemporaryDirectory')
    def test_aggregate_handles_failures(self, mock_tmpdir, mock_gcloud_cp):
        """Test aggregation handles VM download failures"""
        mock_tmpdir.return_value.__enter__.return_value = '/tmp/test'
        mock_gcloud_cp.side_effect = Exception('Download failed')
        
        vms = ['vm-1', 'vm-2']
        result = result_aggregator.aggregate_results('bench-123', 'artifacts', vms)
        
        # Should return empty dict when all downloads fail
        self.assertEqual(len(result), 0)


if __name__ == '__main__':
    unittest.main()
