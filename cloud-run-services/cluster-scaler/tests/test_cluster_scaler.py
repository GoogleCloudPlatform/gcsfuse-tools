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

"""Comprehensive offline unit test suite for GKE Cluster Scaler."""

from __future__ import annotations

import datetime
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import types
from enum import Enum

# Ensure third-party modules can be imported/mocked offline
def _setup_offline_mock_modules() -> None:
    if "google" not in sys.modules:
        sys.modules["google"] = types.ModuleType("google")
    if "google.auth" not in sys.modules:
        sys.modules["google.auth"] = types.ModuleType("google.auth")
    if "google.auth.transport" not in sys.modules:
        sys.modules["google.auth.transport"] = types.ModuleType("google.auth.transport")
    if "google.auth.transport.requests" not in sys.modules:
        sys.modules["google.auth.transport.requests"] = types.ModuleType("google.auth.transport.requests")
    if "google.cloud" not in sys.modules:
        sys.modules["google.cloud"] = types.ModuleType("google.cloud")
    if "google.cloud.container_v1" not in sys.modules:
        container_mod = types.ModuleType("google.cloud.container_v1")

        class MockClusterStatus(Enum):
            STATUS_UNSPECIFIED = 0
            PROVISIONING = 1
            RUNNING = 2
            RECONCILING = 3
            STOPPING = 4
            ERROR = 5
            DEGRADED = 6

        class MockOperationStatus(Enum):
            STATUS_UNSPECIFIED = 0
            PENDING = 1
            RUNNING = 2
            DONE = 3
            ABORTING = 4

        container_mod.Cluster = types.SimpleNamespace(Status=MockClusterStatus)
        container_mod.Operation = types.SimpleNamespace(Status=MockOperationStatus)
        container_mod.ClusterManagerClient = MagicMock
        container_mod.SetLabelsRequest = MagicMock
        container_mod.SetNodePoolAutoscalingRequest = MagicMock
        container_mod.SetNodePoolSizeRequest = MagicMock
        container_mod.NodePoolAutoscaling = MagicMock

        sys.modules["google.cloud.container_v1"] = container_mod
        setattr(sys.modules["google.cloud"], "container_v1", container_mod)

    if "kubernetes" not in sys.modules:
        sys.modules["kubernetes"] = MagicMock()
        sys.modules["kubernetes.client"] = MagicMock()

    if "flask" not in sys.modules:
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

# Add service directory to Python path
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from scaler.config import ScalerConfig
from scaler.cluster_processor import ClusterProcessor, parse_idle_since
from scaler.gke_client import GKEClient
from scaler.service import ClusterScalerService
import main


