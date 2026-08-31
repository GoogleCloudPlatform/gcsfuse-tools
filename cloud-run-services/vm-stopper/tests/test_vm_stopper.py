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

"""Comprehensive offline unit test suite for VM Stopper."""

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
import types
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

# Ensure third-party modules can be imported/mocked offline
def _setup_offline_mock_modules() -> None:
    if "google" not in sys.modules:
        try:
            import google
        except ImportError:
            sys.modules["google"] = types.ModuleType("google")

    if "google.auth" not in sys.modules or not hasattr(sys.modules.get("google", None), "auth"):
        try:
            import google.auth
        except ImportError:
            auth_mod = types.ModuleType("google.auth")
            auth_mod.default = MagicMock(return_value=(MagicMock(), None))
            sys.modules["google.auth"] = auth_mod
            if "google" in sys.modules:
                setattr(sys.modules["google"], "auth", auth_mod)

    if "google.auth.credentials" not in sys.modules or not hasattr(sys.modules.get("google.auth", None), "credentials"):
        try:
            import google.auth.credentials
        except ImportError:
            cred_mod = types.ModuleType("google.auth.credentials")
            cred_mod.Credentials = MagicMock
            sys.modules["google.auth.credentials"] = cred_mod
            if "google.auth" in sys.modules:
                setattr(sys.modules["google.auth"], "credentials", cred_mod)

    if "google.auth.transport" not in sys.modules or not hasattr(sys.modules.get("google.auth", None), "transport"):
        try:
            import google.auth.transport
        except ImportError:
            trans_mod = types.ModuleType("google.auth.transport")
            sys.modules["google.auth.transport"] = trans_mod
            if "google.auth" in sys.modules:
                setattr(sys.modules["google.auth"], "transport", trans_mod)

    if "google.auth.transport.requests" not in sys.modules or not hasattr(sys.modules.get("google.auth.transport", None), "requests"):
        try:
            import google.auth.transport.requests
        except ImportError:
            req_mod = types.ModuleType("google.auth.transport.requests")
            req_mod.Request = MagicMock
            sys.modules["google.auth.transport.requests"] = req_mod
            if "google.auth.transport" in sys.modules:
                setattr(sys.modules["google.auth.transport"], "requests", req_mod)

    if "google.cloud" not in sys.modules or not hasattr(sys.modules.get("google", None), "cloud"):
        try:
            import google.cloud
        except ImportError:
            sys.modules["google.cloud"] = types.ModuleType("google.cloud")
            if "google" in sys.modules:
                setattr(sys.modules["google"], "cloud", sys.modules["google.cloud"])

    if "google.cloud.compute_v1" not in sys.modules or not hasattr(sys.modules.get("google.cloud", None), "compute_v1"):
        try:
            import google.cloud.compute_v1
        except (ImportError, AttributeError):
            compute_mod = types.ModuleType("google.cloud.compute_v1")
            compute_mod.InstancesClient = MagicMock
            compute_mod.StopInstanceRequest = MagicMock
            compute_mod.DeleteInstanceRequest = MagicMock
            sys.modules["google.cloud.compute_v1"] = compute_mod
            if "google.cloud" in sys.modules:
                setattr(sys.modules["google.cloud"], "compute_v1", compute_mod)

    if "google.cloud.logging_v2" not in sys.modules or not hasattr(sys.modules.get("google.cloud", None), "logging_v2"):
        try:
            import google.cloud.logging_v2
        except (ImportError, AttributeError):
            logging_mod = types.ModuleType("google.cloud.logging_v2")
            logging_mod.Client = MagicMock
            sys.modules["google.cloud.logging_v2"] = logging_mod
            if "google.cloud" in sys.modules:
                setattr(sys.modules["google.cloud"], "logging_v2", logging_mod)

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

                def route(self, rule: str, **options: Any):

                    def decorator(f: Any) -> Any:
                        self.routes[rule] = f
                        return f

                    return decorator

                def test_client(self):
                    return MagicMock()

            flask_mock.Flask = MockFlask
            flask_mock.jsonify = lambda d: d
            flask_mock.request = MagicMock()
            sys.modules["flask"] = flask_mock


_setup_offline_mock_modules()

# Ensure package root is in sys.path for test discovery
_PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from stopper.config import (
    StopperConfig,
    _parse_bool,
    _parse_dict,
    _parse_int,
    _parse_list,
)
from stopper.gce_client import GCEClient
from stopper.service import process_request
from stopper.vm_processor import (
    VMProcessor,
    is_part_of_gke_or_mig,
    is_whitelisted,
    parse_timestamp,
)
import main


