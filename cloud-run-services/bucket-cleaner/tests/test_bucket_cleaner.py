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

"""100% Offline Mock Unit Tests for Bucket Cleaner."""

import datetime
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _setup_offline_mock_modules() -> None:
    """Mock external libraries if not installed in the current environment."""
    if "functions_framework" not in sys.modules:
        try:
            import functions_framework
        except ImportError:
            ff_mock = types.ModuleType("functions_framework")
            ff_mock.http = lambda f: f
            sys.modules["functions_framework"] = ff_mock

    if "flask" not in sys.modules:
        try:
            import flask
        except ImportError:
            flask_mock = MagicMock()

            class MockFlask:
                def __init__(self, name: str):
                    self.name = name
                    self.routes = {}

                def route(self, rule: str, **options):
                    def decorator(f):
                        self.routes[rule] = f
                        return f
                    return decorator

                def test_client(self):
                    client = MagicMock()
                    client.get = MagicMock(return_value=MagicMock(status_code=200, json={"status": "healthy", "service": "bucket-cleaner"}))
                    return client

            flask_mock.Flask = MockFlask
            flask_mock.jsonify = lambda d: d
            flask_mock.request = MagicMock()
            sys.modules["flask"] = flask_mock

    if "google" not in sys.modules:
        try:
            import google
        except ImportError:
            sys.modules["google"] = types.ModuleType("google")

    if "google.cloud" not in sys.modules or not hasattr(sys.modules.get("google", None), "cloud"):
        try:
            import google.cloud
        except ImportError:
            sys.modules["google.cloud"] = types.ModuleType("google.cloud")
            if "google" in sys.modules:
                setattr(sys.modules["google"], "cloud", sys.modules["google.cloud"])

    if "google.cloud.storage" not in sys.modules or not hasattr(sys.modules.get("google.cloud", None), "storage"):
        try:
            import google.cloud.storage
        except (ImportError, AttributeError):
            storage_mod = types.ModuleType("google.cloud.storage")
            storage_mod.Client = MagicMock
            storage_mod.Bucket = MagicMock
            sys.modules["google.cloud.storage"] = storage_mod
            if "google.cloud" in sys.modules:
                setattr(sys.modules["google.cloud"], "storage", storage_mod)

    if "google.cloud.bigquery" not in sys.modules or not hasattr(sys.modules.get("google.cloud", None), "bigquery"):
        try:
            import google.cloud.bigquery
        except (ImportError, AttributeError):
            bq_mod = types.ModuleType("google.cloud.bigquery")
            bq_mod.Client = MagicMock
            sys.modules["google.cloud.bigquery"] = bq_mod
            if "google.cloud" in sys.modules:
                setattr(sys.modules["google.cloud"], "bigquery", bq_mod)


_setup_offline_mock_modules()

# Ensure package root is in sys.path
_PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from cleaner.config import CleanerConfig
from cleaner.gcs_client import GCSClient
from cleaner.bucket_processor import BucketProcessor
from cleaner.service import process_request
import main


class TestCleanerConfig(unittest.TestCase):
    """Unit tests for CleanerConfig resolution and validation."""

    def test_default_configuration(self):
        config = CleanerConfig()
        self.assertEqual(config.projects, ["gcs-fuse-test", "gcs-fuse-test-ml"])
        self.assertEqual(config.bucket_prefix, "gcsfuse-e2e-")
        self.assertEqual(config.age_days, 3)
        self.assertEqual(config.age_hours, 72)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.concurrency, 32)
        self.assertEqual(config.batch_size, 50)
        self.assertEqual(config.bq_project, "gcs-fuse-test-ml")
        self.assertEqual(config.bq_dataset, "bucket_cleaner_metrics")
        self.assertEqual(config.bq_table, "daily_metrics")
        self.assertTrue(config.enable_bq_logging)

    def test_from_request_json(self):
        req = {
            "projects": ["proj-1", "proj-2"],
            "prefix": "test-prefix-",
            "age_days": 5,
            "dry_run": True,
            "concurrency": 8,
            "batch_size": 20,
        }
        config = CleanerConfig.from_request(request_data=req)
        self.assertEqual(config.projects, ["proj-1", "proj-2"])
        self.assertEqual(config.bucket_prefix, "test-prefix-")
        self.assertEqual(config.age_days, 5)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.concurrency, 8)
        self.assertEqual(config.batch_size, 20)

    def test_from_query_args(self):
        args = {
            "project": "proj-single",
            "age_days": "2",
            "dry_run": "true",
        }
        config = CleanerConfig.from_request(query_args=args)
        self.assertEqual(config.projects, ["proj-single"])
        self.assertEqual(config.age_days, 2)
        self.assertTrue(config.dry_run)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            CleanerConfig(projects=[]).validate()

        with self.assertRaises(ValueError):
            CleanerConfig(age_days=-1).validate()

        with self.assertRaises(ValueError):
            CleanerConfig(concurrency=0).validate()