def create_mock_pod(name: str, namespace: str, phase: str = "Running") -> MagicMock:
    """Helper to create a mock Kubernetes pod."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.status.phase = phase
    return pod


def create_mock_node_pool(
    name: str = "default-pool",
    node_count: int = 3,
    autoscaling_enabled: bool = True,
    min_nodes: int = 1,
    max_nodes: int = 5,
) -> MagicMock:
    """Helper to create a mock GKE NodePool."""
    pool = MagicMock()
    pool.name = name
    pool.initial_node_count = node_count
    pool.node_count = node_count
    if autoscaling_enabled:
        pool.autoscaling.enabled = True
        pool.autoscaling.min_node_count = min_nodes
        pool.autoscaling.max_node_count = max_nodes
        pool.autoscaling.total_min_node_count = min_nodes
        pool.autoscaling.total_max_node_count = max_nodes
    else:
        pool.autoscaling = None
    return pool


def create_mock_cluster(
    name: str = "projects/test-project/locations/us-central1/clusters/test-cluster",
    status: str = "RUNNING",
    endpoint: str = "35.1.2.3",
    labels: dict[str, str] = None,
    node_pools: list[MagicMock] = None,
    is_autopilot: bool = False,
    fingerprint: str = "abc123fingerprint",
) -> MagicMock:
    """Helper to create a mock GKE Cluster."""
    cluster = MagicMock()
    cluster.name = name
    cluster.status.name = status
    cluster.status = status
    cluster.endpoint = endpoint
    cluster.resource_labels = labels if labels is not None else {}
    cluster.label_fingerprint = fingerprint
    cluster.master_auth.cluster_ca_certificate = "dGVzdC1jYS1jZXJ0"  # base64 for 'test-ca-cert'

    if is_autopilot:
        cluster.autopilot.enabled = True
        cluster.node_pools = []
    else:
        cluster.autopilot = None
        cluster.node_pools = node_pools if node_pools is not None else [create_mock_node_pool()]
    return cluster


class TestScalerConfig(unittest.TestCase):
    """Test suite for ScalerConfig parsing and validations."""

    def test_default_config(self) -> None:
        config = ScalerConfig(project_id="my-proj")
        self.assertEqual(config.project_id, "my-proj")
        self.assertEqual(config.location, "-")
        self.assertEqual(config.idle_days_threshold, 7)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.max_workers, 10)
        self.assertIn("kube-system", config.ignored_namespaces)
        self.assertIn("gke-managed-system", config.ignored_namespaces)

    def test_validation_errors(self) -> None:
        with self.assertRaises(ValueError):
            ScalerConfig(project_id="p", idle_days_threshold=-1)

        with self.assertRaises(ValueError):
            ScalerConfig(project_id="p", max_workers=0)

    def test_system_namespace_detection(self) -> None:
        config = ScalerConfig(project_id="my-proj")
        # System namespaces
        self.assertTrue(config.is_system_namespace("kube-system"))
        self.assertTrue(config.is_system_namespace("kube-public"))
        self.assertTrue(config.is_system_namespace("kube-node-lease"))
        self.assertTrue(config.is_system_namespace("gke-managed-system"))
        self.assertTrue(config.is_system_namespace("gke-gcsfuse-csi"))
        self.assertTrue(config.is_system_namespace("gcs-fuse-csi-driver"))
        self.assertTrue(config.is_system_namespace("istio-system"))
        self.assertTrue(config.is_system_namespace("gatekeeper-system"))

        # User namespaces
        self.assertFalse(config.is_system_namespace("default"))
        self.assertFalse(config.is_system_namespace("staging"))
        self.assertFalse(config.is_system_namespace("production"))
        self.assertFalse(config.is_system_namespace("ml-pipeline"))

    def test_from_request_json_payload(self) -> None:
        mock_req = MagicMock()
        mock_req.get_json.return_value = {
            "project_id": "payload-proj",
            "location": "europe-west1",
            "idle_days_threshold": 14,
            "dry_run": True,
            "max_workers": 4,
            "ignored_namespaces": ["kube-system", "custom-infra"],
            "cluster_names": ["cluster-a", "cluster-b"],
        }
        mock_req.args = {}

        config = ScalerConfig.from_request(mock_req)
        self.assertEqual(config.project_id, "payload-proj")
        self.assertEqual(config.location, "europe-west1")
        self.assertEqual(config.idle_days_threshold, 14)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.max_workers, 4)
        self.assertEqual(config.ignored_namespaces, {"kube-system", "custom-infra"})
        self.assertEqual(config.cluster_names, ["cluster-a", "cluster-b"])

    def test_from_request_query_args_fallback(self) -> None:
        mock_req = MagicMock()
        mock_req.get_json.return_value = None
        mock_req.args = {
            "project": "query-proj",
            "days_threshold": "10",
            "dry_run": "true",
            "max_workers": "8",
        }

        config = ScalerConfig.from_request(mock_req)
        self.assertEqual(config.project_id, "query-proj")
        self.assertEqual(config.idle_days_threshold, 10)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.max_workers, 8)

    def test_from_request_env_var_fallback(self) -> None:
        mock_req = MagicMock()
        mock_req.get_json.return_value = {}
        mock_req.args = {}

        env_patch = {
            "PROJECT_ID": "env-proj",
            "LOCATION": "us-east1",
            "IDLE_DAYS_THRESHOLD": "5",
            "DRY_RUN": "1",
            "MAX_WORKERS": "20",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            config = ScalerConfig.from_request(mock_req)
            self.assertEqual(config.project_id, "env-proj")
            self.assertEqual(config.location, "us-east1")
            self.assertEqual(config.idle_days_threshold, 5)
            self.assertTrue(config.dry_run)
            self.assertEqual(config.max_workers, 20)

    def test_parse_idle_since_date_formats(self) -> None:
        # ISO format
        d1 = parse_idle_since("2026-08-01")
        self.assertEqual(d1, datetime.date(2026, 8, 1))

        # Underscore format
        d2 = parse_idle_since("2026_08_15")
        self.assertEqual(d2, datetime.date(2026, 8, 15))

        # Epoch timestamp
        epoch = 1787938600.0  # Approx year 2026
        d3 = parse_idle_since(str(epoch))
        self.assertIsNotNone(d3)

        # Invalid formats return None
        self.assertIsNone(parse_idle_since(""))
        self.assertIsNone(parse_idle_since("invalid-date-string-xyz"))


class TestClusterProcessor(unittest.TestCase):
    """Test suite for ClusterProcessor business logic and state transitions."""

    def setUp(self) -> None:
        self.config = ScalerConfig(
            project_id="test-project",
            idle_days_threshold=7,
            dry_run=False,
        )
        self.mock_gke_client = MagicMock(spec=GKEClient)

        def mock_check_activity(cluster, *args, **kwargs):
            has_pods, pods = self.mock_gke_client.get_cluster_active_pods(cluster=cluster)
            return has_pods, pods, f"{len(pods)} active user pod(s)" if has_pods else "No active workloads"

        self.mock_gke_client.check_cluster_activity.side_effect = mock_check_activity
        self.processor = ClusterProcessor(config=self.config, gke_client=self.mock_gke_client)

    def test_cluster_visited_today_via_recent_workload_clears_idle_since(self) -> None:
        """Cluster with 0 running pods but recent workload activity today clears idle_since."""
        ten_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(labels={"idle_since": ten_days_ago, "env": "dev"})

        # Mock: 0 running pods, but workload activity was detected today
        self.mock_gke_client.check_cluster_activity.side_effect = None
        self.mock_gke_client.check_cluster_activity.return_value = (
            True,
            [],
            "User pod 'batch-eval-job' completed recently at 2026-08-31T12:00:00Z",
        )

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "active_clusters")
        self.assertTrue(details["idle_label_cleared"])
        self.assertIn("batch-eval-job", details["reason"])

        # idle_since must be removed from cluster labels
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={"env": "dev"},
            dry_run=False,
        )
        # Node pools must NOT be touched
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_cluster_tinkered_with_today_via_gke_operation_clears_idle_since(self) -> None:
        """Cluster scaled up/updated today via GKE operation clears idle_since and is not downscaled."""
        twelve_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=12)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(labels={"idle_since": twelve_days_ago})

        self.mock_gke_client.check_cluster_activity.side_effect = None
        self.mock_gke_client.check_cluster_activity.return_value = (
            True,
            [],
            "GKE operation 'RESIZE_NODE_POOL' (DONE) was executed on cluster at 2026-08-31T14:30:00Z",
        )

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "active_clusters")
        self.assertTrue(details["idle_label_cleared"])
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={},
            dry_run=False,
        )
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_cluster_accessed_today_via_audit_log_clears_idle_since(self) -> None:
        """Cluster accessed today (e.g. kubectl or get-credentials) clears idle_since and resets countdown."""
        five_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(labels={"idle_since": five_days_ago})

        self.mock_gke_client.check_cluster_activity.side_effect = None
        self.mock_gke_client.check_cluster_activity.return_value = (
            True,
            [],
            "Cloud Audit log detected 'GetCluster' by 'developer@example.com' at 2026-08-31T10:00:00Z",
        )

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "active_clusters")
        self.assertTrue(details["idle_label_cleared"])
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={},
            dry_run=False,
        )
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_active_cluster_with_stale_idle_label_clears_label(self) -> None:
        """Active cluster with running user pods and idle_since label -> label cleared."""
        cluster = create_mock_cluster(
            name="projects/test/locations/us-central1/clusters/active-cluster",
            labels={"idle_since": "2026-08-01", "env": "prod"},
        )
        # Mock active user pods present
        self.mock_gke_client.get_cluster_active_pods.return_value = (
            True,
            [{"name": "web-pod-1", "namespace": "default", "phase": "Running"}],
        )

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "active_clusters")
        self.assertTrue(details["idle_label_cleared"])
        self.assertEqual(details["active_pods_count"], 1)

        # Verify set_cluster_labels called with idle_since removed
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={"env": "prod"},
            dry_run=False,
        )
        # Node pools must NOT be touched
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_active_cluster_without_idle_label_kept_active(self) -> None:
        """Active cluster without idle label remains active without API mutations."""
        cluster = create_mock_cluster(labels={"env": "prod"})
        self.mock_gke_client.get_cluster_active_pods.return_value = (
            True,
            [{"name": "app-pod", "namespace": "custom-ns", "phase": "Running"}],
        )

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "active_clusters")
        self.assertFalse(details["idle_label_cleared"])
        self.mock_gke_client.set_cluster_labels.assert_not_called()
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_newly_idle_cluster_stamps_idle_since(self) -> None:
        """Idle cluster without idle_since label -> stamped with today's date."""
        cluster = create_mock_cluster(labels={"env": "test"})
        # No user pods running
        self.mock_gke_client.get_cluster_active_pods.return_value = (False, [])

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "idle_marked_clusters")
        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(details["idle_since"], today_str)

        # Verify set_cluster_labels stamped idle_since
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={"env": "test", "idle_since": today_str},
            dry_run=False,
        )
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_idle_cluster_under_threshold_remains_pending(self) -> None:
        """Idle cluster idle for 3 days (threshold 7 days) -> idle_pending_threshold."""
        three_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(labels={"idle_since": three_days_ago})
        self.mock_gke_client.get_cluster_active_pods.return_value = (False, [])

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "idle_pending_threshold")
        self.assertEqual(details["idle_days"], 3)
        self.assertEqual(details["threshold"], 7)
        self.assertEqual(details["days_remaining"], 4)

        # No mutation calls
        self.mock_gke_client.set_cluster_labels.assert_not_called()
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_idle_cluster_exceeding_threshold_scales_to_zero(self) -> None:
        """Idle cluster idle for 10 days (threshold 7 days) -> scales node pools to 0."""
        ten_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        pool1 = create_mock_node_pool(name="pool-1", node_count=3)
        pool2 = create_mock_node_pool(name="pool-2", node_count=2)
        cluster = create_mock_cluster(
            labels={"idle_since": ten_days_ago},
            node_pools=[pool1, pool2],
        )
        self.mock_gke_client.get_cluster_active_pods.return_value = (False, [])
        self.mock_gke_client.scale_node_pool_to_zero.side_effect = [
            {"node_pool": "pool-1", "actions": ["set_autoscaling_min_zero", "resize_pool_zero"], "dry_run": False},
            {"node_pool": "pool-2", "actions": ["set_autoscaling_min_zero", "resize_pool_zero"], "dry_run": False},
        ]

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "scaled_down_clusters")
        self.assertEqual(details["idle_days"], 10)
        self.assertEqual(details["threshold"], 7)
        self.assertEqual(details["cluster_type"], "standard")
        self.assertEqual(len(details["node_pools_scaled"]), 2)

        # Verify scale_node_pool_to_zero was called for both pools
        self.assertEqual(self.mock_gke_client.scale_node_pool_to_zero.call_count, 2)

    def test_dry_run_mode_omits_destructive_actions(self) -> None:
        """When dry_run=True, detects scaling need but makes 0 mutating API calls."""
        dry_config = ScalerConfig(project_id="test", idle_days_threshold=7, dry_run=True)
        processor = ClusterProcessor(config=dry_config, gke_client=self.mock_gke_client)

        ten_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(labels={"idle_since": ten_days_ago})
        self.mock_gke_client.get_cluster_active_pods.return_value = (False, [])
        self.mock_gke_client.scale_node_pool_to_zero.return_value = {
            "node_pool": "default-pool",
            "actions": ["dry_run_resize_pool_zero"],
            "dry_run": True,
        }

        category, details = processor.process_cluster(cluster)

        self.assertEqual(category, "scaled_down_clusters")
        self.assertTrue(details["dry_run"])
        self.mock_gke_client.scale_node_pool_to_zero.assert_called_once_with(
            cluster=cluster,
            node_pool=cluster.node_pools[0],
            dry_run=True,
        )

    def test_autopilot_cluster_exceeding_threshold(self) -> None:
        """Autopilot clusters exceeding threshold are logged as autopilot-managed."""
        fifteen_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        cluster = create_mock_cluster(
            labels={"idle_since": fifteen_days_ago},
            is_autopilot=True,
        )
        self.mock_gke_client.get_cluster_active_pods.return_value = (False, [])

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "scaled_down_clusters")
        self.assertEqual(details["cluster_type"], "autopilot")
        self.mock_gke_client.scale_node_pool_to_zero.assert_not_called()

    def test_non_running_cluster_is_skipped(self) -> None:
        """Clusters in PROVISIONING or STOPPING states are skipped."""
        cluster = create_mock_cluster(status="PROVISIONING")
        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "skipped_clusters")
        self.assertIn("not running", details["reason"])
        self.mock_gke_client.get_cluster_active_pods.assert_not_called()

    def test_protected_cluster_with_labels_is_skipped(self) -> None:
        """Clusters with keep-alive, do-not-scale, or protected labels are skipped."""
        cluster1 = create_mock_cluster(labels={"keep-alive": "true"})
        cluster2 = create_mock_cluster(labels={"do-not-scale": "1"})
        cluster3 = create_mock_cluster(labels={"auto-scale": "false"})

        cat1, det1 = self.processor.process_cluster(cluster1)
        cat2, det2 = self.processor.process_cluster(cluster2)
        cat3, det3 = self.processor.process_cluster(cluster3)

        self.assertEqual(cat1, "skipped_clusters")
        self.assertIn("keep-alive", det1["reason"])
        self.assertEqual(cat2, "skipped_clusters")
        self.assertIn("do-not-scale", det2["reason"])
        self.assertEqual(cat3, "skipped_clusters")
        self.assertIn("auto-scale=false", det3["reason"])

        # GKE pod inspection should not be invoked for protected clusters
        self.mock_gke_client.get_cluster_active_pods.assert_not_called()

    def test_protected_cluster_cleans_up_stale_idle_label(self) -> None:
        """Protected cluster with an existing idle_since label removes the idle_since label."""
        cluster = create_mock_cluster(labels={"idle_since": "2026-08-01", "protected": "true"})
        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "skipped_clusters")
        self.mock_gke_client.set_cluster_labels.assert_called_once_with(
            cluster=cluster,
            labels={"protected": "true"},
            dry_run=False,
        )

    def test_k8s_api_failure_handled_as_cluster_error(self) -> None:
        """If Kubernetes API connection fails for a cluster, error is captured gracefully."""
        cluster = create_mock_cluster()
        self.mock_gke_client.get_cluster_active_pods.side_effect = RuntimeError("K8s API connection timeout")

        category, details = self.processor.process_cluster(cluster)

        self.assertEqual(category, "errors")
        self.assertEqual(details["phase"], "pod_inspection")
        self.assertIn("K8s API connection timeout", details["error"])


