#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import io
import unittest
from unittest.mock import patch, MagicMock
import build_images


class TestBuildImages(unittest.TestCase):

    @patch('urllib.request.urlopen')
    def test_resolve_latest_gcsfuse_version_redirect(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://github.com/GoogleCloudPlatform/gcsfuse/releases/tag/v3.11.2"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        version = build_images.resolve_latest_gcsfuse_version()
        self.assertEqual(version, "v3.11.2")

    @patch('urllib.request.urlopen')
    def test_resolve_latest_gcsfuse_version_api_fallback(self, mock_urlopen):
        mock_resp_redirect = MagicMock()
        mock_resp_redirect.geturl.return_value = "https://github.com/GoogleCloudPlatform/gcsfuse/releases"
        
        mock_resp_api = MagicMock()
        mock_resp_api.read.return_value = b'{"tag_name": "v3.11.2"}'

        mock_urlopen.return_value.__enter__.side_effect = [mock_resp_redirect, mock_resp_api]

        version = build_images.resolve_latest_gcsfuse_version()
        self.assertEqual(version, "v3.11.2")

    @patch('urllib.request.urlopen')
    def test_resolve_latest_gcsfuse_version_failure_fallback(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network unreachable")
        version = build_images.resolve_latest_gcsfuse_version()
        self.assertEqual(version, "v3.11.2")

    @patch('urllib.request.urlopen')
    def test_resolve_go_version_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"module github.com/googlecloudplatform/gcsfuse/v3\n\ngo 1.26.5\n\nrequire (\n)"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        version = build_images.resolve_go_version("v3.11.2")
        self.assertEqual(version, "1.26.5")

    def test_resolve_go_version_invalid_input(self):
        self.assertIsNone(build_images.resolve_go_version("../invalid"))
        self.assertIsNone(build_images.resolve_go_version("v3.11.2; rm -rf /"))

    @patch('urllib.request.urlopen')
    def test_resolve_go_version_http_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://dummy", code=404, msg="Not Found", hdrs={}, fp=io.BytesIO()
        )
        self.assertIsNone(build_images.resolve_go_version("nonexistent-tag"))

    @patch('build_images._run_builds')
    @patch('build_images.resolve_go_version', return_value="1.26.5")
    @patch('build_images.resolve_latest_gcsfuse_version', return_value="v3.11.2")
    def test_main_default_to_latest_release_and_image_tag(self, mock_resolve_latest, mock_resolve_go, mock_run_builds):
        with patch('sys.argv', ['build_images.py']):
            build_images.main()
            mock_resolve_latest.assert_called_once()
            mock_resolve_go.assert_called_once_with('v3.11.2')
            args = mock_run_builds.call_args[0][0]
            self.assertEqual(args.gcsfuse_version, "v3.11.2")
            self.assertEqual(args.image_version, "v3.11.2")
            self.assertEqual(args.go_version, "1.26.5")

    @patch('build_images._run_builds')
    @patch('build_images.resolve_go_version', return_value="1.26.5")
    def test_main_dynamic_go_version_resolution(self, mock_resolve, mock_run_builds):
        with patch('sys.argv', ['build_images.py', '--gcsfuse-version', 'v3.11.2']):
            build_images.main()
            mock_resolve.assert_called_once_with('v3.11.2')
            args = mock_run_builds.call_args[0][0]
            self.assertEqual(args.go_version, "1.26.5")
            self.assertEqual(args.gcsfuse_version, "v3.11.2")
            self.assertEqual(args.image_version, "v3.11.2")

    @patch('build_images._run_builds')
    @patch('build_images.resolve_go_version')
    def test_main_explicit_go_version(self, mock_resolve, mock_run_builds):
        with patch('sys.argv', ['build_images.py', '--gcsfuse-version', 'v3.11.2', '--go-version', '1.27.0', '--image-version', 'custom-1']):
            build_images.main()
            mock_resolve.assert_not_called()
            args = mock_run_builds.call_args[0][0]
            self.assertEqual(args.go_version, "1.27.0")
            self.assertEqual(args.image_version, "custom-1")

    @patch('build_images._run_builds')
    @patch('build_images.resolve_go_version', return_value=None)
    def test_main_fallback_go_version(self, mock_resolve, mock_run_builds):
        with patch('sys.argv', ['build_images.py', '--gcsfuse-version', 'unknown-tag']):
            build_images.main()
            mock_resolve.assert_called_once_with('unknown-tag')
            args = mock_run_builds.call_args[0][0]
            self.assertEqual(args.go_version, "1.26.5")


if __name__ == '__main__':
    unittest.main()