class TestBucketProcessor(unittest.TestCase):
    """Unit tests for BucketProcessor discovery, age filtering, and deletion."""

    def setUp(self):
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.old_time = self.now - datetime.timedelta(days=4)  # Eligible (> 3 days)
        self.new_time = self.now - datetime.timedelta(days=1)  # Ineligible (< 3 days)

    def _create_mock_bucket(self, name: str, created_at: datetime.datetime):
        b = MagicMock()
        b.name = name
        b.time_created = created_at
        return b

    def test_age_filtering_and_deletion(self):
        mock_gcs_client = MagicMock(spec=GCSClient)

        b_old_1 = self._create_mock_bucket("gcsfuse-e2e-old-1", self.old_time)
        b_old_2 = self._create_mock_bucket("gcsfuse-e2e-old-2", self.old_time)
        b_new = self._create_mock_bucket("gcsfuse-e2e-new", self.new_time)

        mock_gcs_client.list_buckets.return_value = [b_old_1, b_old_2, b_new]

        config = CleanerConfig(projects=["test-proj"], age_days=3, dry_run=False, enable_bq_logging=False)
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()

        self.assertEqual(result["status"], "success")
        summary = result["summary"]
        self.assertEqual(summary["total_scanned"], 3)
        self.assertEqual(summary["total_eligible"], 2)
        self.assertEqual(summary["deleted_count"], 2)
        self.assertEqual(summary["skipped_count"], 1)

        # Ensure delete_bucket was called only on old buckets
        self.assertEqual(mock_gcs_client.delete_bucket.call_count, 2)
        mock_gcs_client.delete_bucket.assert_any_call(b_old_1, force=True)
        mock_gcs_client.delete_bucket.assert_any_call(b_old_2, force=True)

    def test_dry_run_mode(self):
        mock_gcs_client = MagicMock(spec=GCSClient)
        b_old = self._create_mock_bucket("gcsfuse-e2e-old", self.old_time)
        mock_gcs_client.list_buckets.return_value = [b_old]

        config = CleanerConfig(projects=["test-proj"], age_days=3, dry_run=True, enable_bq_logging=False)
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()

        self.assertEqual(result["summary"]["deleted_count"], 1)
        self.assertTrue(result["summary"]["dry_run"])
        # In dry-run, delete_bucket MUST NOT be called
        mock_gcs_client.delete_bucket.assert_not_called()

    def test_deletion_failure_with_olm_fallback(self):
        mock_gcs_client = MagicMock(spec=GCSClient)
        b_old = self._create_mock_bucket("gcsfuse-e2e-failed-bucket", self.old_time)
        mock_gcs_client.list_buckets.return_value = [b_old]
        mock_gcs_client.delete_bucket.side_effect = RuntimeError("409 BucketNotEmpty / ManagedFolders")

        config = CleanerConfig(projects=["test-proj"], age_days=3, dry_run=False, apply_olm_fallback=True, enable_bq_logging=False)
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()

        self.assertEqual(result["summary"]["olm_count"], 1)
        self.assertEqual(result["summary"]["failed_count"], 0)
        self.assertEqual(result["summary"]["deleted_count"], 0)
        # Verify OLM fallback rule was applied
        mock_gcs_client.apply_lifecycle_rule.assert_called_once_with(b_old, age_days=1)

    def test_deletion_failure_without_olm(self):
        mock_gcs_client = MagicMock(spec=GCSClient)
        b_old = self._create_mock_bucket("gcsfuse-e2e-failed-bucket", self.old_time)
        mock_gcs_client.list_buckets.return_value = [b_old]
        mock_gcs_client.delete_bucket.side_effect = RuntimeError("Permission denied")

        config = CleanerConfig(projects=["test-proj"], age_days=3, dry_run=False, apply_olm_fallback=False, enable_bq_logging=False)
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()

        self.assertEqual(result["summary"]["failed_count"], 1)
        self.assertEqual(result["summary"]["olm_count"], 0)
        self.assertEqual(result["summary"]["deleted_count"], 0)
        mock_gcs_client.apply_lifecycle_rule.assert_not_called()

    def test_csv_metrics_generation(self):
        mock_gcs_client = MagicMock(spec=GCSClient)
        b1 = self._create_mock_bucket("gcsfuse-e2e-b1", self.old_time)
        mock_gcs_client.list_buckets.return_value = [b1]

        config = CleanerConfig(projects=["test-proj"], age_days=3, dry_run=False, enable_bq_logging=False)
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()
        self.assertIn("csv_metrics", result)
        self.assertIn("date,project,found,deleted,olm,failed", result["csv_metrics"])
        self.assertIn("test-proj,1,1,0,0", result["csv_metrics"])
        self.assertIn("total,1,1,0,0", result["csv_metrics"])

    @patch("cleaner.bucket_processor.BucketProcessor._record_metrics_to_bigquery")
    def test_bigquery_metrics_recording_called(self, mock_record_bq):
        mock_gcs_client = MagicMock(spec=GCSClient)
        b1 = self._create_mock_bucket("gcsfuse-e2e-b1", self.old_time)
        mock_gcs_client.list_buckets.return_value = [b1]

        config = CleanerConfig(
            projects=["gcs-fuse-test", "gcs-fuse-test-ml"],
            age_days=3,
            dry_run=False,
            enable_bq_logging=True,
            bq_project="gcs-fuse-test-ml",
            bq_dataset="bucket_cleaner_metrics",
            bq_table="daily_metrics",
        )
        processor = BucketProcessor(config=config, gcs_client=mock_gcs_client)

        result = processor.process_all_projects()
        self.assertEqual(result["status"], "success")
        mock_record_bq.assert_called_once()
        args, _ = mock_record_bq.call_args
        # Second argument is project_results dict with 2 projects
        self.assertIn("gcs-fuse-test", args[1])
        self.assertIn("gcs-fuse-test-ml", args[1])


class TestServiceLayer(unittest.TestCase):
    """Unit tests for process_request service entrypoint."""

    def test_process_request_validation_error(self):
        res, code = process_request(request_data={"age_days": -5})
        self.assertEqual(code, 400)
        self.assertEqual(res["status"], "error")

    def test_healthz_endpoint(self):
        client = main.app.test_client()
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
