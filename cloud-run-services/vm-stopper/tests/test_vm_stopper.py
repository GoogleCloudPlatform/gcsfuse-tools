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

    if "google.cloud.monitoring_v3" not in sys.modules or not hasattr(sys.modules.get("google.cloud", None), "monitoring_v3"):
        try:
            import google.cloud.monitoring_v3
        except (ImportError, AttributeError):
            mon_mod = types.ModuleType("google.cloud.monitoring_v3")
            mon_mod.MetricServiceClient = MagicMock

            class MockView:
                FULL = 2

            class MockListTimeSeriesRequest:
                TimeSeriesView = MockView

                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

            mon_mod.ListTimeSeriesRequest = MockListTimeSeriesRequest

            class MockTimeInterval:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

            mon_mod.TimeInterval = MockTimeInterval

            class MockAligner:
                ALIGN_DELTA = "ALIGN_DELTA"

            class MockAggregation:
                Aligner = MockAligner

                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

            mon_mod.Aggregation = MockAggregation

            sys.modules["google.cloud.monitoring_v3"] = mon_mod
            if "google.cloud" in sys.modules:
                setattr(sys.modules["google.cloud"], "monitoring_v3", mon_mod)


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
    _parse_bytes,
    _parse_dict,
    _parse_float,
    _parse_int,
    _parse_list,
)
from stopper.gce_client import GCEClient, RateLimiter, is_rate_limit_error
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


class MockShutdownDetails:
    """Mock compute_v1.Instance.ResourceStatus.ShutdownDetails object."""

    def __init__(self, request_timestamp: str = ""):
        self.request_timestamp = request_timestamp