class MockMetadataItem:
    """Mock compute_v1.Items protobuf object."""

    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value


class MockMetadata:
    """Mock compute_v1.Metadata object."""

    def __init__(self, items=None):
        self.items = items or []


class MockTags:
    """Mock compute_v1.Tags object."""

    def __init__(self, items=None):
        self.items = items or []


class MockInstance:
    """Mock GCE compute_v1.Instance object."""

    def __init__(
        self,
        name: str,
        instance_id: str = "123456789",
        status: str = "RUNNING",
        creation_timestamp: str = "2026-08-01T00:00:00.000Z",
        last_stop_timestamp: str = "",
        last_suspended_timestamp: str = "",
        labels: dict = None,
        metadata_items: list = None,
        metadata: Any = None,
        tags: list = None,
    ):
        self.name = name
        self.id = instance_id
        self.status = status
        self.creation_timestamp = creation_timestamp
        self.last_stop_timestamp = last_stop_timestamp
        self.last_suspended_timestamp = last_suspended_timestamp
        self.labels = labels or {}
        if metadata is not None:
            if isinstance(metadata, dict):
                items = [MockMetadataItem(k, v) for k, v in metadata.items()]
                self.metadata = MockMetadata(items)
            elif isinstance(metadata, MockMetadata):
                self.metadata = metadata
            else:
                self.metadata = MockMetadata(metadata)
        else:
            self.metadata = MockMetadata(metadata_items or [])
        self.tags = MockTags(tags or [])