class TestClusterScalerService(unittest.TestCase):
    """Test suite for full fleet multi-threaded execution."""

    def test_service_run_fleet_orchestration(self) -> None:
        mock_gke_client = MagicMock(spec=GKEClient)
        config = ScalerConfig(project_id="test-fleet-project", idle_days_threshold=7)

        # 3 clusters in project:
        # Cluster 1: Active
        # Cluster 2: Newly idle
        # Cluster 3: Exceeded threshold (scale down)
        c1 = create_mock_cluster(name="projects/test/locations/us-central1/clusters/c1-active", labels={"idle_since": "2026-08-01"})
        c2 = create_mock_cluster(name="projects/test/locations/us-central1/clusters/c2-new-idle", labels={})
        c3_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=12)).strftime("%Y-%m-%d")
        c3 = create_mock_cluster(name="projects/test/locations/us-central1/clusters/c3-scale-me", labels={"idle_since": c3_date})

        mock_gke_client.list_clusters.return_value = [c1, c2, c3]

        def mock_get_pods(cluster: Any, **kwargs: Any) -> tuple[bool, list[Any]]:
            if "c1-active" in cluster.name:
                return True, [{"name": "app", "namespace": "prod", "phase": "Running"}]
            return False, []

        mock_gke_client.get_cluster_active_pods.side_effect = mock_get_pods
        mock_gke_client.scale_node_pool_to_zero.return_value = {"node_pool": "default-pool", "actions": ["resize_pool_zero"]}

        service = ClusterScalerService(config=config, gke_client=mock_gke_client)
        result = service.run()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["project_id"], "test-fleet-project")
        self.assertEqual(result["summary"]["total_clusters_found"], 3)
        self.assertEqual(result["summary"]["active_clusters"], 1)
        self.assertEqual(result["summary"]["idle_marked"], 1)
        self.assertEqual(result["summary"]["scaled_down"], 1)
        self.assertEqual(result["summary"]["errors"], 0)

    def test_service_run_missing_project_id_returns_error(self) -> None:
        config = ScalerConfig(project_id="")
        service = ClusterScalerService(config=config)
        result = service.run()

        self.assertEqual(result["status"], "error")
        self.assertIn("GCP Project ID is required", result["message"])

    def test_service_run_cluster_list_failure(self) -> None:
        mock_gke_client = MagicMock(spec=GKEClient)
        mock_gke_client.list_clusters.side_effect = RuntimeError("PermissionDenied: container.clusters.list")
        config = ScalerConfig(project_id="test-forbidden-proj")

        service = ClusterScalerService(config=config, gke_client=mock_gke_client)
        result = service.run()

        self.assertEqual(result["status"], "error")
        self.assertIn("PermissionDenied", result["message"])


