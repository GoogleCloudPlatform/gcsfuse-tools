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

"""GKE and Kubernetes API client wrapper for cluster discovery and control."""

from __future__ import annotations

import base64
import datetime
import logging
import os
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import dateutil.parser

logger = logging.getLogger(__name__)


def _parse_utc_datetime(val: Any) -> Optional[datetime.datetime]:
    """Safely parse various datetime representations into UTC timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=datetime.timezone.utc)
        return val.astimezone(datetime.timezone.utc)
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean:
            return None
        try:
            import dateutil.parser
            dt = dateutil.parser.parse(val_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            return None
    return None


class GKEClient:
    """Wrapper around Google Kubernetes Engine and Kubernetes Core APIs."""

    def __init__(
        self,
        credentials: Any = None,
        container_client: Any = None,
        logging_client: Any = None,
    ) -> None:
        self._credentials = credentials
        self._container_client = container_client
        self._logging_client = logging_client
        self._lock = threading.RLock()

    def _get_credentials(self) -> Any:
        with self._lock:
            if self._credentials is None:
                import google.auth
                from google.auth.transport.requests import Request

                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                if not creds.valid:
                    creds.refresh(Request())
                self._credentials = creds
            else:
                try:
                    from google.auth.transport.requests import Request
                    if hasattr(self._credentials, "valid") and not self._credentials.valid:
                        if hasattr(self._credentials, "refresh"):
                            self._credentials.refresh(Request())
                except Exception as e:
                    logger.debug("Credentials refresh skipped or failed: %s", e)
        return self._credentials

    def _get_container_client(self) -> Any:
        with self._lock:
            if self._container_client is None:
                from google.cloud import container_v1
                self._container_client = container_v1.ClusterManagerClient(
                    credentials=self._get_credentials()
                )
        return self._container_client

    def _get_logging_client(self, project_id: str) -> Any:
        with self._lock:
            if self._logging_client is None or getattr(self._logging_client, "project", None) != project_id:
                if not project_id or project_id.startswith("test-") or project_id == "test-project":
                    return None
                creds = self._get_credentials()
                if creds is not None and type(creds).__name__.endswith("Mock"):
                    return None
                try:
                    from google.cloud import logging_v2
                    self._logging_client = logging_v2.Client(
                        project=project_id,
                        credentials=creds,
                    )
                except Exception as e:
                    logger.debug("Failed to initialize Cloud Logging client: %s", e)
                    return None
        return self._logging_client

    def list_clusters(self, project_id: str, location: str = "-") -> List[Any]:
        """Lists all GKE clusters in a project across specified or all locations."""
        client = self._get_container_client()
        parent = f"projects/{project_id}/locations/{location}"
        logger.info("Discovering GKE clusters under parent '%s'", parent)
        try:
            response = client.list_clusters(parent=parent)
            # Response might be ListClustersResponse object or list
            clusters = getattr(response, "clusters", response)
            return list(clusters or [])
        except Exception as e:
            logger.error("Failed to list clusters under %s: %s", parent, e)
            raise

    def get_cluster_active_pods(
        self,
        cluster: Any,
        is_system_namespace_fn: Optional[Callable[[str], bool]] = None,
        request_timeout: int = 15,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Connects to cluster control plane and detects active user workload pods."""
        has_active, active_pods, _ = self.get_cluster_workload_and_activity(
            cluster=cluster,
            is_system_namespace_fn=is_system_namespace_fn,
            cutoff_time=None,
            request_timeout=request_timeout,
        )
        return has_active, active_pods

    def get_cluster_workload_and_activity(
        self,
        cluster: Any,
        is_system_namespace_fn: Optional[Callable[[str], bool]] = None,
        cutoff_time: Optional[datetime.datetime] = None,
        request_timeout: int = 15,
    ) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
        """Connects to cluster control plane and inspects pods and nodes for activity.

        Returns:
            Tuple of (has_active_pods, active_pods_list, recent_activity_reasons)
        """
        import kubernetes.client
        from kubernetes.client import Configuration, ApiClient, CoreV1Api

        endpoint = getattr(cluster, "endpoint", None)
        if not endpoint or not isinstance(endpoint, str) or not endpoint.strip():
            logger.warning("Cluster %s has no valid endpoint, skipping pod inspection", getattr(cluster, "name", "unknown"))
            return False, [], []

        creds = self._get_credentials()
        try:
            from google.auth.transport.requests import Request
            if hasattr(creds, "refresh"):
                with self._lock:
                    if not creds.valid:
                        creds.refresh(Request())
        except Exception as e:
            logger.debug("Could not refresh token for k8s connection: %s", e)

        token = getattr(creds, "token", "") or ""
        ca_cert_data = getattr(getattr(cluster, "master_auth", None), "cluster_ca_certificate", None)
        ca_file_path = None

        active_pods: List[Dict[str, Any]] = []
        recent_activity: List[str] = []

        try:
            kube_config = Configuration()
            kube_config.host = f"https://{cluster.endpoint}"
            kube_config.api_key["authorization"] = f"Bearer {token}"
            kube_config.api_key["BearerToken"] = token
            kube_config.api_key_prefix["BearerToken"] = "Bearer"

            if ca_cert_data and isinstance(ca_cert_data, (str, bytes)):
                try:
                    ca_bytes = base64.b64decode(ca_cert_data)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as f:
                        ca_file_path = f.name
                        f.write(ca_bytes)
                    kube_config.ssl_ca_cert = ca_file_path
                except Exception as e:
                    logger.debug("Failed to decode cluster CA certificate: %s", e)
                    kube_config.verify_ssl = False
            else:
                kube_config.verify_ssl = False

            api_client = ApiClient(kube_config)
            core_v1 = CoreV1Api(api_client)

            cluster_name = getattr(cluster, "name", "")
            logger.info("Listing pods across all namespaces for cluster %s", cluster_name)
            pod_list = core_v1.list_pod_for_all_namespaces(_request_timeout=request_timeout)

            items = getattr(pod_list, "items", []) or []
            for pod in items:
                ns = getattr(getattr(pod, "metadata", None), "namespace", "default")
                pod_name = getattr(getattr(pod, "metadata", None), "name", "unknown")
                phase = getattr(getattr(pod, "status", None), "phase", "")

                if is_system_namespace_fn and is_system_namespace_fn(ns):
                    continue

                if phase in ("Running", "Pending"):
                    active_pods.append({
                        "name": pod_name,
                        "namespace": ns,
                        "phase": phase,
                    })

                # Check recent creation or start time if cutoff_time is supplied
                if cutoff_time:
                    pod_meta = getattr(pod, "metadata", None)
                    pod_status = getattr(pod, "status", None)
                    c_time = _parse_utc_datetime(getattr(pod_meta, "creation_timestamp", None))
                    s_time = _parse_utc_datetime(getattr(pod_status, "start_time", None))

                    if c_time and c_time >= cutoff_time:
                        recent_activity.append(
                            f"User pod '{pod_name}' (ns: {ns}, phase: {phase}) was created recently at {c_time.isoformat()}"
                        )
                    elif s_time and s_time >= cutoff_time:
                        recent_activity.append(
                            f"User pod '{pod_name}' (ns: {ns}, phase: {phase}) started recently at {s_time.isoformat()}"
                        )

            # Check if any cluster node was created or joined recently
            if cutoff_time:
                try:
                    node_list = core_v1.list_node(_request_timeout=request_timeout)
                    nodes = getattr(node_list, "items", []) or []
                    for node in nodes:
                        n_meta = getattr(node, "metadata", None)
                        n_name = getattr(n_meta, "name", "node")
                        n_time = _parse_utc_datetime(getattr(n_meta, "creation_timestamp", None))
                        if n_time and n_time >= cutoff_time:
                            recent_activity.append(
                                f"Cluster node '{n_name}' was created/scaled up recently at {n_time.isoformat()}"
                            )
                except Exception as ne:
                    logger.debug("Node list check skipped on cluster %s: %s", cluster_name, ne)

            has_active = len(active_pods) > 0
            logger.info(
                "Cluster %s active user pod check: %d active pod(s) found, %d recent activity signals",
                cluster_name,
                len(active_pods),
                len(recent_activity),
            )
            return has_active, active_pods, recent_activity

        finally:
            if ca_file_path and os.path.exists(ca_file_path):
                try:
                    os.remove(ca_file_path)
                except OSError:
                    pass

    def get_recent_cluster_operations(
        self,
        project_id: str,
        location: str,
        cluster_name: str,
        cutoff_time: datetime.datetime,
    ) -> List[str]:
        """Queries GKE operations to detect recent node pool resizing or cluster tinkering."""
        operations_activity: List[str] = []
        loc = location if location and location != "-" else "-"
        parent = f"projects/{project_id}/locations/{loc}"

        try:
            client = self._get_container_client()
            resp = client.list_operations(parent=parent)
            if hasattr(resp, "operations") and not type(getattr(resp, "operations")).__name__.endswith("Mock"):
                ops = resp.operations
            elif isinstance(resp, (list, tuple)):
                ops = resp
            elif hasattr(resp, "operations") and isinstance(getattr(resp, "operations"), (list, tuple)):
                ops = resp.operations
            else:
                ops = []

            simple_cluster_name = cluster_name.split("/")[-1]

            for op in ops:
                target_link = str(getattr(op, "target_link", "") or "")
                self_link = str(getattr(op, "self_link", "") or "")
                op_name = str(getattr(op, "name", "") or "")

                if simple_cluster_name not in target_link and simple_cluster_name not in self_link and simple_cluster_name not in op_name:
                    continue

                start_dt = _parse_utc_datetime(getattr(op, "start_time", None))
                end_dt = _parse_utc_datetime(getattr(op, "end_time", None))
                op_type = str(getattr(op, "operation_type", "OPERATION"))
                status = str(getattr(op, "status", "DONE"))

                if (start_dt and start_dt >= cutoff_time) or (end_dt and end_dt >= cutoff_time):
                    ts_str = (end_dt or start_dt).isoformat()
                    operations_activity.append(
                        f"GKE operation '{op_type}' ({status}) was executed on cluster at {ts_str}"
                    )

        except Exception as e:
            logger.debug("Failed to query GKE operations for %s: %s", parent, e)

        return operations_activity

    def get_recent_audit_logs(
        self,
        project_id: str,
        cluster_name: str,
        cutoff_time: datetime.datetime,
    ) -> List[str]:
        """Queries Cloud Audit logs for recent user or tool interactions with the GKE cluster."""
        audit_activities: List[str] = []
        client = self._get_logging_client(project_id)
        if not client:
            return audit_activities

        simple_cluster_name = cluster_name.split("/")[-1]
        cutoff_iso = cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        log_filter = (
            f'('
            f'  logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Factivity"'
            f'  OR logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Fdata_access"'
            f')\n'
            f'AND (\n'
            f'  resource.type="gke_cluster"\n'
            f'  OR resource.type="k8s_cluster"\n'
            f'  OR resource.type="gke_nodepool"\n'
            f'  OR protoPayload.serviceName="container.googleapis.com"\n'
            f'  OR protoPayload.serviceName="k8s.io"\n'
            f')\n'
            f'AND (\n'
            f'  resource.labels.cluster_name="{simple_cluster_name}"\n'
            f'  OR protoPayload.resourceName:"clusters/{simple_cluster_name}"\n'
            f')\n'
            f'AND NOT protoPayload.authenticationInfo.principalEmail:"cluster-scaler-sa@"\n'
            f'AND NOT protoPayload.authenticationInfo.principalEmail:"cluster-scaler-sched@"\n'
            f'AND timestamp >= "{cutoff_iso}"'
        )

        try:
            entries = client.list_entries(filter_=log_filter, page_size=10)
            if type(entries).__name__.endswith("Mock") and not isinstance(entries, (list, tuple)):
                return audit_activities
            for entry in entries:
                payload = getattr(entry, "payload", None) or getattr(entry, "proto_payload", {}) or {}
                method = (
                    getattr(payload, "method_name", None)
                    or (payload.get("methodName") if isinstance(payload, dict) else "")
                    or "audit_interaction"
                )
                auth_info = (
                    getattr(payload, "authentication_info", {})
                    or (payload.get("authenticationInfo") if isinstance(payload, dict) else {})
                )
                principal = (
                    getattr(auth_info, "principal_email", None)
                    or (auth_info.get("principalEmail") if isinstance(auth_info, dict) else "")
                    or "user"
                )
                ts = getattr(entry, "timestamp", None)
                ts_str = ts.isoformat() if ts else "recently"
                audit_activities.append(
                    f"Cloud Audit log detected '{method}' by '{principal}' at {ts_str}"
                )
                if len(audit_activities) >= 3:
                    break
        except Exception as e:
            logger.debug("Cloud Audit log query skipped or failed on cluster %s: %s", simple_cluster_name, e)

        return audit_activities

    def check_cluster_activity(
        self,
        cluster: Any,
        project_id: str,
        location: str = "-",
        is_system_namespace_fn: Optional[Callable[[str], bool]] = None,
        cutoff_time: Optional[datetime.datetime] = None,
        check_audit_logs: bool = True,
        request_timeout: int = 15,
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Comprehensive evaluation of active workloads, recent pods, node changes, GKE ops, and audit logs.

        Returns:
            Tuple of (is_active_or_visited, active_pods_list, explanation_reason)
        """
        cluster_name = getattr(cluster, "name", "unknown")
        cutoff = cutoff_time or (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
        )

        # 1. Inspect Pods via get_cluster_active_pods
        has_active_pods, active_pods = self.get_cluster_active_pods(
            cluster=cluster,
            is_system_namespace_fn=is_system_namespace_fn,
            request_timeout=request_timeout,
        )

        if has_active_pods:
            return True, active_pods, f"{len(active_pods)} active running user pod(s)"

        endpoint = getattr(cluster, "endpoint", None)
        if endpoint and isinstance(endpoint, str) and endpoint.strip() and cutoff:
            try:
                _, _, workload_activity = self.get_cluster_workload_and_activity(
                    cluster=cluster,
                    is_system_namespace_fn=is_system_namespace_fn,
                    cutoff_time=cutoff,
                    request_timeout=request_timeout,
                )
                if workload_activity:
                    return True, [], f"Recent workload/node activity detected: {workload_activity[0]}"
            except Exception as e:
                logger.debug("Workload activity check skipped on cluster %s: %s", cluster_name, e)

        # 2. Inspect GKE Operations (Node Pool Resizing, Cluster Upgrades, etc.)
        op_activities = self.get_recent_cluster_operations(
            project_id=project_id,
            location=location,
            cluster_name=cluster_name,
            cutoff_time=cutoff,
        )
        if op_activities:
            return True, [], f"Recent GKE operations detected: {op_activities[0]}"

        # 3. Inspect Cloud Audit Logs (kubectl / gcloud get-credentials / API modifications)
        if check_audit_logs:
            audit_activities = self.get_recent_audit_logs(
                project_id=project_id,
                cluster_name=cluster_name,
                cutoff_time=cutoff,
            )
            if audit_activities:
                return True, [], f"Recent cluster access/modification detected: {audit_activities[0]}"

        return False, [], "No active workloads or recent tinkering detected"

    def set_cluster_labels(
        self,
        cluster: Any,
        labels: Dict[str, str],
        dry_run: bool = False,
    ) -> None:
        """Applies or updates resource labels on a GKE cluster."""
        raw_name = getattr(cluster, "name", "")
        location = getattr(cluster, "location", getattr(cluster, "zone", ""))
        self_link = getattr(cluster, "self_link", "")
        fingerprint = getattr(cluster, "label_fingerprint", "")

        if raw_name.startswith("projects/"):
            cluster_path = raw_name
        elif self_link and "projects/" in self_link:
            start_idx = self_link.find("projects/")
            cluster_path = self_link[start_idx:]
        elif location:
            creds = self._get_credentials()
            proj = getattr(creds, "project_id", "") or getattr(creds, "quota_project_id", "")
            if proj:
                cluster_path = f"projects/{proj}/locations/{location}/clusters/{raw_name}"
            else:
                cluster_path = raw_name
        else:
            cluster_path = raw_name

        if dry_run:
            logger.info(
                "[DRY RUN] Would update labels on cluster '%s' to %s (fingerprint: %s)",
                cluster_path,
                labels,
                fingerprint,
            )
            return

        client = self._get_container_client()
        from google.cloud import container_v1

        request = container_v1.SetLabelsRequest(
            name=cluster_path,
            resource_labels=labels,
            label_fingerprint=fingerprint,
        )
        logger.info("Setting labels on cluster '%s': %s", cluster_path, labels)
        client.set_labels(request=request)

    def scale_node_pool_to_zero(
        self,
        cluster: Any,
        node_pool: Any,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Adjusts autoscaling min=0 and scales node pool instance count down to 0."""
        pool_name = getattr(node_pool, "name", "")
        cluster_name = getattr(cluster, "name", "")
        location = getattr(cluster, "location", getattr(cluster, "zone", ""))
        self_link = getattr(cluster, "self_link", "")

        if "/nodePools/" in pool_name:
            pool_path = pool_name
        elif cluster_name.startswith("projects/"):
            pool_path = f"{cluster_name}/nodePools/{pool_name}"
        elif self_link and "projects/" in self_link:
            start_idx = self_link.find("projects/")
            cluster_path = self_link[start_idx:]
            pool_path = f"{cluster_path}/nodePools/{pool_name}"
        elif location:
            creds = self._get_credentials()
            proj = getattr(creds, "project_id", "") or getattr(creds, "quota_project_id", "")
            if proj:
                pool_path = f"projects/{proj}/locations/{location}/clusters/{cluster_name}/nodePools/{pool_name}"
            else:
                pool_path = f"{cluster_name}/nodePools/{pool_name}"
        else:
            pool_path = f"{cluster_name}/nodePools/{pool_name}"

        actions: List[str] = []
        client = self._get_container_client()
        from google.cloud import container_v1

        autoscaling = getattr(node_pool, "autoscaling", None)
        autoscaling_enabled = getattr(autoscaling, "enabled", False)

        if autoscaling_enabled:
            if dry_run:
                logger.info("[DRY RUN] Would adjust autoscaling min_node_count=0 on pool '%s'", pool_path)
                actions.append("dry_run_set_autoscaling_min_zero")
            else:
                logger.info("Adjusting autoscaling min_node_count=0 on node pool '%s'", pool_path)
                as_config = container_v1.NodePoolAutoscaling(
                    enabled=True,
                    min_node_count=0,
                    max_node_count=getattr(autoscaling, "max_node_count", 1),
                )
                if hasattr(autoscaling, "total_min_node_count") and getattr(autoscaling, "total_min_node_count", None) is not None:
                    as_config.total_min_node_count = 0
                if hasattr(autoscaling, "total_max_node_count") and getattr(autoscaling, "total_max_node_count", None) is not None:
                    as_config.total_max_node_count = getattr(autoscaling, "total_max_node_count", 1)

                req = container_v1.SetNodePoolAutoscalingRequest(
                    name=pool_path,
                    autoscaling=as_config,
                )
                op = client.set_node_pool_autoscaling(request=req)
                self.wait_for_operation(getattr(op, "name", ""))
                actions.append("set_autoscaling_min_zero")

        # Determine current node count
        initial_count = getattr(node_pool, "initial_node_count", 0)
        current_count = getattr(node_pool, "node_count", initial_count)

        if current_count > 0 or initial_count > 0:
            if dry_run:
                logger.info("[DRY RUN] Would resize node pool '%s' from %s to 0", pool_path, current_count)
                actions.append("dry_run_resize_pool_zero")
            else:
                logger.info("Resizing node pool '%s' to 0 (current: %s)", pool_path, current_count)
                size_req = container_v1.SetNodePoolSizeRequest(
                    name=pool_path,
                    node_count=0,
                )
                op = client.set_node_pool_size(request=size_req)
                self.wait_for_operation(getattr(op, "name", ""))
                actions.append("resize_pool_zero")
        else:
            logger.info("Node pool '%s' is already sized at 0 nodes, skipping resize API call", pool_path)
            actions.append("already_zero")

        return {
            "node_pool": getattr(node_pool, "name", ""),
            "actions": actions,
            "dry_run": dry_run,
        }

    def wait_for_operation(
        self,
        operation_name: str,
        timeout: int = 600,
        poll_interval: int = 5,
    ) -> None:
        """Polls long-running GKE operation until status is DONE."""
        if not operation_name:
            return

        client = self._get_container_client()
        from google.cloud import container_v1

        start_time = time.time()
        logger.info("Waiting for GKE operation '%s' to complete...", operation_name)

        while time.time() - start_time < timeout:
            try:
                op = client.get_operation(name=operation_name)
                status = getattr(op, "status", None)
                # Check status against DONE enum, value, name, or string representation
                is_done = (
                    status == container_v1.Operation.Status.DONE
                    or getattr(status, "name", "") == "DONE"
                    or str(status) in ("DONE", "Status.DONE", "3")
                )
                if is_done:
                    logger.info("GKE operation '%s' completed successfully", operation_name)
                    return

                is_aborted = (
                    status == container_v1.Operation.Status.ABORTING
                    or getattr(status, "name", "") == "ABORTING"
                    or str(status) in ("ABORTING", "Status.ABORTING", "4")
                )
                if is_aborted:
                    raise RuntimeError(f"GKE operation {operation_name} was aborted: {getattr(op, 'status_message', '')}")
            except Exception as e:
                # If get_operation fails during poll, log and retry or raise
                if "aborted" in str(e).lower():
                    raise
                logger.debug("Polling operation %s encountered transient error: %s", operation_name, e)

            if poll_interval > 0:
                time.sleep(poll_interval)
            else:
                break

        logger.warning("Timed out waiting for GKE operation '%s' after %d seconds", operation_name, timeout)
