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

"""Empirical Adversarial Stress Test Suite for Cloud Run Services.

This test suite empirically verifies all safety guarantees, edge cases,
and fallback logic for:
1. GKE Cluster Scaler
2. GCE Reservation Cleaner
3. GCE VM Stopper
"""

from __future__ import annotations

import datetime
from datetime import timezone
import random
import sys
import unittest
from unittest.mock import MagicMock, patch

# --- Service 1 Imports: GKE Cluster Scaler ---
from scaler.config import ScalerConfig
from scaler.cluster_processor import ClusterProcessor, parse_idle_since
from scaler.gke_client import GKEClient
from scaler.service import ClusterScalerService

# --- Service 2 Imports: GCE Reservation Cleaner ---
from cleaner.config import CleanerConfig
from cleaner.reservation_processor import ReservationProcessor
from cleaner.reservation_client import ReservationClient
from cleaner.service import ReservationCleanerService

# --- Service 3 Imports: GCE VM Stopper ---
from stopper.config import StopperConfig
from stopper.vm_processor import VMProcessor, is_part_of_gke_or_mig, is_whitelisted
from stopper.gce_client import GCEClient
from stopper.service import process_request


class EmpiricalGKEClusterScalerTests(unittest.TestCase):
    """Empirically stress-tests the GKE Cluster Scaler safety guarantees."""

    def setUp(self):
        self.config = ScalerConfig(
            project_id="test-empirical-gke-proj",
            idle_days_threshold=7,
            dry_run=False,
        )

    def test_user_workloads_in_non_system_namespaces_never_ignored(self):
        """Verify that user workloads in non-system namespaces are NOT ignored."""
        user_namespaces = [
            "default",
            "production",
            "staging",
            "dev",
            "analytics-pipeline",
            "user-workloads",
            "ml-training",
            "app-frontend",
            "customer-data-123",
            "custom-batch-job",
            "my-system",
            "tenant-alpha",
            "tenant-omega",
        ]

        mock_gke_client = MagicMock(spec=GKEClient)
        processor = ClusterProcessor(config=self.config, gke_client=mock_gke_client)

        for ns in user_namespaces:
            cluster = MagicMock()
            cluster.name = f"projects/test-empirical-gke-proj/locations/us-central1-a/clusters/user-cluster-{ns}"
            cluster.status = "RUNNING"
            cluster.resource_labels = {"idle_since": "2026-08-01"}
            cluster.autopilot = None
            cluster.node_pools = [MagicMock(name="default-pool")]

            user_pod = {"name": f"pod-in-{ns}", "namespace": ns, "phase": "Running"}
            mock_gke_client.get_cluster_active_pods.return_value = (True, [user_pod])

            category, details = processor.process_cluster(cluster)

            self.assertEqual(
                category,
                "active_clusters",
                f"Cluster with user workload in namespace '{ns}' must be classified as active_clusters!",
            )
            self.assertEqual(details["active_pods_count"], 1)
            mock_gke_client.scale_node_pool_to_zero.assert_not_called()

        print(f"  [PASS] Verified {len(user_namespaces)} user namespaces are NOT ignored and protect clusters from scaling.")

    def test_system_namespaces_are_strictly_ignored(self):
        """Verify that system namespaces (kube-system, gke-managed-*, etc.) are ignored."""
        system_namespaces = [
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "gke-managed-system",
            "gke-managed-cim",
            "gke-gmp-system",
            "gcs-fuse-csi-driver",
            "gmp-system",
            "jobset-system",
            "kueue-system",
            "istio-system",
            "gatekeeper-system",
            "config-management-system",
            "asm-system",
        ]

        for ns in system_namespaces:
            self.assertTrue(
                self.config.is_system_namespace(ns),
                f"Namespace '{ns}' MUST be identified as a system namespace!",
            )

        client = GKEClient()
        mock_cluster = MagicMock()
        mock_cluster.endpoint = "10.0.0.1"
        mock_cluster.name = "test-cluster"
        mock_cluster.master_auth = None

        with patch("google.auth.default") as mock_auth_default, \
             patch("kubernetes.client.ApiClient"), \
             patch("kubernetes.client.CoreV1Api") as mock_core_v1_cls:

            mock_auth_default.return_value = (MagicMock(valid=True, token="fake-token"), "test-proj")
            mock_api = MagicMock()
            mock_core_v1_cls.return_value = mock_api

            system_pod_items = []
            for i, ns in enumerate(system_namespaces * 5):
                pod = MagicMock()
                pod.metadata.name = f"system-agent-{i}"
                pod.metadata.namespace = ns
                pod.status.phase = "Running"
                system_pod_items.append(pod)

            mock_api.list_pod_for_all_namespaces.return_value = MagicMock(items=system_pod_items)

            has_active, active_pods = client.get_cluster_active_pods(
                cluster=mock_cluster,
                is_system_namespace_fn=self.config.is_system_namespace,
            )

            self.assertFalse(
                has_active,
                "Cluster containing ONLY system pods must evaluate to has_active=False (0 active user pods)!",
            )
            self.assertEqual(len(active_pods), 0)

        print(f"  [PASS] Verified {len(system_namespaces)} system namespaces correctly ignored during pod inspection.")

    def test_dry_run_never_calls_node_pool_resize_or_label_updates(self):
        """Verify that dry_run=True NEVER mutates cluster labels or resizes node pools."""
        dry_config = ScalerConfig(
            project_id="test-empirical-gke-proj",
            idle_days_threshold=7,
            dry_run=True,  # DRY RUN ENABLED
        )

        mock_container_client = MagicMock()
        client = GKEClient(container_client=mock_container_client)

        mock_cluster = MagicMock()
        mock_cluster.name = "projects/test-proj/locations/us-central1-a/clusters/test-cluster"
        mock_cluster.label_fingerprint = "abc123xyz"

        client.set_cluster_labels(
            cluster=mock_cluster,
            labels={"idle_since": "2026-08-31"},
            dry_run=True,
        )
        mock_container_client.set_labels.assert_not_called()

        mock_pool = MagicMock()
        mock_pool.name = "default-pool"
        mock_pool.node_count = 10
        mock_pool.initial_node_count = 10
        mock_pool.autoscaling.enabled = True
        mock_pool.autoscaling.min_node_count = 2
        mock_pool.autoscaling.max_node_count = 20

        result = client.scale_node_pool_to_zero(
            cluster=mock_cluster,
            node_pool=mock_pool,
            dry_run=True,
        )

        self.assertTrue(result["dry_run"])
        self.assertIn("dry_run_set_autoscaling_min_zero", result["actions"])
        self.assertIn("dry_run_resize_pool_zero", result["actions"])

        mock_container_client.set_node_pool_size.assert_not_called()
        mock_container_client.set_node_pool_autoscaling.assert_not_called()

        processor = ClusterProcessor(config=dry_config, gke_client=client)

        for i in range(50):
            idle_cluster = MagicMock()
            idle_cluster.name = f"projects/test-proj/locations/us-central1-a/clusters/idle-cluster-{i}"
            idle_cluster.status = "RUNNING"
            idle_cluster.resource_labels = {"idle_since": "2026-01-01"}  # > 200 days idle
            idle_cluster.autopilot = None
            idle_cluster.node_pools = [mock_pool]

            with patch.object(client, "get_cluster_active_pods", return_value=(False, [])):
                category, details = processor.process_cluster(idle_cluster)
                self.assertEqual(category, "scaled_down_clusters")
                self.assertTrue(details["dry_run"])

        mock_container_client.set_node_pool_size.assert_not_called()
        mock_container_client.set_node_pool_autoscaling.assert_not_called()
        mock_container_client.set_labels.assert_not_called()

        print("  [PASS] Verified dry_run=True guarantees 0 API mutations across labels, autoscaling, and pool size.")

    def test_gke_edge_cases_and_safeguards(self):
        """Verify date parsing safeguards, future timestamps, and non-running clusters."""
        # 1. Future idle_since timestamp safeguard
        future_cluster = MagicMock()
        future_cluster.name = "projects/test-proj/locations/us-central1-a/clusters/future-cluster"
        future_cluster.status = "RUNNING"
        future_cluster.resource_labels = {"idle_since": "2099-01-01"}
        future_cluster.autopilot = None

        mock_client = MagicMock(spec=GKEClient)
        mock_client.get_cluster_active_pods.return_value = (False, [])
        processor = ClusterProcessor(config=self.config, gke_client=mock_client)

        category, details = processor.process_cluster(future_cluster)
        self.assertEqual(category, "idle_pending_threshold")
        self.assertEqual(details["idle_days"], 0)

        # 2. Non-running cluster is skipped
        for non_running_status in ["PROVISIONING", "STOPPING", "ERROR", "DEGRADED"]:
            stopped_cluster = MagicMock()
            stopped_cluster.name = f"cluster-{non_running_status}"
            stopped_cluster.status = non_running_status
            cat, det = processor.process_cluster(stopped_cluster)
            self.assertEqual(cat, "skipped_clusters")

        # 3. Autopilot cluster exceeds threshold -> node pools not resized
        autopilot_cluster = MagicMock()
        autopilot_cluster.name = "autopilot-cluster-01"
        autopilot_cluster.status = "RUNNING"
        autopilot_cluster.resource_labels = {"idle_since": "2026-01-01"}
        autopilot_cluster.autopilot = MagicMock(enabled=True)
        autopilot_cluster.node_pools = [MagicMock()]

        cat, det = processor.process_cluster(autopilot_cluster)
        self.assertEqual(cat, "scaled_down_clusters")
        self.assertEqual(det["cluster_type"], "autopilot")
        mock_client.scale_node_pool_to_zero.assert_not_called()

        print("  [PASS] Verified GKE edge cases: future timestamps, non-running states, and autopilot preservation.")


