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

"""Unit tests for report_generator module"""

import sys
import unittest
from unittest.mock import patch, mock_open, MagicMock

# Mock tabulate module before importing report_generator
sys.modules['tabulate'] = MagicMock()

import report_generator


class TestExtractResourceMetrics(unittest.TestCase):
    """Test _extract_resource_metrics helper function"""
    
    def test_extract_all_metrics(self):
        """Test extracting all resource metrics"""
        params = {
            'avg_cpu': 45.2,
            'peak_cpu': 89.1,
            'avg_mem_mb': 1024,
            'peak_mem_mb': 2048,
            'avg_page_cache_gb': 5.5,
            'peak_page_cache_gb': 8.2,
            'avg_sys_cpu': 12.3,
            'peak_sys_cpu': 25.6,
            'avg_net_rx_mbps': 100.5,
            'peak_net_rx_mbps': 250.0,
            'avg_net_tx_mbps': 50.2,
            'peak_net_tx_mbps': 120.8
        }
        
        result = report_generator._extract_resource_metrics(params)
        
        self.assertEqual(result['avg_cpu'], 45.2)
        self.assertEqual(result['peak_mem'], 2048)
        self.assertEqual(result['avg_page_cache'], 5.5)
    
    def test_extract_missing_metrics(self):
        """Test extracting metrics with missing values"""
        params = {'avg_cpu': 50.0}
        
        result = report_generator._extract_resource_metrics(params)
        
        self.assertEqual(result['avg_cpu'], 50.0)
        self.assertEqual(result['peak_cpu'], '-')
        self.assertEqual(result['avg_mem'], '-')


class TestFormatMetric(unittest.TestCase):
    """Test _format_metric helper function"""
    
    def test_format_positive_value(self):
        """Test formatting positive metric value"""
        result = report_generator._format_metric(123.456)
        self.assertEqual(result, '123.46')
    
    def test_format_zero_value(self):
        """Test formatting zero returns default"""
        result = report_generator._format_metric(0)
        self.assertEqual(result, '-')
    
    def test_format_negative_value(self):
        """Test formatting negative value returns default"""
        result = report_generator._format_metric(-5.0)
        self.assertEqual(result, '-')
    
    def test_custom_default(self):
        """Test custom default value"""
        result = report_generator._format_metric(0, default='N/A')
        self.assertEqual(result, 'N/A')


class TestFormatParams(unittest.TestCase):
    """Test format_params function"""
    
    def test_format_all_params(self):
        """Test formatting complete parameter set"""
        params = {
            'io_type': 'read',
            'threads': '4',
            'file_size': '1GB',
            'bs': '1M',
            'io_depth': '64',
            'nrfiles': '100'
        }
        
        result = report_generator.format_params(params)
        self.assertEqual(result, 'read|4|1GB|1M|64|100')
    
    def test_format_partial_params(self):
        """Test formatting with missing parameters"""
        params = {
            'io_type': 'write',
            'threads': '8',
            'bs': '4M'
        }
        
        result = report_generator.format_params(params)
        self.assertEqual(result, 'write|8|4M')
    
    def test_format_empty_params(self):
        """Test formatting empty parameters"""
        result = report_generator.format_params({})
        self.assertEqual(result, '-')
    
    def test_format_none_params(self):
        """Test formatting None parameters"""
        result = report_generator.format_params(None)
        self.assertEqual(result, '-')


class TestGenerateCombinedReport(unittest.TestCase):
    """Test generate_combined_report function"""
    
    @patch('report_generator.tabulate')
    @patch('builtins.open', new_callable=mock_open)
    def test_single_config_report(self, mock_file, mock_tabulate):
        """Test generating single-config report"""
        metrics = {
            1: {
                'test_params': {'io_type': 'read', 'threads': '4'},
                'read_bw_mbps': 100.5,
                'write_bw_mbps': 0,
                'read_lat_min_ms': 1.5,
                'read_lat_max_ms': 50.0,
                'read_lat_avg_ms': 10.2,
                'read_lat_stddev_ms': 5.1,
                'read_lat_p50_ms': 9.8,
                'read_lat_p90_ms': 20.5,
                'read_lat_p99_ms': 45.2,
                'iterations': 3
            }
        }
        
        report_generator.generate_combined_report(metrics, 'output.csv', 'single-config')
        
        # Verify file was written
        mock_file.assert_called_once_with('output.csv', 'w', newline='')
        mock_tabulate.assert_called_once()
    
    @patch('report_generator.tabulate')
    @patch('builtins.open', new_callable=mock_open)
    def test_multi_config_report(self, mock_file, mock_tabulate):
        """Test generating multi-config report"""
        metrics = {
            5: {
                'matrix_id': 5,
                'test_id': 1,
                'test_params': {
                    'config_label': 'cfg1',
                    'commit': 'abc123',
                    'io_type': 'read'
                },
                'read_bw_mbps': 150.0,
                'write_bw_mbps': 0,
                'iterations': 2
            }
        }
        
        report_generator.generate_combined_report(metrics, 'output.csv', 'multi-config')
        
        mock_file.assert_called_once_with('output.csv', 'w', newline='')
        mock_tabulate.assert_called_once()


class TestGenerateSeparateReports(unittest.TestCase):
    """Test generate_separate_reports function"""
    
    @patch('builtins.open', new_callable=mock_open)
    @patch('report_generator.os.path.dirname', return_value='/tmp')
    @patch('report_generator.os.path.basename', return_value='report.csv')
    def test_separate_reports_by_config(self, mock_basename, mock_dirname, mock_file):
        """Test generating separate reports per config"""
        metrics = {
            1: {
                'test_params': {'config_label': 'cfg1', 'io_type': 'read'},
                'read_bw_mbps': 100.0,
                'write_bw_mbps': 50.0,
                'iterations': 2
            },
            2: {
                'test_params': {'config_label': 'cfg2', 'io_type': 'write'},
                'read_bw_mbps': 120.0,
                'write_bw_mbps': 60.0,
                'iterations': 2
            },
            3: {
                'test_params': {'config_label': 'cfg1', 'io_type': 'write'},
                'read_bw_mbps': 110.0,
                'write_bw_mbps': 55.0,
                'iterations': 2
            }
        }
        
        report_generator.generate_separate_reports(metrics, '/tmp/report.csv')
        
        # Should create 2 files (cfg1 and cfg2)
        self.assertEqual(mock_file.call_count, 2)


class TestGenerateReport(unittest.TestCase):
    """Test main generate_report function"""
    
    @patch('report_generator.generate_combined_report')
    @patch('report_generator.os.makedirs')
    def test_generate_combined(self, mock_makedirs, mock_combined):
        """Test routing to combined report"""
        metrics = {1: {'test_params': {}, 'read_bw_mbps': 100}}
        
        report_generator.generate_report(metrics, 'output.csv', mode='single-config')
        
        mock_combined.assert_called_once_with(metrics, 'output.csv', 'single-config')
    
    @patch('report_generator.generate_separate_reports')
    @patch('report_generator.os.makedirs')
    def test_generate_separate(self, mock_makedirs, mock_separate):
        """Test routing to separate reports"""
        metrics = {1: {'test_params': {'config_label': 'cfg1'}, 'read_bw_mbps': 100}}
        
        report_generator.generate_report(metrics, 'output.csv', mode='multi-config', separate_configs=True)
        
        mock_separate.assert_called_once_with(metrics, 'output.csv')


if __name__ == '__main__':
    unittest.main()
