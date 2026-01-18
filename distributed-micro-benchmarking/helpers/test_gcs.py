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

"""Unit tests for gcs module"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from unittest.mock import patch, MagicMock, mock_open
import json

# Import as module to avoid relative import issues
import gcs


class TestGcsOperations(unittest.TestCase):
    """Test GCS operations"""
    
    @patch('gcs.gcloud_utils.gcloud_storage_cp')
    @patch('builtins.open', new_callable=mock_open)
    @patch('gcs.tempfile.NamedTemporaryFile')
    def test_upload_json(self, mock_temp, mock_file, mock_gcloud_cp):
        """Test JSON upload to GCS"""
        mock_temp_file = MagicMock()
        mock_temp_file.name = '/tmp/test.json'
        mock_temp_file.__enter__.return_value = mock_temp_file
        mock_temp.return_value = mock_temp_file
        
        data = {'test': 'data', 'value': 123}
        gcs.upload_json(data, 'gs://bucket/file.json')
        
        mock_gcloud_cp.assert_called_once_with('/tmp/test.json', 'gs://bucket/file.json', retries=3, check=True)
    
    @patch('gcs.gcloud_utils.gcloud_storage_cp')
    @patch('builtins.open', mock_open(read_data='{"test": "data"}'))
    @patch('gcs.tempfile.NamedTemporaryFile')
    def test_download_json(self, mock_temp, mock_gcloud_cp):
        """Test JSON download from GCS"""
        mock_temp_file = MagicMock()
        mock_temp_file.name = '/tmp/test.json'
        mock_temp_file.__enter__.return_value = mock_temp_file
        mock_temp.return_value = mock_temp_file
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_gcloud_cp.return_value = mock_result
        
        result = gcs.download_json('gs://bucket/file.json')
        
        self.assertEqual(result, {'test': 'data'})
        mock_gcloud_cp.assert_called_once()
    
    @patch('gcs.gcloud_utils.gcloud_storage_cp')
    @patch('gcs.os.path.exists', return_value=True)
    def test_upload_test_cases(self, mock_exists, mock_gcloud_cp):
        """Test uploading test cases CSV"""
        gcs.upload_test_cases('/local/test-cases.csv', 'gs://bucket/base')
        
        mock_gcloud_cp.assert_called_once_with(
            '/local/test-cases.csv',
            'gs://bucket/base/test-cases.csv',
            retries=1,
            check=True
        )
    
    @patch('gcs.gcloud_utils.gcloud_storage_ls')
    def test_list_manifests(self, mock_gcloud_ls):
        """Test listing manifest files"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "gs://bucket/bench-123/results/vm-1/manifest.json\n"
            "gs://bucket/bench-123/results/vm-2/manifest.json\n"
        )
        mock_gcloud_ls.return_value = mock_result
        
        manifests = gcs.list_manifests('bench-123', 'bucket')
        
        self.assertEqual(len(manifests), 2)
        self.assertIn('gs://bucket/bench-123/results/vm-1/manifest.json', manifests)
    
    @patch('gcs.gcloud_utils.gcloud_storage_ls')
    def test_check_cancellation_exists(self, mock_gcloud_ls):
        """Test checking for cancellation flag"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_gcloud_ls.return_value = mock_result
        
        result = gcs.check_cancellation('bench-123', 'bucket')
        
        self.assertTrue(result)
    
    @patch('gcs.gcloud_utils.gcloud_storage_ls')
    def test_check_cancellation_not_exists(self, mock_gcloud_ls):
        """Test checking for non-existent cancellation flag"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_gcloud_ls.return_value = mock_result
        
        result = gcs.check_cancellation('bench-123', 'bucket')
        
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