class MockResourceStatus:
    """Mock compute_v1.Instance.ResourceStatus object."""

    def __init__(self, shutdown_details: Any = None):
        self.shutdown_details = shutdown_details


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
        resource_status: Any = None,
    ):
        self.name = name
        self.id = instance_id
        self.status = status
        self.creation_timestamp = creation_timestamp
        self.last_stop_timestamp = last_stop_timestamp
        self.last_suspended_timestamp = last_suspended_timestamp
        self.resource_status = resource_status
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
        self.assertEqual(config.cloud_logging_batch_size, 25)
        self.assertEqual(config.cloud_logging_rate_limit, 40)
        self.assertEqual(config.cloud_logging_max_retries, 4)
        self.assertEqual(config.cloud_logging_retry_backoff, 2.0)

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
            "cloud_logging_batch_size": 10,
            "cloud_logging_rate_limit": 30,
            "cloud_logging_max_retries": 2,
            "cloud_logging_retry_backoff": 1.5,
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
        self.assertEqual(config.cloud_logging_batch_size, 10)
        self.assertEqual(config.cloud_logging_rate_limit, 30)
        self.assertEqual(config.cloud_logging_max_retries, 2)
        self.assertEqual(config.cloud_logging_retry_backoff, 1.5)

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

    def test_parse_bytes(self):
        # Suffix handling for GiB, GB, G, MiB, MB, M, KiB, KB, K, B (case-insensitive)
        self.assertEqual(_parse_bytes("2GiB"), 2 * 1024 * 1024 * 1024)
        self.assertEqual(_parse_bytes("2gib"), 2 * 1024 * 1024 * 1024)
        self.assertEqual(_parse_bytes("1GB"), 1024 * 1024 * 1024)
        self.assertEqual(_parse_bytes("1gb"), 1024 * 1024 * 1024)
        self.assertEqual(_parse_bytes("3G"), 3 * 1024 * 1024 * 1024)
        self.assertEqual(_parse_bytes("3g"), 3 * 1024 * 1024 * 1024)

        self.assertEqual(_parse_bytes("10MiB"), 10 * 1024 * 1024)
        self.assertEqual(_parse_bytes("10mib"), 10 * 1024 * 1024)
        self.assertEqual(_parse_bytes("5MB"), 5 * 1024 * 1024)
        self.assertEqual(_parse_bytes("5mb"), 5 * 1024 * 1024)
        self.assertEqual(_parse_bytes("8M"), 8 * 1024 * 1024)
        self.assertEqual(_parse_bytes("8m"), 8 * 1024 * 1024)

        self.assertEqual(_parse_bytes("64KiB"), 64 * 1024)
        self.assertEqual(_parse_bytes("64kib"), 64 * 1024)
        self.assertEqual(_parse_bytes("32KB"), 32 * 1024)
        self.assertEqual(_parse_bytes("32kb"), 32 * 1024)
        self.assertEqual(_parse_bytes("16K"), 16 * 1024)
        self.assertEqual(_parse_bytes("16k"), 16 * 1024)

        self.assertEqual(_parse_bytes("1024B"), 1024)
        self.assertEqual(_parse_bytes("512b"), 512)

        # Plain numbers (int, float, numeric string)
        self.assertEqual(_parse_bytes(1048576), 1048576)
        self.assertEqual(_parse_bytes(2097152.0), 2097152)
        self.assertEqual(_parse_bytes("4096"), 4096)
        self.assertEqual(_parse_bytes("0"), 0)
        self.assertEqual(_parse_bytes("-1"), -1)

        # Whitespace handling
        self.assertEqual(_parse_bytes("  10  MB  "), 10 * 1024 * 1024)
        self.assertEqual(_parse_bytes(" 512 B "), 512)
        self.assertEqual(_parse_bytes("   100   "), 100)

        # Ensure internal characters are not trimmed (e.g. strings containing suffix chars)
        self.assertEqual(_parse_bytes("1008B"), 1008)
        self.assertEqual(_parse_bytes("10.5 MB"), int(10.5 * 1024 * 1024))
        self.assertEqual(_parse_bytes("1.5 GiB"), int(1.5 * 1024 * 1024 * 1024))

        # Invalid inputs fallback to default
        self.assertEqual(_parse_bytes(""), 10485760)
        self.assertEqual(_parse_bytes("   "), 10485760)
        self.assertEqual(_parse_bytes("invalid"), 10485760)
        self.assertEqual(_parse_bytes("MB"), 10485760)
        self.assertEqual(_parse_bytes("GiB"), 10485760)
        self.assertEqual(_parse_bytes("B"), 10485760)
        self.assertEqual(_parse_bytes(None), 10485760)
        self.assertEqual(_parse_bytes(True), 10485760)
        self.assertEqual(_parse_bytes(False), 10485760)
        self.assertEqual(_parse_bytes([], default=100), 100)
        self.assertEqual(_parse_bytes({}, default=100), 100)
        self.assertEqual(_parse_bytes("abcGB", default=100), 100)


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
        self.assertEqual(mock_instances_client.stop.call_count, 1)
        _, kwargs = mock_instances_client.stop.call_args
        req = kwargs.get("request")
        self.assertIsNotNone(req)
        self.assertEqual(req.project, "proj")
        self.assertEqual(req.zone, "us-central1-a")
        self.assertEqual(req.instance, "vm-1")
        self.assertTrue(req.discard_local_ssd)
        mock_op.result.assert_called_once_with(timeout=300)

        self.client.delete_instance("proj", "us-central1-a", "vm-2")
        mock_instances_client.delete.assert_called_once_with(project="proj", zone="us-central1-a", instance="vm-2")

    @patch("stopper.gce_client.compute_v1.InstancesClient")
    def test_stop_instance_with_explicit_discard_local_ssd(self, mock_instances_cls):
        mock_instances_client = MagicMock()
        mock_instances_cls.return_value = mock_instances_client
        self.client._instances_client = mock_instances_client

        mock_op = MagicMock()
        mock_instances_client.stop.return_value = mock_op

        self.client.stop_instance("proj", "us-central1-a", "vm-1", discard_local_ssd=False)
        self.assertEqual(mock_instances_client.stop.call_count, 1)
        _, kwargs = mock_instances_client.stop.call_args
        req = kwargs.get("request")
        self.assertIsNotNone(req)
        self.assertEqual(req.project, "proj")
        self.assertEqual(req.zone, "us-central1-a")
        self.assertEqual(req.instance, "vm-1")
        self.assertFalse(req.discard_local_ssd)

    @patch("stopper.gce_client.compute_v1.InstancesClient")
    def test_stop_instance_error_raises(self, mock_instances_cls):
        mock_instances_client = MagicMock()
        mock_instances_cls.return_value = mock_instances_client
        self.client._instances_client = mock_instances_client

        mock_instances_client.stop.side_effect = Exception("503 Service Unavailable: Backend error")
        with self.assertRaises(Exception) as ctx:
            self.client.stop_instance("proj", "us-central1-a", "vm-1")
        self.assertIn("503 Service Unavailable", str(ctx.exception))
        self.assertEqual(mock_instances_client.stop.call_count, 1)

    def test_is_rate_limit_error(self):
        """Test rate limit detection across exception types, status codes, and messages."""
        self.assertTrue(is_rate_limit_error(Exception("429 POST https://logging.googleapis.com/...: Quota exceeded for quota metric 'Read requests'")))
        self.assertTrue(is_rate_limit_error(Exception("Rate limit exceeded for read requests per minute")))
        self.assertTrue(is_rate_limit_error(Exception("RESOURCE_EXHAUSTED: Quota exceeded")))

        code_err = Exception("Custom error")
        setattr(code_err, "code", 429)
        self.assertTrue(is_rate_limit_error(code_err))

        grpc_err = Exception("gRPC error")
        setattr(grpc_err, "code", 8)  # gRPC code 8 = RESOURCE_EXHAUSTED
        self.assertTrue(is_rate_limit_error(grpc_err))

        self.assertFalse(is_rate_limit_error(Exception("403 Forbidden: Permission denied")))
        self.assertFalse(is_rate_limit_error(ValueError("Invalid argument")))
        self.assertFalse(is_rate_limit_error(RuntimeError("Unexpected connection error")))

    def test_rate_limiter_tokens(self):
        """Test RateLimiter token replenishment and burst handling."""
        limiter = RateLimiter(max_per_minute=6000, burst_capacity=5)
        # Should acquire without blocking when burst capacity is available
        for _ in range(5):
            limiter.acquire()
        self.assertLessEqual(limiter.tokens, 1.0)

    @patch("time.sleep")
    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_retry_on_429_success(self, mock_logging_cls, mock_sleep):
        """Verify that a 429 Rate Limit error is retried with backoff and succeeds on subsequent attempt."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        # First call raises 429, second call succeeds with an entry
        rate_err = Exception("429 POST https://logging.googleapis.com/v2/entries:list: Quota exceeded")
        mock_entry = MagicMock()
        mock_logging_client.list_entries.side_effect = [
            rate_err,
            [mock_entry],
        ]

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="retry-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(has_activity)
        self.assertEqual(mock_logging_client.list_entries.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_retry_on_429_exhausted_fails_safe(self, mock_logging_cls, mock_sleep):
        """Verify that when 429 retries are exhausted, it fails safe (assumes ACTIVE)."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client
        self.client.max_retries = 2

        rate_err = Exception("429 Too Many Requests: Quota exceeded")
        mock_logging_client.list_entries.side_effect = rate_err

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="exhausted-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        # MUST return True (fail-safe) after exhausting all attempts
        self.assertTrue(has_activity)
        self.assertEqual(mock_logging_client.list_entries.call_count, 3)  # initial + 2 retries
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("stopper.gce_client.logging_v2.Client")
    def test_get_instances_activity_batch(self, mock_logging_cls):
        """Test batch query identifies active instances and confirms idle instances in a single call."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        vm1 = MockInstance("active-oslogin-vm", instance_id="111")
        vm2 = MockInstance("active-activity-vm", instance_id="222")
        vm3 = MockInstance("idle-vm", instance_id="333")
        candidate_instances = [
            ("us-central1-a", vm1),
            ("us-central1-b", vm2),
            ("us-central1-c", vm3),
        ]

        # Entry 1: OS Login data_access log mentioning vm1
        entry1 = MagicMock()
        entry1.resource = MagicMock(type="audited_resource", labels={})
        entry1.payload = {"resourceName": "projects/test-proj/zones/us-central1-a/instances/active-oslogin-vm"}

        # Entry 2: Activity log with instance_id=222
        entry2 = MagicMock()
        entry2.resource = MagicMock(type="gce_instance", labels={"instance_id": "222"})
        entry2.payload = {}

        mock_logging_client.list_entries.return_value = [entry1, entry2]

        results = self.client.get_instances_activity(
            project_id="test-proj",
            candidate_instances=candidate_instances,
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
            batch_size=25,
        )

        self.assertEqual(mock_logging_client.list_entries.call_count, 1)
        self.assertTrue(results[("us-central1-a", "active-oslogin-vm")])
        self.assertTrue(results[("us-central1-b", "active-activity-vm")])
        self.assertFalse(results[("us-central1-c", "idle-vm")])

    @patch("stopper.gce_client.logging_v2.Client")
    def test_get_instances_activity_fallback_on_batch_error(self, mock_logging_cls):
        """Test fallback to individual evaluations when batch query fails."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        vm = MockInstance("fallback-vm", instance_id="999")
        candidate_instances = [("us-central1-a", vm)]

        # First call (batch) raises error, fallback call (individual) returns empty
        mock_logging_client.list_entries.side_effect = [
            Exception("Batch query syntax error"),
            [],
        ]

        results = self.client.get_instances_activity(
            project_id="test-proj",
            candidate_instances=candidate_instances,
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
            batch_size=25,
        )

        self.assertEqual(mock_logging_client.list_entries.call_count, 2)
        self.assertFalse(results[("us-central1-a", "fallback-vm")])

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_filter_excludes_list_login_profiles(self, mock_logging_cls):
        """Verify has_recent_activity includes ListLoginProfiles exclusion in Cloud Logging filter."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client
        mock_logging_client.list_entries.return_value = []

        self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="target-vm",
            instance_id="98765",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )

        mock_logging_client.list_entries.assert_called_once()
        _, kwargs = mock_logging_client.list_entries.call_args
        query_filter = kwargs.get("filter_") or ""

        # Assert ListLoginProfiles exclusion is present
        self.assertIn('NOT protoPayload.methodName:"ListLoginProfiles"', query_filter)
        # Assert target instance resource matching is preserved
        self.assertIn('protoPayload.resourceName="projects/test-proj/zones/us-central1-a/instances/target-vm"', query_filter)
        # Assert interactive methods are retained
        self.assertIn('protoPayload.methodName:"setMetadata"', query_filter)
        self.assertIn('protoPayload.methodName:"oslogin"', query_filter)

    @patch("stopper.gce_client.logging_v2.Client")
    def test_get_instances_activity_batch_filter_excludes_list_login_profiles(self, mock_logging_cls):
        """Verify batch query includes ListLoginProfiles exclusion in batch_filter."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client
        mock_logging_client.list_entries.return_value = []

        candidates = [
            ("us-central1-a", MockInstance("vm-1", instance_id="111")),
            ("us-central1-b", MockInstance("vm-2", instance_id="222")),
        ]
        self.client.get_instances_activity(
            project_id="test-proj",
            candidate_instances=candidates,
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )

        mock_logging_client.list_entries.assert_called_once()
        _, kwargs = mock_logging_client.list_entries.call_args
        batch_filter = kwargs.get("filter_") or ""

        self.assertIn('NOT protoPayload.methodName:"ListLoginProfiles"', batch_filter)
        self.assertIn('projects/test-proj/zones/us-central1-a/instances/vm-1', batch_filter)
        self.assertIn('resource.labels.instance_id="111"', batch_filter)

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_evaluates_list_login_profiles_as_idle(self, mock_logging_cls):
        """Verify instance with only ListLoginProfiles audit entries evaluates as idle (False)."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        def simulate_logging_entries(filter_, **kwargs):
            # If filter excludes ListLoginProfiles, 0 entries match
            if 'NOT protoPayload.methodName:"ListLoginProfiles"' in filter_:
                return []
            # If filter failed to exclude it, the daemon log is returned
            daemon_entry = MagicMock()
            daemon_entry.payload = {"methodName": "ListLoginProfiles"}
            return [daemon_entry]

        mock_logging_client.list_entries.side_effect = simulate_logging_entries

        has_activity = self.client.has_recent_activity(
            project_id="test-proj",
            zone="us-central1-a",
            instance_name="daemon-vm",
            instance_id="12345",
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(has_activity, "Instance with only ListLoginProfiles MUST evaluate as idle (False)")

    @patch("stopper.gce_client.logging_v2.Client")
    def test_has_recent_activity_evaluates_interactive_signals_as_active(self, mock_logging_cls):
        """Verify interactive login and metadata update audit entries evaluate as active (True)."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        interactive_methods = [
            ("setMetadata", {"instance_id": "12345"}),
            ("setInstanceAttributes", {"instance_id": "12345"}),
            ("setCommonInstanceMetadata", {"instance_id": "12345"}),
            ("oslogin", {"instance_id": "12345"}),
        ]

        for method, labels in interactive_methods:
            entry = MagicMock()
            entry.resource = MagicMock(type="gce_instance", labels=labels)
            entry.payload = {"protoPayload": {"methodName": method}}
            mock_logging_client.list_entries.return_value = [entry]

            has_act = self.client.has_recent_activity(
                project_id="test-proj",
                zone="us-central1-a",
                instance_name="active-vm",
                instance_id="12345",
                since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(has_act, f"Interactive method '{method}' MUST evaluate as active (True)")

    @patch("stopper.gce_client.logging_v2.Client")
    def test_get_instances_activity_batch_distinguishes_daemon_from_interactive(self, mock_logging_cls):
        """Verify batch query distinguishes daemon polling (idle) from interactive sessions and metadata changes."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        vm_daemon = MockInstance("vm-idle-daemon", instance_id="101")
        vm_oslogin = MockInstance("vm-active-oslogin", instance_id="102")
        vm_meta = MockInstance("vm-active-metadata", instance_id="103")
        vm_silent = MockInstance("vm-silent-idle", instance_id="104")

        candidate_instances = [
            ("us-central1-a", vm_daemon),
            ("us-central1-a", vm_oslogin),
            ("us-central1-b", vm_meta),
            ("us-central1-c", vm_silent),
        ]

        # Entry for vm_oslogin: legitimate interactive session (not ListLoginProfiles)
        entry_oslogin = MagicMock()
        entry_oslogin.resource = MagicMock(type="audited_resource", labels={})
        entry_oslogin.payload = {
            "serviceName": "oslogin.googleapis.com",
            "resourceName": "projects/test-proj/zones/us-central1-a/instances/vm-active-oslogin",
        }

        # Entry for vm_meta: activity log for SSH key injection
        entry_meta = MagicMock()
        entry_meta.resource = MagicMock(type="gce_instance", labels={"instance_id": "103"})
        entry_meta.payload = {"protoPayload": {"methodName": "setMetadata"}}

        # Cloud Logging server returns only entries matching the filter (vm_oslogin and vm_meta)
        mock_logging_client.list_entries.return_value = [entry_oslogin, entry_meta]

        results = self.client.get_instances_activity(
            project_id="test-proj",
            candidate_instances=candidate_instances,
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
            batch_size=25,
        )

        # Verify assertions
        self.assertFalse(results[("us-central1-a", "vm-idle-daemon")], "vm-idle-daemon with only ListLoginProfiles must be idle")
        self.assertTrue(results[("us-central1-a", "vm-active-oslogin")], "vm-active-oslogin must be active")
        self.assertTrue(results[("us-central1-b", "vm-active-metadata")], "vm-active-metadata must be active")
        self.assertFalse(results[("us-central1-c", "vm-silent-idle")], "vm-silent-idle must be idle")

    @patch("stopper.gce_client.logging_v2.Client")
    def test_get_instances_activity_batch_filter_causation_simulation(self, mock_logging_cls):
        """Prove that presence of ListLoginProfiles exclusion in batch filter produces idle result."""
        mock_logging_client = MagicMock()
        mock_logging_cls.return_value = mock_logging_client
        self.client._logging_client = mock_logging_client

        vm_daemon = MockInstance("daemon-vm", instance_id="555")
        candidates = [("us-central1-a", vm_daemon)]

        def mock_list_entries(filter_, **kwargs):
            self.assertIn('NOT protoPayload.methodName:"ListLoginProfiles"', filter_)
            # Because filter excludes ListLoginProfiles, Cloud Logging returns empty list
            return []

        mock_logging_client.list_entries.side_effect = mock_list_entries

        results = self.client.get_instances_activity(
            project_id="test-proj",
            candidate_instances=candidates,
            since_timestamp=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(results[("us-central1-a", "daemon-vm")])


class TestVMProcessorLifecycle(unittest.TestCase):
    """Test end-to-end VM evaluation, stopping, deleting, and dry-run sweeps."""

    def setUp(self):
        self.mock_client = MagicMock(spec=GCEClient)
        self.mock_client.has_network_activity.return_value = (False, 0)
        self.now = datetime.now(timezone.utc)

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
        self.mock_client.stop_instance.assert_called_once_with(
            "test-proj", "us-central1-a", "idle-vm"
        )

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

    def test_stopped_vm_missing_stop_timestamp_skipped_fail_safe(self):
        """Verifies that an older VM stopped without last_stop_timestamp is not deleted (fail-safe)."""
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=False,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        # VM created 200 days ago, but stop timestamp is unknown/empty
        stopped_vm = MockInstance(
            name="unknown-stop-vm",
            status="TERMINATED",
            creation_timestamp=(self.now - timedelta(days=200)).isoformat(),
            last_stop_timestamp="",
            last_suspended_timestamp="",
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "skipped_stopped")
        self.assertEqual(res["action"], "none")
        self.assertIn("stop timestamp could not be determined", res["reason"])
        self.mock_client.delete_instance.assert_not_called()

    def test_stopped_vm_fallback_to_shutdown_details_request_timestamp(self):
        """Verifies that shutdown_details.request_timestamp is used if last_stop_timestamp is absent."""
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=False,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        shutdown_details = MockShutdownDetails(request_timestamp=(self.now - timedelta(days=100)).isoformat())
        resource_status = MockResourceStatus(shutdown_details=shutdown_details)
        stopped_vm = MockInstance(
            name="shutdown-details-vm",
            status="TERMINATED",
            last_stop_timestamp="",
            resource_status=resource_status,
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "deleted")
        self.assertEqual(res["action"], "deleted")
        self.mock_client.delete_instance.assert_called_once_with("test-proj", "us-central1-a", "shutdown-details-vm")

    def test_stopped_vm_recent_shutdown_details_retained(self):
        """Verifies that a VM recently stopped via shutdown_details is retained."""
        config = StopperConfig(
            project_id="test-proj",
            delete_stopped_vms=True,
            stopped_days_threshold=90,
            dry_run=False,
        )
        processor = VMProcessor(config, gce_client=self.mock_client)

        shutdown_details = MockShutdownDetails(request_timestamp=(self.now - timedelta(days=10)).isoformat())
        resource_status = MockResourceStatus(shutdown_details=shutdown_details)
        stopped_vm = MockInstance(
            name="shutdown-recent-vm",
            status="TERMINATED",
            last_stop_timestamp="",
            resource_status=resource_status,
        )

        res = processor.process_single_instance("us-central1-a", stopped_vm, self.now)
        self.assertEqual(res["category"], "skipped_stopped")
        self.assertEqual(res["action"], "none")
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

        self.mock_client.stop_instance.assert_called_once_with(
            "test-proj", "us-central1-b", "idle-vm"
        )
        self.mock_client.delete_instance.assert_called_once_with("test-proj", "us-central1-c", "stopped-old")

    def test_sweep_with_batch_activity_checking(self):
        """Verify that processor.sweep() batch-evaluates candidate running instances."""
        config = StopperConfig(
            project_id="test-proj",
            idle_days_threshold=7,
            dry_run=False,
            cloud_logging_batch_size=10,
        )
        mock_client = self.mock_client
        processor = VMProcessor(config, gce_client=mock_client)

        idle_vm1 = MockInstance("candidate-idle-1", status="RUNNING", creation_timestamp=(self.now - timedelta(days=20)).isoformat())
        active_vm2 = MockInstance("candidate-active-2", status="RUNNING", creation_timestamp=(self.now - timedelta(days=20)).isoformat())

        mock_client.list_instances.return_value = [
            ("us-central1-a", idle_vm1),
            ("us-central1-b", active_vm2),
        ]

        # Explicitly configure mock_client.get_instances_activity
        mock_client.get_instances_activity.return_value = {
            ("us-central1-a", "candidate-idle-1"): False,
            ("us-central1-b", "candidate-active-2"): True,
        }

        response = processor.sweep()
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["summary"]["stopped"], 1)
        self.assertEqual(response["summary"]["skipped_active"], 1)

        mock_client.get_instances_activity.assert_called_once()
        mock_client.stop_instance.assert_called_once_with(
            "test-proj", "us-central1-a", "candidate-idle-1"
        )

    def test_running_vm_with_only_list_login_profiles_is_stopped(self):
        """Verify running instance with only daemon polling is stopped when delete_stopped=False."""
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7, dry_run=False)
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=20)).isoformat()
        daemon_vm = MockInstance(name="daemon-vm", status="RUNNING", creation_timestamp=created_ts)

        # gce_client evaluates daemon-only instance as idle (False)
        self.mock_client.has_recent_activity.return_value = False

        res = processor.process_single_instance("us-central1-a", daemon_vm, self.now)
        self.assertEqual(res["category"], "stopped")
        self.assertEqual(res["action"], "stopped")
        self.mock_client.stop_instance.assert_called_once_with("test-proj", "us-central1-a", "daemon-vm")

    def test_running_vm_with_only_list_login_profiles_dry_run(self):
        """Verify dry-run sweep identifies daemon-only running VM as dry_run_stop without stopping it."""
        config = StopperConfig(project_id="test-proj", idle_days_threshold=7, dry_run=True)
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=30)).isoformat()
        target_vm = MockInstance(name="gargnitin-tess-e2std16-us-central1a", status="RUNNING", creation_timestamp=created_ts)

        # Under the updated filter, has_recent_activity returns False
        self.mock_client.has_recent_activity.return_value = False

        res = processor.process_single_instance("us-central1-a", target_vm, self.now)
        self.assertEqual(res["category"], "dry_run_stops")
        self.assertEqual(res["action"], "dry_run_stop")
        self.assertIn("[DRY RUN] Would stop idle running VM", res["reason"])
        self.mock_client.stop_instance.assert_not_called()


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


class TestCloudMonitoringNetworkTelemetry(unittest.TestCase):
    """Unit tests for Cloud Monitoring network metrics telemetry, hierarchical fallback, and fail-safe error handling."""

    def setUp(self):
        self.now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.mock_client = MagicMock(spec=GCEClient)

    def test_config_network_telemetry_defaults(self):
        config = StopperConfig(project_id="test-proj")
        self.assertEqual(config.network_bytes_threshold, 10485760)
        self.assertIsNone(config.network_lookback_hours)
        self.assertTrue(config.enable_network_monitoring)

    def test_config_network_telemetry_env_vars(self):
        env = {
            "PROJECT_ID": "env-proj",
            "NETWORK_BYTES_THRESHOLD": "52428800",
            "NETWORK_LOOKBACK_HOURS": "48",
            "ENABLE_NETWORK_MONITORING": "false",
        }
        config = StopperConfig.from_request(env=env)
        self.assertEqual(config.project_id, "env-proj")
        self.assertEqual(config.network_bytes_threshold, 52428800)
        self.assertEqual(config.network_lookback_hours, 48)
        self.assertFalse(config.enable_network_monitoring)

    def test_config_network_bytes_suffix_parsing(self):
        # 10MB -> 10 * 1024 * 1024
        c1 = StopperConfig.from_request(request_data={"project": "p", "network_bytes_threshold": "10MB"})
        self.assertEqual(c1.network_bytes_threshold, 10485760)

        # 500KB -> 500 * 1024
        c2 = StopperConfig.from_request(request_data={"project": "p", "network_bytes_threshold": "500KB"})
        self.assertEqual(c2.network_bytes_threshold, 512000)

        # 1GB -> 1073741824
        c3 = StopperConfig.from_request(request_data={"project": "p", "network_bytes_threshold": "1GB"})
        self.assertEqual(c3.network_bytes_threshold, 1073741824)

        # Raw int
        c4 = StopperConfig.from_request(request_data={"project": "p", "network_bytes_threshold": 2048})
        self.assertEqual(c4.network_bytes_threshold, 2048)

    def test_config_validation_negative_threshold(self):
        config = StopperConfig(project_id="test-proj", network_bytes_threshold=-100)
        with self.assertRaises(ValueError) as ctx:
            config.validate()
        self.assertIn("network_bytes_threshold must be >= 0", str(ctx.exception))

    def test_config_validation_invalid_lookback(self):
        c1 = StopperConfig(project_id="test-proj", network_lookback_hours=0)
        with self.assertRaises(ValueError) as ctx1:
            c1.validate()
        self.assertIn("network_lookback_hours must be > 0", str(ctx1.exception))

        c2 = StopperConfig(project_id="test-proj", network_lookback_hours=-10)
        with self.assertRaises(ValueError) as ctx2:
            c2.validate()
        self.assertIn("network_lookback_hours must be > 0", str(ctx2.exception))

    def test_get_instance_network_bytes_aggregation(self):
        mock_mon_client = MagicMock()
        mock_series_rx = {
            "points": [
                {"value": {"int64Value": 4000000}},
                {"value": {"int64Value": 1000000}},
            ]
        }
        mock_series_tx = {
            "points": [
                {"value": {"int64Value": 7000000}},
            ]
        }
        mock_mon_client.list_time_series.return_value = [mock_series_rx, mock_series_tx]

        client = GCEClient(monitoring_client=mock_mon_client)
        total = client.get_instance_network_bytes(
            "test-proj",
            "inst-12345",
            self.now - timedelta(hours=24),
            self.now,
        )
        self.assertEqual(total, 12000000)
        mock_mon_client.list_time_series.assert_called_once()
        call_kwargs = mock_mon_client.list_time_series.call_args[1]
        req = call_kwargs["request"]
        self.assertEqual(req.name, "projects/test-proj")
        self.assertIn(
            '(metric.type = "compute.googleapis.com/instance/network/received_bytes_count" OR '
            'metric.type = "compute.googleapis.com/instance/network/sent_bytes_count")',
            req.filter,
        )
        self.assertNotIn("one_of", req.filter)
        self.assertIn("inst-12345", req.filter)
        self.assertEqual(req.aggregation.alignment_period, {"seconds": 3600})

    def test_get_instance_network_bytes_alignment_period_ge_1_hour(self):
        mock_mon_client = MagicMock()
        mock_mon_client.list_time_series.return_value = []
        client = GCEClient(monitoring_client=mock_mon_client)

        # 24 hours interval
        client.get_instance_network_bytes(
            "test-proj",
            "inst-12345",
            self.now - timedelta(hours=24),
            self.now,
        )
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.aggregation.alignment_period, {"seconds": 3600})

        # Exactly 1 hour interval
        client.get_instance_network_bytes(
            "test-proj",
            "inst-12345",
            self.now - timedelta(hours=1),
            self.now,
        )
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.aggregation.alignment_period, {"seconds": 3600})

    def test_get_instance_network_bytes_alignment_period_lt_1_hour(self):
        mock_mon_client = MagicMock()
        mock_mon_client.list_time_series.return_value = []
        client = GCEClient(monitoring_client=mock_mon_client)

        # 5-minute fallback when since_timestamp >= until_timestamp
        client.get_instance_network_bytes(
            "test-proj",
            "inst-12345",
            self.now,
            self.now,
        )
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.aggregation.alignment_period, {"seconds": 60})

        # Explicit interval < 1 hour (e.g. 30 minutes)
        client.get_instance_network_bytes(
            "test-proj",
            "inst-12345",
            self.now - timedelta(minutes=30),
            self.now,
        )
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.aggregation.alignment_period, {"seconds": 60})

    def test_has_network_activity_above_threshold(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", return_value=15000000):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertTrue(is_active)
            self.assertEqual(count, 15000000)

    def test_has_network_activity_below_threshold(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", return_value=5000000):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertFalse(is_active)
            self.assertEqual(count, 5000000)

    def test_has_network_activity_empty_series(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", return_value=0):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertFalse(is_active)
            self.assertEqual(count, 0)

    def test_has_network_activity_fail_safe_on_403(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", side_effect=Exception("403 Forbidden")):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertTrue(is_active)
            self.assertEqual(count, -1)

    def test_has_network_activity_fail_safe_on_timeout(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", side_effect=Exception("DeadlineExceeded")):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertTrue(is_active)
            self.assertEqual(count, -1)

    def test_has_network_activity_fail_safe_on_generic_exception(self):
        client = GCEClient()
        with patch.object(client, "get_instance_network_bytes", side_effect=RuntimeError("Connection reset")):
            is_active, count = client.has_network_activity(
                "test-proj",
                "inst-1",
                "vm-test",
                "us-central1-a",
                self.now - timedelta(hours=24),
                10485760,
            )
            self.assertTrue(is_active)
            self.assertEqual(count, -1)

    def test_extract_point_value_which_oneof_proto_plus(self):
        # 1. Proto-plus object with _pb attribute supporting WhichOneof
        # Case A: int64 active
        pb_int = MagicMock()
        pb_int.WhichOneof.return_value = "int64_value"
        pb_int.int64_value = 1048576
        val_int = types.SimpleNamespace(_pb=pb_int)
        point_int = types.SimpleNamespace(value=val_int)
        self.assertEqual(GCEClient._extract_point_value(point_int), 1048576)
        pb_int.WhichOneof.assert_called_with("value")

        # Case B: double active
        # Note: on proto-plus objects, accessing unset int64_value returns 0.
        # Ensure that WhichOneof correctly extracts double_value even when int64_value=0.
        pb_double = MagicMock()
        pb_double.WhichOneof.return_value = "double_value"
        pb_double.double_value = 2097152.0
        val_double = types.SimpleNamespace(_pb=pb_double, int64_value=0, double_value=2097152.0)
        point_double = types.SimpleNamespace(value=val_double)
        self.assertEqual(GCEClient._extract_point_value(point_double), 2097152)
        pb_double.WhichOneof.assert_called_with("value")

        # 2. Direct protobuf object (without _pb attribute) supporting WhichOneof
        pb_direct_int = MagicMock()
        pb_direct_int.WhichOneof.return_value = "int64_value"
        pb_direct_int.int64_value = 524288
        del pb_direct_int._pb  # Ensure no _pb
        point_direct_int = types.SimpleNamespace(value=pb_direct_int)
        self.assertEqual(GCEClient._extract_point_value(point_direct_int), 524288)

        pb_direct_double = MagicMock()
        pb_direct_double.WhichOneof.return_value = "double_value"
        pb_direct_double.double_value = 65536.0
        del pb_direct_double._pb
        point_direct_double = types.SimpleNamespace(value=pb_direct_double)
        self.assertEqual(GCEClient._extract_point_value(point_direct_double), 65536)

        # 3. WhichOneof raises ValueError/TypeError, falls back to attributes
        pb_err = MagicMock()
        pb_err.WhichOneof.side_effect = ValueError("Invalid field")
        pb_err.int64_value = 4096
        val_err = types.SimpleNamespace(_pb=pb_err, int64_value=4096)
        point_err = types.SimpleNamespace(value=val_err)
        self.assertEqual(GCEClient._extract_point_value(point_err), 4096)

        # 4. Fallback attribute access on simple mocks without WhichOneof
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=types.SimpleNamespace(int64_value=123))), 123)
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=types.SimpleNamespace(double_value=456.7))), 456)
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=types.SimpleNamespace(int64Value=789))), 789)
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=types.SimpleNamespace(doubleValue=321.4))), 321)

    def test_extract_point_value_dicts_and_scalars(self):
        # Dict inputs with nested value dict
        self.assertEqual(GCEClient._extract_point_value({"value": {"int64Value": 100}}), 100)
        self.assertEqual(GCEClient._extract_point_value({"value": {"int64_value": 200}}), 200)
        self.assertEqual(GCEClient._extract_point_value({"value": {"doubleValue": 300.7}}), 300)
        self.assertEqual(GCEClient._extract_point_value({"value": {"double_value": 400.2}}), 400)
        self.assertEqual(GCEClient._extract_point_value({"value": {}}), 0)
        self.assertEqual(GCEClient._extract_point_value({"value": 500}), 500)
        self.assertEqual(GCEClient._extract_point_value({"value": None}), 0)
        self.assertEqual(GCEClient._extract_point_value({}), 0)

        # Scalar inputs
        self.assertEqual(GCEClient._extract_point_value(1024), 1024)
        self.assertEqual(GCEClient._extract_point_value(2048.9), 2048)
        self.assertEqual(GCEClient._extract_point_value(None), 0)

        # Object with scalar value attribute
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=4096)), 4096)
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=8192.5)), 8192)
        self.assertEqual(GCEClient._extract_point_value(types.SimpleNamespace(value=None)), 0)
        self.assertEqual(GCEClient._extract_point_value(object()), 0)

    def test_get_instance_network_bytes_explicit_and_keyword_args(self):
        mock_mon_client = MagicMock()
        mock_mon_client.list_time_series.return_value = []
        client = GCEClient(monitoring_client=mock_mon_client)

        start_ts = self.now - timedelta(hours=12)
        end_ts = self.now

        # 1. Explicit positional arguments
        total = client.get_instance_network_bytes("proj-pos", "inst-pos", start_ts, end_ts, "us-central1-a", 12)
        self.assertEqual(total, 0)
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.name, "projects/proj-pos")
        self.assertIn("inst-pos", req.filter)
        self.assertEqual(req.interval.start_time, start_ts)
        self.assertEqual(req.interval.end_time, end_ts)

        # 2. Keyword arguments
        mock_mon_client.reset_mock()
        total = client.get_instance_network_bytes(
            project_id="proj-kw",
            instance_id="inst-kw",
            since_timestamp=start_ts,
            until_timestamp=end_ts,
            zone="us-east1-b",
            lookback_hours=12,
        )
        self.assertEqual(total, 0)
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.name, "projects/proj-kw")
        self.assertIn("inst-kw", req.filter)
        self.assertEqual(req.interval.start_time, start_ts)
        self.assertEqual(req.interval.end_time, end_ts)

        # 3. Default arguments with lookback_hours
        mock_mon_client.reset_mock()
        total = client.get_instance_network_bytes(
            project_id="proj-lookback",
            instance_id="inst-lookback",
            until_timestamp=end_ts,
            lookback_hours=6,
        )
        self.assertEqual(total, 0)
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.interval.start_time, end_ts - timedelta(hours=6))
        self.assertEqual(req.interval.end_time, end_ts)

        # 4. Default 24h lookback when since_timestamp is None
        mock_mon_client.reset_mock()
        total = client.get_instance_network_bytes(
            project_id="proj-default",
            instance_id="inst-default",
            until_timestamp=end_ts,
        )
        self.assertEqual(total, 0)
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.interval.start_time, end_ts - timedelta(hours=24))
        self.assertEqual(req.interval.end_time, end_ts)

        # 5. Fallback when since_timestamp >= until_timestamp
        mock_mon_client.reset_mock()
        total = client.get_instance_network_bytes(
            project_id="proj-fallback",
            instance_id="inst-fallback",
            since_timestamp=end_ts,
            until_timestamp=end_ts,
        )
        self.assertEqual(total, 0)
        req = mock_mon_client.list_time_series.call_args[1]["request"]
        self.assertEqual(req.interval.start_time, end_ts - timedelta(minutes=5))

    def test_has_network_activity_explicit_and_keyword_args(self):
        client = GCEClient()

        # 1. Explicit positional arguments
        with patch.object(client, "get_instance_network_bytes", return_value=20000000) as mock_get_bytes:
            start_ts = self.now - timedelta(hours=8)
            end_ts = self.now
            is_active, count = client.has_network_activity(
                "proj-pos",
                "inst-pos-id",
                "vm-pos-name",
                "us-central1-b",
                start_ts,
                15000000,
                end_ts,
                8,
            )
            self.assertTrue(is_active)
            self.assertEqual(count, 20000000)
            mock_get_bytes.assert_called_once_with(
                project_id="proj-pos",
                instance_id="inst-pos-id",
                since_timestamp=start_ts,
                until_timestamp=end_ts,
                zone="us-central1-b",
                lookback_hours=8,
            )

        # 2. Keyword arguments
        with patch.object(client, "get_instance_network_bytes", return_value=5000000) as mock_get_bytes:
            start_ts = self.now - timedelta(hours=10)
            end_ts = self.now
            is_active, count = client.has_network_activity(
                project_id="proj-kw",
                instance_id="inst-kw-id",
                instance_name="vm-kw-name",
                zone="europe-west1-b",
                since_timestamp=start_ts,
                threshold_bytes=10000000,
                until_timestamp=end_ts,
                lookback_hours=10,
            )
            self.assertFalse(is_active)
            self.assertEqual(count, 5000000)
            mock_get_bytes.assert_called_once_with(
                project_id="proj-kw",
                instance_id="inst-kw-id",
                since_timestamp=start_ts,
                until_timestamp=end_ts,
                zone="europe-west1-b",
                lookback_hours=10,
            )

        # 3. Defaults when only required args provided
        with patch.object(client, "get_instance_network_bytes", return_value=10485760) as mock_get_bytes:
            is_active, count = client.has_network_activity(
                project_id="proj-req",
                instance_id="123456789",
            )
            self.assertTrue(is_active)
            self.assertEqual(count, 10485760)
            mock_get_bytes.assert_called_once_with(
                project_id="proj-req",
                instance_id="123456789",
                since_timestamp=None,
                until_timestamp=None,
                zone="unknown",
                lookback_hours=None,
            )

        # 4. Explicit None for optional args defaults properly
        with patch.object(client, "get_instance_network_bytes", return_value=10485759) as mock_get_bytes:
            is_active, count = client.has_network_activity(
                project_id="proj-none",
                instance_id="987654321",
                instance_name=None,
                zone=None,
                threshold_bytes=None,
            )
            self.assertFalse(is_active)
            self.assertEqual(count, 10485759)
            mock_get_bytes.assert_called_once_with(
                project_id="proj-none",
                instance_id="987654321",
                since_timestamp=None,
                until_timestamp=None,
                zone="unknown",
                lookback_hours=None,
            )

    def test_hierarchical_fallback_logging_active_skips_monitoring(self):
        config = StopperConfig(project_id="test-proj")
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        vm = MockInstance(name="active-log-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = True
        self.mock_client.has_network_activity = MagicMock()

        res = processor.process_single_instance("us-central1-a", vm, self.now)
        self.assertEqual(res["category"], "skipped_active")
        self.mock_client.has_network_activity.assert_not_called()
        self.mock_client.stop_instance.assert_not_called()

    def test_hierarchical_fallback_logging_idle_monitoring_active_retains_vm(self):
        config = StopperConfig(project_id="test-proj")
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        vm = MockInstance(name="active-net-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False
        self.mock_client.has_network_activity.return_value = (True, 25000000)

        res = processor.process_single_instance("us-central1-a", vm, self.now)
        self.assertEqual(res["category"], "skipped_active")
        self.assertIn("Active network traffic detected", res["reason"])
        self.mock_client.stop_instance.assert_not_called()

    def test_hierarchical_fallback_logging_idle_monitoring_idle_stops_vm(self):
        config = StopperConfig(project_id="test-proj")
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        vm = MockInstance(name="idle-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False
        self.mock_client.has_network_activity.return_value = (False, 1500000)

        res = processor.process_single_instance("us-central1-a", vm, self.now)
        self.assertEqual(res["category"], "stopped")
        self.assertEqual(res["action"], "stopped")
        self.mock_client.stop_instance.assert_called_once_with("test-proj", "us-central1-a", "idle-vm")

    def test_hierarchical_fallback_monitoring_error_retains_vm(self):
        config = StopperConfig(project_id="test-proj")
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        vm = MockInstance(name="err-net-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False
        self.mock_client.has_network_activity.return_value = (True, -1)

        res = processor.process_single_instance("us-central1-a", vm, self.now)
        self.assertEqual(res["category"], "skipped_active")
        self.assertIn("failing safe (assuming active)", res["reason"])
        self.mock_client.stop_instance.assert_not_called()

    def test_hierarchical_fallback_disabled_monitoring_bypasses_query(self):
        config = StopperConfig(project_id="test-proj", enable_network_monitoring=False)
        processor = VMProcessor(config, gce_client=self.mock_client)

        created_ts = (self.now - timedelta(days=15)).isoformat()
        vm = MockInstance(name="no-net-mon-vm", status="RUNNING", creation_timestamp=created_ts)

        self.mock_client.has_recent_activity.return_value = False
        self.mock_client.has_network_activity = MagicMock()

        res = processor.process_single_instance("us-central1-a", vm, self.now)
        self.assertEqual(res["category"], "stopped")
        self.mock_client.has_network_activity.assert_not_called()
        self.mock_client.stop_instance.assert_called_once()


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