class TestStopperConfig(unittest.TestCase):
    """Test dynamic configuration parsing and hierarchy resolution."""

    def test_default_config_from_payload(self):
        payload = {"project": "test-project-123"}
        config = StopperConfig.from_request(request_data=payload)
        self.assertEqual(config.project_id, "test-project-123")
        self.assertEqual(config.idle_days_threshold, 7)
        self.assertEqual(config.stopped_days_threshold, 90)
        self.assertFalse(config.delete_stopped_vms)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.max_workers, 20)

    def test_config_overrides_from_payload(self):
        payload = {
            "project_id": "custom-project",
            "idle_days_threshold": 14,
            "stopped_days_threshold": 60,
            "delete_stopped_vms": True,
            "dry_run": True,
            "max_workers": 10,
            "exclude_label_keys": ["custom-keep", "no-touch"],
            "exclude_label_values": {"tier": "prod"},
            "whitelist_names": ["bastion-vm", "db-leader"],
            "whitelist_tags": ["safe-tag"],
        }
        config = StopperConfig.from_request(request_data=payload)
        self.assertEqual(config.project_id, "custom-project")
        self.assertEqual(config.idle_days_threshold, 14)
        self.assertEqual(config.stopped_days_threshold, 60)
        self.assertTrue(config.delete_stopped_vms)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.max_workers, 10)
        self.assertEqual(config.exclude_label_keys, ["custom-keep", "no-touch"])
        self.assertEqual(config.exclude_label_values, {"tier": "prod"})
        self.assertEqual(config.whitelist_names, ["bastion-vm", "db-leader"])
        self.assertEqual(config.whitelist_tags, ["safe-tag"])

    def test_config_from_query_args(self):
        query_args = {
            "project": "query-proj",
            "idle_days": "5",
            "delete_stopped": "true",
            "dry_run": "1",
        }
        config = StopperConfig.from_request(query_args=query_args)
        self.assertEqual(config.project_id, "query-proj")
        self.assertEqual(config.idle_days_threshold, 5)
        self.assertTrue(config.delete_stopped_vms)
        self.assertTrue(config.dry_run)

    def test_config_from_env_vars(self):
        env = {
            "PROJECT_ID": "env-project",
            "IDLE_DAYS_THRESHOLD": "10",
            "STOPPED_DAYS_THRESHOLD": "45",
            "DELETE_STOPPED_VMS": "true",
            "DRY_RUN": "true",
            "MAX_WORKERS": "8",
            "EXCLUDE_LABEL_KEYS": "keep-1,keep-2",
        }
        config = StopperConfig.from_request(env=env)
        self.assertEqual(config.project_id, "env-project")
        self.assertEqual(config.idle_days_threshold, 10)
        self.assertEqual(config.stopped_days_threshold, 45)
        self.assertTrue(config.delete_stopped_vms)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.max_workers, 8)
        self.assertEqual(config.exclude_label_keys, ["keep-1", "keep-2"])

    def test_resolution_hierarchy(self):
        payload = {"project": "payload-proj", "dry_run": False}
        query_args = {"project": "query-proj", "dry_run": "true", "idle_days": "3"}
        env = {"PROJECT_ID": "env-proj", "IDLE_DAYS": "20", "DELETE_STOPPED_VMS": "true"}

        config = StopperConfig.from_request(request_data=payload, query_args=query_args, env=env)
        # Payload takes precedence for project and dry_run
        self.assertEqual(config.project_id, "payload-proj")
        self.assertFalse(config.dry_run)
        # Query args takes precedence for idle_days
        self.assertEqual(config.idle_days_threshold, 3)
        # Env falls through for delete_stopped_vms
        self.assertTrue(config.delete_stopped_vms)

    @patch("google.auth.default", return_value=(None, "adc-project-id"))
    def test_adc_fallback(self, mock_auth):
        config = StopperConfig.from_request(request_data={}, query_args={}, env={})
        self.assertEqual(config.project_id, "adc-project-id")

    @patch("google.auth.default", return_value=(None, None))
    def test_missing_project_raises_error(self, mock_auth):
        with self.assertRaises(ValueError) as ctx:
            StopperConfig.from_request(request_data={}, query_args={}, env={})
        self.assertIn("Missing target GCP Project ID", str(ctx.exception))

    def test_invalid_thresholds_raise_error(self):
        with self.assertRaises(ValueError):
            cfg = StopperConfig(project_id="test", idle_days_threshold=0)
            cfg.validate()

        with self.assertRaises(ValueError):
            cfg = StopperConfig(project_id="test", stopped_days_threshold=-1)
            cfg.validate()

        with self.assertRaises(ValueError):
            cfg = StopperConfig(project_id="test", max_workers=0)
            cfg.validate()

    def test_helper_parsers(self):
        self.assertTrue(_parse_bool("yes"))
        self.assertTrue(_parse_bool("True"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool(True))
        self.assertFalse(_parse_bool("no"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool(None, default=False))

        self.assertEqual(_parse_int("15", default=5), 15)
        self.assertEqual(_parse_int("invalid", default=5), 5)
        self.assertEqual(_parse_int("-3", default=5, min_val=1), 1)

        self.assertEqual(_parse_list("a, b, c"), ["a", "b", "c"])
        self.assertEqual(_parse_list('["x", "y"]'), ["x", "y"])
        self.assertEqual(_parse_list(["foo", "bar"]), ["foo", "bar"])

        self.assertEqual(_parse_dict('{"k": "v"}'), {"k": "v"})
        self.assertEqual(_parse_dict({"a": 1}), {"a": "1"})


class TestGkeAndMigFiltering(unittest.TestCase):
    """Test detection and exclusion of GKE nodes and MIG instances."""

    def test_gke_name_prefix(self):
        inst1 = MockInstance(name="gke-cluster-pool-1-abc")
        inst2 = MockInstance(name="gk3-cluster-pool-2-def")
        normal_inst = MockInstance(name="standalone-vm-1")

        self.assertTrue(is_part_of_gke_or_mig(inst1))
        self.assertTrue(is_part_of_gke_or_mig(inst2))
        self.assertFalse(is_part_of_gke_or_mig(normal_inst))

    def test_gke_labels(self):
        inst_k8s = MockInstance(name="worker-1", labels={"goog-k8s-node-pool-name": "default-pool"})
        inst_gke = MockInstance(name="worker-2", labels={"goog-gke-version": "1.28"})
        inst_custom_gke = MockInstance(name="worker-3", labels={"gke-addon": "true"})
        normal_inst = MockInstance(name="worker-4", labels={"env": "dev", "owner": "test"})

        self.assertTrue(is_part_of_gke_or_mig(inst_k8s))
        self.assertTrue(is_part_of_gke_or_mig(inst_gke))
        self.assertTrue(is_part_of_gke_or_mig(inst_custom_gke))
        self.assertFalse(is_part_of_gke_or_mig(normal_inst))

    def test_gke_metadata(self):
        for key in ["cluster-name", "cluster-location", "gke-nodepool", "kube-env", "instance-template"]:
            inst = MockInstance(name="node-x", metadata_items=[MockMetadataItem(key, "val")])
            self.assertTrue(is_part_of_gke_or_mig(inst), f"Failed for metadata key {key}")

    def test_mig_created_by(self):
        inst_mig = MockInstance(
            name="mig-instance-1",
            metadata_items=[
                MockMetadataItem(
                    "created-by",
                    "projects/123/zones/us-central1-a/instanceGroupManagers/my-ig",
                )
            ],
        )
        self.assertTrue(is_part_of_gke_or_mig(inst_mig))

        inst_region_mig = MockInstance(
            name="mig-instance-2",
            metadata_items=[
                MockMetadataItem(
                    "created-by",
                    "projects/123/regions/us-central1/regionInstanceGroupManagers/my-rig",
                )
            ],
        )
        self.assertTrue(is_part_of_gke_or_mig(inst_region_mig))

    def test_tags_filtering(self):
        inst_gke_tag = MockInstance(name="custom-node", tags=["gke-cluster-node", "http-server"])
        inst_k8s_tag = MockInstance(name="custom-node-2", tags=["k8s-node"])
        inst_mig_tag = MockInstance(name="custom-node-3", tags=["mig-worker"])
        normal_inst = MockInstance(name="custom-node-4", tags=["http-server", "https-server"])

        self.assertTrue(is_part_of_gke_or_mig(inst_gke_tag))
        self.assertTrue(is_part_of_gke_or_mig(inst_k8s_tag))
        self.assertTrue(is_part_of_gke_or_mig(inst_mig_tag))
        self.assertFalse(is_part_of_gke_or_mig(normal_inst))


class TestWhitelistFiltering(unittest.TestCase):
    """Test user-specified whitelist and exclusion rules."""

    def setUp(self):
        self.config = StopperConfig(
            project_id="test-proj",
            exclude_label_keys=["keep-alive", "do-not-stop"],
            exclude_label_values={"env": "production"},
            whitelist_names=["bastion", "leader-node"],
            whitelist_tags=["permanent-vm", "do-not-delete"],
        )

    def test_whitelist_by_name(self):
        inst1 = MockInstance(name="bastion")
        inst2 = MockInstance(name="my-leader-node-prod")
        inst3 = MockInstance(name="random-worker")

        whitelisted1, _ = is_whitelisted(inst1, self.config)
        whitelisted2, _ = is_whitelisted(inst2, self.config)
        whitelisted3, _ = is_whitelisted(inst3, self.config)

        self.assertTrue(whitelisted1)
        self.assertTrue(whitelisted2)
        self.assertFalse(whitelisted3)

    def test_whitelist_by_label_key(self):
        inst1 = MockInstance(name="vm-1", labels={"keep-alive": "true"})
        inst2 = MockInstance(name="vm-2", labels={"do-not-stop": "1"})
        inst3 = MockInstance(name="vm-3", labels={"ephemeral": "true"})

        self.assertTrue(is_whitelisted(inst1, self.config)[0])
        self.assertTrue(is_whitelisted(inst2, self.config)[0])
        self.assertFalse(is_whitelisted(inst3, self.config)[0])

    def test_whitelist_by_label_value(self):
        inst1 = MockInstance(name="vm-1", labels={"env": "production"})
        inst2 = MockInstance(name="vm-2", labels={"env": "staging"})

        self.assertTrue(is_whitelisted(inst1, self.config)[0])
        self.assertFalse(is_whitelisted(inst2, self.config)[0])

    def test_whitelist_by_tag(self):
        inst1 = MockInstance(name="vm-1", tags=["permanent-vm"])
        inst2 = MockInstance(name="vm-2", tags=["test-vm"])
        inst3 = MockInstance(name="vm-3", tags=["do-not-delete"])

        self.assertTrue(is_whitelisted(inst1, self.config)[0])
        self.assertFalse(is_whitelisted(inst2, self.config)[0])
        self.assertTrue(is_whitelisted(inst3, self.config)[0])

    def test_whitelist_by_metadata_and_auto_stop_flag(self):
        inst_meta = MockInstance(name="vm-meta", metadata={"keep-alive": "true"})
        inst_disable = MockInstance(name="vm-disable", labels={"auto-stop": "false"})
        inst_disable_meta = MockInstance(name="vm-meta-disable", metadata={"auto-delete": "0"})
        inst_normal = MockInstance(name="vm-normal", metadata={"startup-script": "echo hello"})

        self.assertTrue(is_whitelisted(inst_meta, self.config)[0])
        self.assertTrue(is_whitelisted(inst_disable, self.config)[0])
        self.assertTrue(is_whitelisted(inst_disable_meta, self.config)[0])
        self.assertFalse(is_whitelisted(inst_normal, self.config)[0])


class TestGCEClientAndCloudLogging(unittest.TestCase):
    """Test GCEClient API calls and Cloud Logging activity inspection."""

    def setUp(self):
        self.client = GCEClient()

    @patch("stopper.gce_client.compute_v1.InstancesClient")
    def test_list_instances(self, mock_instances_cls):
        mock_instances_client = MagicMock()
        mock_instances_cls.return_value = mock_instances_client
        self.client._instances_client = mock_instances_client

        scoped_1 = MagicMock()
        scoped_1.instances = [MockInstance("vm-a1"), MockInstance("vm-a2")]

        scoped_2 = MagicMock()
        scoped_2.instances = [MockInstance("vm-b1")]

        scoped_empty = MagicMock()
        scoped_empty.instances = []

        mock_instances_client.aggregated_list.return_value = [
            ("zones/us-central1-a", scoped_1),
            ("zones/us-central1-b", scoped_2),
            ("zones/us-central1-c", scoped_empty),
        ]

        result = self.client.list_instances("test-proj")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], ("us-central1-a", scoped_1.instances[0]))
        self.assertEqual(result[1], ("us-central1-a", scoped_1.instances[1]))
        self.assertEqual(result[2], ("us-central1-b", scoped_2.instances[0]))

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_found(self, mock_logging_cls):
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        # Return non-empty generator
        mock_logging_client.list_entries.return_value = [MagicMock()]

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="idle-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(has_activity)
        mock_logging_client.list_entries.assert_called_once()

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_not_found(self, mock_logging_cls):
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        # Return empty generator
        mock_logging_client.list_entries.return_value = []

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="idle-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(has_activity)

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_exception_fallback(self, mock_logging_cls):
        """Verify fail-safe fallback: assume ACTIVE when Cloud Logging raises an error."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        mock_logging_client.list_entries.side_effect = Exception("Permission denied on logs")

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="err-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        # MUST return True for safety
        self.assertTrue(has_activity)

    @patch("stopper.gce_client.compute_v1.InstancesClient")
    def test_stop_and_delete_instance(self, mock_instances_cls):
        mock_instances_client = MagicMock()
        mock_instances_cls.return_value = mock_instances_client
        self.client._instances_client = mock_instances_client

        mock_op = MagicMock()
        mock_instances_client.stop.return_value = mock_op
        mock_instances_client.delete.return_value = mock_op

        self.client.stop_instance("proj", "us-central1-a", "vm-1")
        mock_instances_client.stop.assert_called_once_with(project="proj", zone="us-central1-a", instance="vm-1")
        mock_op.result.assert_called_once_with(timeout=300)

        self.client.delete_instance("proj", "us-central1-a", "vm-2")
        mock_instances_client.delete.assert_called_once_with(project="proj", zone="us-central1-a", instance="vm-2")


class TestVMProcessorLifecycle(unittest.TestCase):
    """Test end-to-end VM evaluation, stopping, deleting, and dry-run sweeps."""

    def setUp(self):
        self.mock_client = MagicMock(spec=GCEClient)
        self.now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_young_running_vm_is_skipped(self):
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7)
        processor = VMProcessor(config, gce_client=self.mock_client)

        # Created 2 days ago
        created_ts = (self.now - timedelta(days=2)).isoformat()
        young_vm = MockInstance(name="young-vm", status="RUNNING", creation_timestamp=created_ts)

        res = processor.process_single_instance("us-central1-a", young_vm, self.now)
        self.assertEqual(res["category"], "skipped_recently_created")
        self.assertEqual(res["action"], "none")
        self.mock_client.has_recent_activity.assert_not_called()
        self.mock_client.stop_instance.assert_not_called()

    def test_active_running_vm_is_skipped(self):
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7)
        processor = VMProcessor(config, gce_client=self.mock_client)

        # Created 15 days ago
        created_ts = (self.now - timedelta(days=15)).isoformat()
        active_vm = MockInstance(name="active-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = True

        res = processor.process_single_instance("us-central1-a", active_vm, self.now)
        self.assertEqual(res["category"], "skipped_active")
        self.assertEqual(res["action"], "none")
        self.mock_client.has_recent_activity.assert_called_once()
        self.mock_client.stop_instance.assert_not_called()

    def test_idle_running_vm_is_stopped(self):
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7, dry_run=False)
        processor = VMProcessor(config, gce_client=self.mock_client)

        # Created 15 days ago, no login
        created_ts = (self.now - timedelta(days=15)).isoformat()
        idle_vm = MockInstance(name="idle-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False

        res = processor.process_single_instance("us-central1-a", idle_vm, self.now)
        self.assertEqual(res["category"], "stopped")
        self.assertEqual(res["action"], "stopped")
        self.assertIn("Stopped idle running VM", res["reason"])
        self.mock_client.stop_instance.assert_called_once_with("test-proj", "us-central1-a", "idle-vm")

    def test_idle_running_vm_dry_run(self):
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7, dry_run=True)
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        idle_vm = MockInstance(name="idle-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False

        res = processor.process_single_instance("us-central1-a", idle_vm, self.now)
        self.assertEqual(res["category"], "dry_run_stops")
        self.assertEqual(res["action"], "dry_run_stop")
        self.assertIn("[DRY RUN] Would stop idle running VM", res["reason"])
        self.mock_client.stop_instance.assert_not_called()

    def test_stopped_vm_retained_when_delete_disabled(self):
        config = StopperConfig(project_id="test-proj", delete_stopped_vms=False)
        processor = VMProcessor(config, gce_client=self.mock_client)

        stopped_vm = MockInstance(
            name="stopped-vm",
            status="TERMINATED",
            last_stop_timestamp=(self.now - timedelta(days=120)).isoformat(),
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "skipped_stopped")
        self.assertEqual(res["action"], "none")
        self.mock_client.delete_instance.assert_not_called()

    def test_recently_stopped_vm_retained_when_delete_enabled(self):
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        # Stopped 30 days ago
        stopped_vm = MockInstance(
            name="stopped-vm",
            status="TERMINATED",
            last_stop_timestamp=(self.now - timedelta(days=30)).isoformat(),
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "skipped_stopped")
        self.mock_client.delete_instance.assert_not_called()

    def test_long_stopped_vm_deleted_when_enabled(self):
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=False,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        # Stopped 100 days ago
        stopped_vm = MockInstance(
            name="old-stopped-vm",
            status="TERMINATED",
            last_stop_timestamp=(self.now - timedelta(days=100)).isoformat(),
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "deleted")
        self.assertEqual(res["action"], "deleted")
        self.assertIn("Deleted long-stopped VM", res["reason"])
        self.mock_client.delete_instance.assert_called_once_with("test-proj", "us-central1-a", "old-stopped-vm")

    def test_long_stopped_vm_dry_run_delete(self):
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=True,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        stopped_vm = MockInstance(
            name="old-stopped-vm",
            status="TERMINATED",
            last_stop_timestamp=(self.now - timedelta(days=100)).isoformat(),
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "dry_run_deletions")
        self.assertEqual(res["action"], "dry_run_delete")
        self.assertIn("[DRY RUN] Would delete stopped VM", res["reason"])
        self.mock_client.delete_instance.assert_not_called()

    def test_stop_instance_api_error_handling(self):
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7, dry_run=False)
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        idle_vm = MockInstance(name="error-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False
        self.mock_client.stop_instance.side_effect = Exception("GCP Quota Exceeded")

        res = processor.process_single_instance("us-central1-a", idle_vm, self.now)
        self.assertEqual(res["category"], "errors_count")
        self.assertEqual(res["action"], "error")
        self.assertIn("GCP Quota Exceeded", res["error"])

    def test_full_sweep_orchestration(self):
        config = StopperConfig(
            project_id="test-proj",
            idle_days_threshold=7,
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=False,
            max_workers=4,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        gke_vm = MockInstance("gke-node-1", status="RUNNING")
        whitelisted_vm = MockInstance("protected-vm", status="RUNNING", labels={"keep-alive": "true"})
        young_vm = MockInstance("young-vm", status="RUNNING", creation_timestamp=(self.now - timedelta(days=2)).isoformat())
        active_vm = MockInstance("active-vm", status="RUNNING", creation_timestamp=(self.now - timedelta(days=20)).isoformat())
        idle_vm = MockInstance("idle-vm", status="RUNNING", creation_timestamp=(self.now - timedelta(days=20)).isoformat())
        stopped_recent_vm = MockInstance("stopped-recent", status="TERMINATED", last_stop_timestamp=(self.now - timedelta(days=10)).isoformat())
        stopped_old_vm = MockInstance("stopped-old", status="TERMINATED", last_stop_timestamp=(self.now - timedelta(days=100)).isoformat())

        self.mock_client.list_instances.return_value = [
            ("us-central1-a", gke_vm),
            ("us-central1-a", whitelisted_vm),
            ("us-central1-b", young_vm),
            ("us-central1-b", active_vm),
            ("us-central1-b", idle_vm),
            ("us-central1-c", stopped_recent_vm),
            ("us-central1-c", stopped_old_vm),
        ]

        def mock_activity(project_id, zone, instance_name, instance_id, since_timestamp):
            return instance_name == "active-vm"

        self.mock_client.has_recent_activity.side_effect = mock_activity

        response = processor.sweep()

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["project_id"], "test-proj")
        summary = response["summary"]
        self.assertEqual(summary["total_scanned"], 7)
        self.assertEqual(summary["skipped_gke_mig"], 1)
        self.assertEqual(summary["skipped_whitelisted"], 1)
        self.assertEqual(summary["skipped_recently_created"], 1)
        self.assertEqual(summary["skipped_active"], 1)
        self.assertEqual(summary["stopped"], 1)
        self.assertEqual(summary["skipped_stopped"], 1)
        self.assertEqual(summary["deleted"], 1)
        self.assertEqual(summary["errors_count"], 0)

        self.mock_client.stop_instance.assert_called_once_with("test-proj", "us-central1-b", "idle-vm")
        self.mock_client.delete_instance.assert_called_once_with("test-proj", "us-central1-c", "stopped-old")


class TestHTTPServiceAndMain(unittest.TestCase):
    """Test HTTP handlers, Flask app endpoints, and Functions Framework routing."""

    def setUp(self):
        self.app = main.app
        self.client = self.app.test_client()

    def test_healthz_endpoint(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "vm-stopper")

    @patch("stopper.service.VMProcessor")
    def test_flask_post_valid_payload(self, mock_processor_cls):
        mock_proc = MagicMock()
        mock_processor_cls.return_value = mock_proc
        mock_proc.sweep.return_value = {
            "status": "success",
            "service": "vm-stopper",
            "project_id": "flask-proj",
            "dry_run": True,
            "summary": {"total_scanned": 10, "stopped": 0, "errors_count": 0},
            "actions_taken": [],
            "errors": [],
        }

        response = self.client.post(
            "/",
            data=json.dumps({"project": "flask-proj", "dry_run": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["project_id"], "flask-proj")
        self.assertTrue(data["dry_run"])

    @patch("google.auth.default", return_value=(None, None))
    def test_flask_post_missing_project_returns_400(self, mock_auth):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/", data=json.dumps({}), content_type="application/json")
            self.assertEqual(response.status_code, 400)
            data = json.loads(response.data)
            self.assertEqual(data["status"], "error")
            self.assertIn("Missing target GCP Project ID", data["error"])

    @patch("stopper.service.VMProcessor")
    def test_functions_framework_handler(self, mock_processor_cls):
        mock_proc = MagicMock()
        mock_processor_cls.return_value = mock_proc
        mock_proc.sweep.return_value = {
            "status": "success",
            "service": "vm-stopper",
            "project_id": "gcf-proj",
            "summary": {},
        }

        mock_req = MagicMock()
        mock_req.args = {"project": "gcf-proj"}
        mock_req.get_json.return_value = None

        with self.app.app_context():
            resp, status = main.check_and_stop_idle_vms(mock_req)
            self.assertEqual(status, 200)


class TestDeploymentScriptSyntax(unittest.TestCase):
    """Static validation of deploy.sh syntax and CLI flags."""

    def test_bash_n_syntax(self):
        deploy_sh = os.path.join(os.path.dirname(__file__), "..", "deploy.sh")
        result = subprocess.run(["bash", "-n", deploy_sh], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"bash -n failed: {result.stderr}")

    def test_deploy_sh_help_flag(self):
        deploy_sh = os.path.join(os.path.dirname(__file__), "..", "deploy.sh")
        result = subprocess.run(["bash", deploy_sh, "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--project", result.stdout)
        self.assertIn("--schedule", result.stdout)


if __name__ == "__main__":
    unittest.main()