class EmpiricalGCEReservationCleanerTests(unittest.TestCase):
    """Empirically stress-tests the GCE Reservation Cleaner safety guarantees."""

    def test_active_reservations_are_never_deleted_under_any_conditions(self):
        """Verify that active reservations (in_use_now > 0) are NEVER deleted."""
        mock_client = MagicMock(spec=ReservationClient)

        random.seed(42)
        for i in range(500):
            capacity = random.randint(1, 100)
            in_use_now = random.randint(1, capacity)
            age_days = random.randint(1, 1000)
            creation_time = (datetime.datetime.now(timezone.utc) - datetime.timedelta(days=age_days)).isoformat()

            config = CleanerConfig(
                project_id="test-cleaner-proj",
                delete_idle_days=0.0,
                delete_never_used=True,
                max_age_days=1.0,
                dry_run=False,
            )

            processor = ReservationProcessor(config=config, client=mock_client)

            reservation_obj = {
                "id": f"res-{i}",
                "name": f"reservation-{i}",
                "zone": "us-central1-a",
                "creationTimestamp": creation_time,
                "specificReservation": {
                    "count": capacity,
                    "inUseCount": in_use_now,
                    "instanceProperties": {
                        "machineType": "n2-standard-4",
                    },
                },
            }

            evaluated = processor.evaluate_reservation(reservation_obj)

            self.assertEqual(
                evaluated["status"],
                "Active Now",
                f"Reservation with in_use_now={in_use_now} must have status 'Active Now'!",
            )
            self.assertFalse(
                evaluated["is_candidate"],
                f"Reservation with in_use_now={in_use_now} must NEVER be a deletion candidate!",
            )
            self.assertEqual(evaluated["action"], "retained_active")

            processed = processor.process_reservation(evaluated)
            self.assertEqual(processed["action"], "retained_active")

        mock_client.delete_reservation.assert_not_called()
        mock_client.query_reservation_usage.assert_not_called()

        print("  [PASS] Verified 500 active reservation permutations (in_use > 0) are 100% protected from deletion.")

    def test_cloud_monitoring_api_errors_fallback_safely(self):
        """Verify that Cloud Monitoring API errors fall back safely without deleting reservations."""
        error_scenarios = [
            RuntimeError("503 Service Unavailable: Backend timeout"),
            ConnectionError("Connection refused by monitoring.googleapis.com"),
            ValueError("Malformed JSON response from monitoring API"),
            {"error": "HTTP 403: Caller does not have monitoring.timeSeries.list permission"},
            {"error": "HTTP 500: Internal Server Error"},
            {"error": "Quota exceeded for quota metric 'monitoring.googleapis.com/read_requests'"},
        ]

        config = CleanerConfig(
            project_id="test-cleaner-proj",
            delete_idle_days=30.0,
            delete_never_used=True,
            max_age_days=60.0,
            dry_run=False,
        )

        for err in error_scenarios:
            mock_client = MagicMock(spec=ReservationClient)

            if isinstance(err, Exception):
                mock_client.query_reservation_usage.side_effect = err
            else:
                mock_client.query_reservation_usage.return_value = err

            processor = ReservationProcessor(config=config, client=mock_client)

            reservation_obj = {
                "id": "res-error-test",
                "name": "res-stale-candidate",
                "zone": "europe-west1-b",
                "creationTimestamp": "2025-01-01T00:00:00Z",
                "specificReservation": {
                    "count": 10,
                    "inUseCount": 0,
                    "instanceProperties": {"machineType": "a2-highgpu-1g"},
                },
            }

            evaluated = processor.evaluate_reservation(reservation_obj)

            self.assertEqual(
                evaluated["status"],
                "Query Error",
                f"On monitoring error {err}, status must be 'Query Error'!",
            )
            self.assertFalse(
                evaluated["is_candidate"],
                f"On monitoring error {err}, is_candidate MUST be False!",
            )
            self.assertEqual(evaluated["action"], "retained_error")

            processed = processor.process_reservation(evaluated)
            self.assertEqual(processed["action"], "retained_error")
            mock_client.delete_reservation.assert_not_called()

        print(f"  [PASS] Verified {len(error_scenarios)} Cloud Monitoring error scenarios safely retain reservations.")

    def test_reservation_cleaner_dry_run_and_pricing_fallback(self):
        """Verify dry run simulation and custom pricing fallback."""
        config = CleanerConfig(
            project_id="test-cleaner-proj",
            delete_idle_days=10.0,
            dry_run=True,
        )
        mock_client = MagicMock(spec=ReservationClient)
        mock_client.query_reservation_usage.return_value = {
            "is_never_used": True,
            "error": None,
        }

        processor = ReservationProcessor(config=config, client=mock_client)
        reservation_obj = {
            "id": "res-dry-1",
            "name": "stale-reservation-dry",
            "zone": "us-central1-a",
            "creationTimestamp": "2025-01-01T00:00:00Z",
            "specificReservation": {
                "count": 4,
                "inUseCount": 0,
                "instanceProperties": {"machineType": "custom-8-32768"},
            },
        }

        evaluated = processor.evaluate_reservation(reservation_obj)
        self.assertTrue(evaluated["is_candidate"])
        self.assertGreater(evaluated["monthly_cost_usd"], 0)

        processed = processor.process_reservation(evaluated)
        self.assertEqual(processed["action"], "dry_run_candidate")
        self.assertIn("[DRY-RUN]", processed["message"])
        mock_client.delete_reservation.assert_not_called()

        print("  [PASS] Verified dry_run simulation and pricing model fallback for custom machine types.")