class TestGKEClient(unittest.TestCase):
    """Test suite for GKEClient API wrapping and dynamic K8s credentials."""

    def setUp(self) -> None:
        self.mock_creds = MagicMock()
        self.mock_creds.valid = True
        self.mock_creds.token = "mock-token-xyz"
        self.mock_container_client = MagicMock()
        self.client = GKEClient(
            credentials=self.mock_creds,
            container_client=self.mock_container_client,
        )

    def test_list_clusters(self) -> None:
        mock_response = MagicMock()
        mock_response.clusters = [create_mock_cluster()]
        self.mock_container_client.list_clusters.return_value = mock_response

        clusters = self.client.list_clusters(project_id="proj-1", location="-")
        self.assertEqual(len(clusters), 1)
        self.mock_container_client.list_clusters.assert_called_once_with(
            parent="projects/proj-1/locations/-"
        )

    def test_set_cluster_labels_real_and_dry_run(self) -> None:
        cluster = create_mock_cluster()

        # Dry run does not call container client
        self.client.set_cluster_labels(cluster, {"k": "v"}, dry_run=True)
        self.mock_container_client.set_labels.assert_not_called()

        # Real run calls container client
        self.client.set_cluster_labels(cluster, {"k": "v"}, dry_run=False)
        self.mock_container_client.set_labels.assert_called_once()

    def test_scale_node_pool_to_zero_with_autoscaling(self) -> None:
        pool = create_mock_node_pool(name="pool-a", node_count=5, autoscaling_enabled=True)
        cluster = create_mock_cluster(node_pools=[pool])

        op_mock = MagicMock()
        op_mock.name = "operations/op-1"
        self.mock_container_client.set_node_pool_autoscaling.return_value = op_mock
        self.mock_container_client.set_node_pool_size.return_value = op_mock

        # Mock get_operation returns DONE
        op_done = MagicMock()
        import google.cloud.container_v1 as container_v1
        op_done.status = container_v1.Operation.Status.DONE
        self.mock_container_client.get_operation.return_value = op_done

        res = self.client.scale_node_pool_to_zero(cluster, pool, dry_run=False)
        self.assertIn("set_autoscaling_min_zero", res["actions"])
        self.assertIn("resize_pool_zero", res["actions"])
        self.mock_container_client.set_node_pool_autoscaling.assert_called_once()
        self.mock_container_client.set_node_pool_size.assert_called_once()

    def test_scale_node_pool_already_zero_skips_resize(self) -> None:
        pool = create_mock_node_pool(name="pool-b", node_count=0, autoscaling_enabled=False)
        cluster = create_mock_cluster(node_pools=[pool])

        res = self.client.scale_node_pool_to_zero(cluster, pool, dry_run=False)
        self.assertIn("already_zero", res["actions"])
        self.mock_container_client.set_node_pool_size.assert_not_called()

    def test_wait_for_operation_done(self) -> None:
        op_done = MagicMock()
        import google.cloud.container_v1 as container_v1
        op_done.status = container_v1.Operation.Status.DONE
        self.mock_container_client.get_operation.return_value = op_done

        # Should complete without error
        self.client.wait_for_operation("operations/op-done-123", timeout=10, poll_interval=1)
        self.mock_container_client.get_operation.assert_called_with(name="operations/op-done-123")

    def test_get_recent_cluster_operations(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        two_hours_ago = (now - datetime.timedelta(hours=2)).isoformat()
        two_days_ago = (now - datetime.timedelta(days=2)).isoformat()

        op1 = MagicMock()
        op1.name = "projects/proj-1/locations/us-central1/operations/op-recent"
        op1.target_link = "projects/proj-1/locations/us-central1/clusters/test-cluster"
        op1.operation_type = "RESIZE_NODE_POOL"
        op1.status = "DONE"
        op1.start_time = two_hours_ago

        op2 = MagicMock()
        op2.name = "projects/proj-1/locations/us-central1/operations/op-old"
        op2.target_link = "projects/proj-1/locations/us-central1/clusters/test-cluster"
        op2.operation_type = "UPDATE_CLUSTER"
        op2.status = "DONE"
        op2.start_time = two_days_ago

        mock_resp = MagicMock()
        mock_resp.operations = [op1, op2]
        self.mock_container_client.list_operations.return_value = mock_resp

        cutoff = now - datetime.timedelta(hours=24)
        results = self.client.get_recent_cluster_operations(
            project_id="proj-1",
            location="us-central1",
            cluster_name="test-cluster",
            cutoff_time=cutoff,
        )

        self.assertEqual(len(results), 1)
        self.assertIn("RESIZE_NODE_POOL", results[0])


class TestDualEntrypoints(unittest.TestCase):
    """Test suite for HTTP entrypoint and Functions Framework routing."""

    @patch.object(ClusterScalerService, "run")
    def test_functions_framework_http_entrypoint_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = {
            "status": "success",
            "service": "cluster-scaler",
            "project_id": "http-test-proj",
            "summary": {"total_clusters_found": 1},
        }

        mock_req = MagicMock()
        mock_req.get_json.return_value = {"project_id": "http-test-proj"}
        mock_req.args = {}

        response, status_code = main.check_and_scale_idle_gke(mock_req)
        self.assertEqual(status_code, 200)

    def test_healthz_endpoint(self) -> None:
        response, status_code = main.healthz()
        self.assertEqual(status_code, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["service"], "cluster-scaler")


if __name__ == "__main__":
    unittest.main()