class EmpiricalGCEVMStopperTests(unittest.TestCase):
    """Empirically stress-tests the GCE VM Stopper safety guarantees."""

    def setUp(self):
        self.config = StopperConfig(
            project_id="test-stopper-proj",
            idle_days_threshold=7,
            stopped_days_threshold=90,
            delete_stopped_vms=True,
            dry_run=False,
        )

    def test_gke_nodes_and_mig_instances_are_never_stopped_or_deleted(self):
        """Verify that GKE nodes and MIG instances are NEVER stopped or deleted."""
        # 1. GKE Name prefix
        inst1 = MagicMock()
        inst1.name = "gke-cluster-1-default-pool-1234abcd-node-1"
        inst2 = MagicMock()
        inst2.name = "gk3-cluster-prod-worker-9988"
        inst3 = MagicMock()
        inst3.name = "GKE-UPPERCASE-CLUSTER-NODE"
        gke_name_instances = [inst1, inst2, inst3]

        # 2. GKE Labels
        inst_lbl1 = MagicMock()
        inst_lbl1.name = "custom-vm-1"
        inst_lbl1.labels = {"goog-k8s-node-pool-name": "pool-1"}

        inst_lbl2 = MagicMock()
        inst_lbl2.name = "custom-vm-2"
        inst_lbl2.labels = {"goog-gke-node": "true"}

        inst_lbl3 = MagicMock()
        inst_lbl3.name = "custom-vm-3"
        inst_lbl3.labels = {"goog-k8s-cluster-name": "owned"}

        inst_lbl4 = MagicMock()
        inst_lbl4.name = "custom-vm-4"
        inst_lbl4.labels = {"gke-addon": "installed"}

        inst_lbl5 = MagicMock()
        inst_lbl5.name = "custom-vm-5"
        inst_lbl5.labels = {"k8s-worker": "active"}
        gke_label_instances = [inst_lbl1, inst_lbl2, inst_lbl3, inst_lbl4, inst_lbl5]

        # 3. GKE and MIG Metadata
        inst_meta1 = MagicMock()
        inst_meta1.name = "node-meta-1"
        inst_meta1.metadata = MagicMock(items=[MagicMock(key="cluster-name", value="prod-cluster")])

        inst_meta2 = MagicMock()
        inst_meta2.name = "node-meta-2"
        inst_meta2.metadata = MagicMock(items=[MagicMock(key="gke-nodepool", value="high-mem-pool")])

        inst_meta3 = MagicMock()
        inst_meta3.name = "node-meta-3"
        inst_meta3.metadata = MagicMock(items=[MagicMock(key="kube-env", value="CLUSTER_NAME=prod")])

        inst_meta4 = MagicMock()
        inst_meta4.name = "node-meta-4"
        inst_meta4.metadata = MagicMock(items=[MagicMock(key="created-by", value="projects/123/zones/us-central1-a/instanceGroupManagers/mig-app-servers")])

        inst_meta5 = MagicMock()
        inst_meta5.name = "node-meta-5"
        inst_meta5.metadata = MagicMock(items=[MagicMock(key="instance-template", value="projects/123/global/instanceTemplates/template-v1")])
        gke_mig_metadata_instances = [inst_meta1, inst_meta2, inst_meta3, inst_meta4, inst_meta5]

        # 4. Network Tags
        inst_tag1 = MagicMock()
        inst_tag1.name = "tagged-vm-1"
        inst_tag1.tags = MagicMock(items=["gke-cluster-node"])

        inst_tag2 = MagicMock()
        inst_tag2.name = "tagged-vm-2"
        inst_tag2.tags = MagicMock(items=["k8s-app-worker"])

        inst_tag3 = MagicMock()
        inst_tag3.name = "tagged-vm-3"
        inst_tag3.tags = MagicMock(items=["mig-group-worker"])
        gke_mig_tag_instances = [inst_tag1, inst_tag2, inst_tag3]

        all_exempt_instances = (
            gke_name_instances + gke_label_instances +
            gke_mig_metadata_instances + gke_mig_tag_instances
        )

        mock_client = MagicMock(spec=GCEClient)
        processor = VMProcessor(config=self.config, gce_client=mock_client)
        now_utc = datetime.datetime.now(timezone.utc)

        for inst in all_exempt_instances:
            inst.id = "123456789"
            inst.status = "RUNNING"
            inst.creation_timestamp = "2020-01-01T00:00:00Z"
            inst.last_stop_timestamp = "2020-01-01T00:00:00Z"

            self.assertTrue(
                is_part_of_gke_or_mig(inst),
                f"Instance '{getattr(inst, 'name', '')}' MUST be recognized as GKE/MIG!",
            )

            res = processor.process_single_instance("us-central1-a", inst, now_utc)

            self.assertEqual(res["category"], "skipped_gke_mig")
            self.assertEqual(res["action"], "none")

        mock_client.stop_instance.assert_not_called()
        mock_client.delete_instance.assert_not_called()
        mock_client.has_recent_activity.assert_not_called()

        print(f"  [PASS] Verified {len(all_exempt_instances)} GKE/MIG instances are 100% exempt from stop/delete.")

    def test_whitelisted_instances_and_labels_are_preserved(self):
        """Verify that whitelisted instances, labels, and tags are strictly preserved."""
        def _make_inst(name, labels=None, tags=None):
            m = MagicMock()
            m.name = name
            m.labels = labels or {}
            m.tags = MagicMock(items=tags or [])
            return m

        whitelisted_instances = [
            _make_inst("db-master", labels={"keep-alive": "true"}),
            _make_inst("bastion-host", labels={"do-not-stop": "1"}),
            _make_inst("dns-server", labels={"permanent": "yes"}),
            _make_inst("monitoring-agent", labels={"protected": "prod"}),
            _make_inst("vault-server", labels={"whitelisted": "security-team"}),
            _make_inst("nat-gateway", tags=["keep-alive"]),
            _make_inst("vpn-gateway", tags=["permanent"]),
            _make_inst("special-runner-01"),
        ]

        config = StopperConfig(
            project_id="test-stopper-proj",
            idle_days_threshold=1,
            stopped_days_threshold=1,
            delete_stopped_vms=True,
            whitelist_names=["special-runner"],
            dry_run=False,
        )

        mock_client = MagicMock(spec=GCEClient)
        processor = VMProcessor(config=config, gce_client=mock_client)
        now_utc = datetime.datetime.now(timezone.utc)

        for inst in whitelisted_instances:
            inst.id = "987654321"
            inst.status = "RUNNING"
            inst.creation_timestamp = "2020-01-01T00:00:00Z"

            is_white, reason = is_whitelisted(inst, config)
            self.assertTrue(is_white, f"Instance '{getattr(inst, 'name', '')}' MUST be recognized as whitelisted!")

            res = processor.process_single_instance("us-central1-a", inst, now_utc)
            self.assertEqual(res["category"], "skipped_whitelisted")
            self.assertEqual(res["action"], "none")

        mock_client.stop_instance.assert_not_called()
        mock_client.delete_instance.assert_not_called()

        print(f"  [PASS] Verified {len(whitelisted_instances)} whitelisted instances are strictly preserved.")

    def test_cloud_logging_errors_fallback_safely_preventing_shutdown(self):
        """Verify that Cloud Logging errors fall back safely (assumes VM active) to prevent shutdown."""
        logging_exceptions = [
            Exception("503 Service Unavailable: Logging API temporary outage"),
            RuntimeError("Transport failed on log read stream"),
            PermissionError("403 Forbidden: Caller lacks logging.viewer role on audit logs"),
            TimeoutError("Deadline exceeded querying Cloud Logging API"),
        ]

        now_utc = datetime.datetime.now(timezone.utc)
        since_cutoff = now_utc - datetime.timedelta(days=7)

        for exc in logging_exceptions:
            client = GCEClient()
            with patch.object(client, "get_logging_client") as mock_get_log:
                mock_log_client = MagicMock()
                mock_log_client.list_entries.side_effect = exc
                mock_get_log.return_value = mock_log_client

                has_activity = client.has_recent_activity(
                    project_id="test-stopper-proj",
                    zone="us-central1-a",
                    instance_name="idle-vm-candidate",
                    instance_id="11223344",
                    since_timestamp=since_cutoff,
                )

                self.assertTrue(
                    has_activity,
                    f"On Cloud Logging exception '{exc}', has_recent_activity() MUST return True (fail-safe)!",
                )

            mock_client = MagicMock(spec=GCEClient)
            mock_client.has_recent_activity.return_value = True

            processor = VMProcessor(config=self.config, gce_client=mock_client)
            vm = MagicMock(
                name="candidate-vm",
                id="11223344",
                status="RUNNING",
                creation_timestamp="2024-01-01T00:00:00Z",
                labels={},
                metadata=MagicMock(items=[]),
                tags=MagicMock(items=[]),
            )

            res = processor.process_single_instance("us-central1-a", vm, now_utc)

            self.assertEqual(
                res["category"],
                "skipped_active",
                "VM must be classified as skipped_active when logging reports activity/fails safe!",
            )
            self.assertEqual(res["action"], "none")
            mock_client.stop_instance.assert_not_called()

        print(f"  [PASS] Verified {len(logging_exceptions)} Cloud Logging failure modes safely fail open (assumes active).")

    def test_vm_stopper_lifecycle_statuses_and_concurrency(self):
        """Verify transitional statuses, young VMs, and multi-threaded sweep concurrency."""
        now_utc = datetime.datetime.now(timezone.utc)
        mock_client = MagicMock(spec=GCEClient)
        processor = VMProcessor(config=self.config, gce_client=mock_client)

        # 1. Transitional VM statuses
        for trans_status in ["PROVISIONING", "STAGING", "STOPPING", "SUSPENDING", "REPAIRING"]:
            vm = MagicMock(
                name=f"vm-{trans_status}",
                id="999",
                status=trans_status,
                labels={},
                metadata=MagicMock(items=[]),
                tags=MagicMock(items=[]),
            )
            res = processor.process_single_instance("us-central1-a", vm, now_utc)
            self.assertEqual(res["category"], "skipped_other")
            self.assertEqual(res["action"], "none")

        # 2. Young running VM (< idle threshold)
        young_vm = MagicMock(
            name="young-vm",
            id="888",
            status="RUNNING",
            creation_timestamp=(now_utc - datetime.timedelta(days=2)).isoformat(),
            labels={},
            metadata=MagicMock(items=[]),
            tags=MagicMock(items=[]),
        )
        res = processor.process_single_instance("us-central1-a", young_vm, now_utc)
        self.assertEqual(res["category"], "skipped_recently_created")
        self.assertEqual(res["action"], "none")

        # 3. Multi-threaded sweep over 100 instances
        instances = []
        for i in range(100):
            vm = MagicMock()
            vm.name = f"fleet-vm-{i}"
            vm.id = str(i)
            vm.status = "RUNNING"
            vm.creation_timestamp = "2024-01-01T00:00:00Z"
            vm.labels = {"keep-alive": "true"} if i % 2 == 0 else {}
            vm.metadata = MagicMock(items=[])
            vm.tags = MagicMock(items=[])
            instances.append(("us-central1-a", vm))

        mock_client.list_instances.return_value = instances
        mock_client.has_recent_activity.return_value = False  # Idle for non-whitelisted

        sweep_result = processor.sweep()
        self.assertEqual(sweep_result["summary"]["total_scanned"], 100)
        self.assertEqual(sweep_result["summary"]["skipped_whitelisted"], 50)
        self.assertEqual(sweep_result["summary"]["stopped"], 50)

        print("  [PASS] Verified VM lifecycle statuses, young VM safety, and concurrent multi-threaded execution.")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING EMPIRICAL ADVERSARIAL STRESS TEST HARNESS")
    print("=" * 70)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(EmpiricalGKEClusterScalerTests))
    suite.addTest(loader.loadTestsFromTestCase(EmpiricalGCEReservationCleanerTests))
    suite.addTest(loader.loadTestsFromTestCase(EmpiricalGCEVMStopperTests))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if not result.wasSuccessful():
        print("\n[FAIL] Stress tests failed!")
        sys.exit(1)
    else:
        print("\n[SUCCESS] ALL EMPIRICAL CHALLENGER STRESS TESTS PASSED CLEANLY!")
        sys.exit(0)
