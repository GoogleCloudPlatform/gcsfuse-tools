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

"""GCE Compute and Cloud Logging client wrappers for VM Stopper."""

from datetime import datetime, timezone
import logging
import random
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import google.auth
from google.cloud import compute_v1
from google.cloud import logging_v2

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe token bucket rate limiter for pacing API requests."""

    def __init__(self, max_per_minute: int = 40, burst_capacity: int = 5):
        self.rate = max_per_minute / 60.0 if max_per_minute > 0 else 0.0
        self.capacity = float(burst_capacity)
        self.tokens = float(burst_capacity)
        self.last_check = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available."""
        if self.rate <= 0:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_check
                self.last_check = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                needed = 1.0 - self.tokens
                wait_time = needed / self.rate

            time.sleep(wait_time)


def is_rate_limit_error(exc: BaseException) -> bool:
    """Determine if an exception is due to API rate limiting or quota exhaustion (HTTP 429)."""
    try:
        from google.api_core import exceptions as gcp_exceptions
        if isinstance(exc, (gcp_exceptions.TooManyRequests, gcp_exceptions.ResourceExhausted)):
            return True
    except ImportError:
        pass

    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 8):
        return True
    if hasattr(code, "value") and code.value in (429, 8):
        return True

    msg = str(exc).lower()
    rate_limit_keywords = (
        "429",
        "rate_limit_exceeded",
        "rate limit exceeded",
        "quota exceeded",
        "read requests per minute",
        "read requests",
        "too many requests",
        "resource exhausted",
        "resourceexhausted",
    )
    return any(kw in msg for kw in rate_limit_keywords)


class GCEClient:
    """Client wrapper for Google Compute Engine and Cloud Logging APIs."""

    def __init__(
        self,
        credentials: Optional[google.auth.credentials.Credentials] = None,
        rate_limit_per_minute: int = 40,
        max_retries: int = 4,
        backoff_base: float = 2.0,
    ):
        self.credentials = credentials
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.1, backoff_base)
        self.rate_limiter = RateLimiter(max_per_minute=rate_limit_per_minute)
        self._instances_client: Optional[compute_v1.InstancesClient] = None
        self._logging_client: Optional[logging_v2.Client] = None

    @property
    def instances_client(self) -> compute_v1.InstancesClient:
        """Lazy-initialize and return the GCE InstancesClient."""
        if self._instances_client is None:
            if self.credentials:
                self._instances_client = compute_v1.InstancesClient(credentials=self.credentials)
            else:
                self._instances_client = compute_v1.InstancesClient()
        return self._instances_client

    def get_logging_client(self, project_id: str) -> logging_v2.Client:
        """Return a Cloud Logging client for the specified project."""
        if self._logging_client is None or getattr(self._logging_client, "project", None) != project_id:
            if self.credentials:
                self._logging_client = logging_v2.Client(
                    project=project_id,
                    credentials=self.credentials,
                    _use_grpc=False,
                )
            else:
                self._logging_client = logging_v2.Client(
                    project=project_id,
                    _use_grpc=False,
                )
        return self._logging_client

    def list_instances(self, project_id: str) -> List[Tuple[str, Any]]:
        """List all GCE instances across all zones in the target project.

        Returns:
            A list of tuples (zone_name, instance_object).
        """
        logger.info("Scanning GCE compute instances in project '%s' across all zones...", project_id)
        request = compute_v1.AggregatedListInstancesRequest(project=project_id)
        aggregated_result = self.instances_client.aggregated_list(request=request)

        instances: List[Tuple[str, Any]] = []

        # AggregatedListPager in google-cloud-compute yields (zone_name, scoped_list) tuples directly.
        # Fall back to dict.items() or iterating directly for unit-test mock compatibility.
        if isinstance(aggregated_result, dict):
            items = aggregated_result.items()
        else:
            items = aggregated_result

        for item in items:
            if isinstance(item, tuple) and len(item) == 2:
                zone_raw, scoped_list = item
            else:
                try:
                    zone_raw, scoped_list = item
                except Exception:
                    continue

            # Strip 'zones/' prefix if present
            zone = zone_raw.split("/")[-1] if "/" in zone_raw else zone_raw
            if not scoped_list:
                continue
            instance_list = getattr(scoped_list, "instances", None)
            if instance_list:
                for inst in instance_list:
                    instances.append((zone, inst))

        logger.info("Discovered %d total instances in project '%s'.", len(instances), project_id)
        return instances

    def _execute_logging_query_with_retry(
        self,
        project_id: str,
        log_filter: str,
        page_size: int = 1,
        max_results: Optional[int] = 1,
        target_description: str = "instance",
    ) -> List[Any]:
        """Execute Cloud Logging query with token-bucket rate limiting and exponential backoff retry on 429."""
        for attempt in range(self.max_retries + 1):
            try:
                if self.rate_limiter:
                    self.rate_limiter.acquire()
                client = self.get_logging_client(project_id)
                entries = client.list_entries(
                    filter_=log_filter,
                    page_size=page_size,
                    max_results=max_results,
                )
                collected: List[Any] = []
                if entries is not None:
                    for entry in entries:
                        collected.append(entry)
                        if max_results and len(collected) >= max_results:
                            break
                return collected
            except Exception as exc:
                if is_rate_limit_error(exc) and attempt < self.max_retries:
                    delay = min(self.backoff_base * (2 ** attempt) + random.uniform(0.5, 1.5), 60.0)
                    logger.warning(
                        "Cloud Logging quota/rate limit exceeded (HTTP 429) for %s (attempt %d/%d). "
                        "Backing off for %.1fs before retrying...",
                        target_description,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise
        return []

    def has_recent_activity(
        self,
        project_id: str,
        zone: str,
        instance_name: str,
        instance_id: str,
        since_timestamp: datetime,
    ) -> bool:
        """Check Cloud Logging for recent OSLogin audit events or SSH/metadata activity.

        Safety Guard:
        If Cloud Logging queries fail or raise any exception (e.g. IAM permission error,
        network timeout), this method logs a warning and returns True (assumes ACTIVE)
        to prevent accidental stopping of workloads.

        Args:
            project_id: Target GCP project.
            zone: Compute zone name (e.g. 'us-central1-a').
            instance_name: VM instance name.
            instance_id: Numeric or string instance ID.
            since_timestamp: UTC datetime cutoff.

        Returns:
            True if recent activity detected or on error (safe fallback); False if confirmed idle.
        """
        if since_timestamp.tzinfo is None:
            since_timestamp = since_timestamp.replace(tzinfo=timezone.utc)
        cutoff_iso = since_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build compound audit and activity log filter
        log_filter = (
            f'(\n'
            f'  (\n'
            f'    logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Fdata_access"\n'
            f'    AND resource.type="audited_resource"\n'
            f'    AND protoPayload.serviceName="oslogin.googleapis.com"\n'
            f'    AND protoPayload.resourceName="projects/{project_id}/zones/{zone}/instances/{instance_name}"\n'
            f'  )\n'
            f'  OR\n'
            f'  (\n'
            f'    logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Factivity"\n'
            f'    AND resource.type="gce_instance"\n'
            f'    AND resource.labels.instance_id="{instance_id}"\n'
            f'    AND (protoPayload.methodName:"setMetadata" OR protoPayload.methodName:"setInstanceAttributes" OR protoPayload.methodName:"setCommonInstanceMetadata" OR protoPayload.methodName:"oslogin")\n'
            f'  )\n'
            f'  OR\n'
            f'  (\n'
            f'    resource.type="gce_instance"\n'
            f'    AND resource.labels.instance_id="{instance_id}"\n'
            f'    AND (protoPayload.methodName:"oslogin" OR jsonPayload.event_subtype="compute.instances.osLogin")\n'
            f'  )\n'
            f')\n'
            f'AND NOT protoPayload.authenticationInfo.principalEmail="vm-stopper-sa@{project_id}.iam.gserviceaccount.com"\n'
            f'AND NOT protoPayload.authenticationInfo.principalEmail="vm-stopper-sched@{project_id}.iam.gserviceaccount.com"\n'
            f'AND timestamp >= "{cutoff_iso}"'
        )

        try:
            entries = self._execute_logging_query_with_retry(
                project_id=project_id,
                log_filter=log_filter,
                page_size=1,
                max_results=1,
                target_description=f"instance {instance_name} in zone {zone}",
            )
            for _ in entries:
                logger.info(
                    "Recent activity log detected for instance %s (id: %s) in zone %s.",
                    instance_name,
                    instance_id,
                    zone,
                )
                return True
            logger.info(
                "No recent activity logs found since %s for instance %s in zone %s.",
                cutoff_iso,
                instance_name,
                zone,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Cloud Logging query failed for instance %s in zone %s: %s. "
                "Failing safe: assuming instance is ACTIVE to prevent accidental stop.",
                instance_name,
                zone,
                exc,
            )
            return True

    def get_instances_activity(
        self,
        project_id: str,
        candidate_instances: List[Tuple[str, Any]],
        since_timestamp: datetime,
        batch_size: int = 25,
    ) -> Dict[Tuple[str, str], bool]:
        """Batch-query Cloud Logging for recent activity across candidate instances.

        Reduces API call volume by batching multiple candidate instances into compound
        Cloud Logging queries, avoiding the Cloud Logging 60 req/min quota exhaustion.

        Args:
            project_id: Target GCP project.
            candidate_instances: List of (zone, instance_object) tuples.
            since_timestamp: UTC datetime cutoff.
            batch_size: Max instances to evaluate in a single query (default: 25).

        Returns:
            Dict mapping (zone, instance_name) to bool (True if active, False if confirmed idle).
        """
        if not candidate_instances:
            return {}

        if since_timestamp.tzinfo is None:
            since_timestamp = since_timestamp.replace(tzinfo=timezone.utc)
        cutoff_iso = since_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

        results: Dict[Tuple[str, str], bool] = {}

        for i in range(0, len(candidate_instances), batch_size):
            batch = candidate_instances[i : i + batch_size]
            batch_meta: List[Tuple[str, str, str]] = []
            for zone, inst in batch:
                name = str(getattr(inst, "name", "") or "")
                iid = str(getattr(inst, "id", "") or "")
                batch_meta.append((zone, name, iid))

            oslogin_clauses = [
                f'protoPayload.resourceName="projects/{project_id}/zones/{z}/instances/{n}"'
                for z, n, _ in batch_meta if z and n
            ]
            id_clauses = [
                f'resource.labels.instance_id="{iid}"'
                for _, _, iid in batch_meta if iid
            ]

            if not oslogin_clauses and not id_clauses:
                continue

            filter_parts = []
            if oslogin_clauses:
                oslogin_joined = " OR\n      ".join(oslogin_clauses)
                filter_parts.append(
                    f'(\n'
                    f'  logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Fdata_access"\n'
                    f'  AND resource.type="audited_resource"\n'
                    f'  AND protoPayload.serviceName="oslogin.googleapis.com"\n'
                    f'  AND (\n      {oslogin_joined}\n  )\n'
                    f')'
                )

            if id_clauses:
                id_joined = " OR ".join(id_clauses)
                filter_parts.append(
                    f'(\n'
                    f'  resource.type="gce_instance"\n'
                    f'  AND ({id_joined})\n'
                    f'  AND (\n'
                    f'    (logName="projects/{project_id}/logs/cloudaudit.googleapis.com%2Factivity" AND (protoPayload.methodName:"setMetadata" OR protoPayload.methodName:"setInstanceAttributes" OR protoPayload.methodName:"setCommonInstanceMetadata" OR protoPayload.methodName:"oslogin"))\n'
                    f'    OR protoPayload.methodName:"oslogin"\n'
                    f'    OR jsonPayload.event_subtype="compute.instances.osLogin"\n'
                    f'  )\n'
                    f')'
                )

            combined_filters = " OR\n  ".join(filter_parts)
            batch_filter = (
                f'(\n  {combined_filters}\n)\n'
                f'AND NOT protoPayload.authenticationInfo.principalEmail="vm-stopper-sa@{project_id}.iam.gserviceaccount.com"\n'
                f'AND NOT protoPayload.authenticationInfo.principalEmail="vm-stopper-sched@{project_id}.iam.gserviceaccount.com"\n'
                f'AND timestamp >= "{cutoff_iso}"'
            )

            id_to_key = {iid: (z, n) for z, n, iid in batch_meta if iid}
            name_to_key = {n: (z, n) for z, n, _ in batch_meta if n}

            active_in_batch: Set[Tuple[str, str]] = set()

            try:
                max_results_for_batch = 1000
                entries = self._execute_logging_query_with_retry(
                    project_id=project_id,
                    log_filter=batch_filter,
                    page_size=min(max_results_for_batch, 1000),
                    max_results=max_results_for_batch,
                    target_description=f"batch of {len(batch)} candidate instances",
                )

                for entry in entries:
                    matched_key = None
                    resource = getattr(entry, "resource", None)
                    labels = getattr(resource, "labels", None) if resource else None
                    if isinstance(labels, dict) and "instance_id" in labels:
                        matched_key = id_to_key.get(str(labels["instance_id"]))
                    elif hasattr(labels, "get") and labels.get("instance_id"):
                        matched_key = id_to_key.get(str(labels.get("instance_id")))

                    if not matched_key:
                        payload = getattr(entry, "payload", None) or getattr(entry, "proto_payload", None) or {}
                        res_name = ""
                        if isinstance(payload, dict):
                            res_name = payload.get("resourceName") or payload.get("resource_name") or ""
                        else:
                            res_name = getattr(payload, "resource_name", None) or getattr(payload, "resourceName", "") or ""
                        if res_name and "instances/" in res_name:
                            parsed_name = res_name.split("instances/")[-1].split("/")[0]
                            matched_key = name_to_key.get(parsed_name)

                    if matched_key:
                        active_in_batch.add(matched_key)
                        if len(active_in_batch) == len(batch_meta):
                            break

                for key in active_in_batch:
                    results[key] = True

                if len(entries) < max_results_for_batch:
                    for z, n, _ in batch_meta:
                        key = (z, n)
                        if key not in results:
                            results[key] = False
                else:
                    for z, n, iid in batch_meta:
                        key = (z, n)
                        if key not in results:
                            results[key] = self.has_recent_activity(
                                project_id=project_id,
                                zone=z,
                                instance_name=n,
                                instance_id=iid,
                                since_timestamp=since_timestamp,
                            )
            except Exception as batch_exc:
                logger.warning(
                    "Batch Cloud Logging query failed for %d instances: %s. Falling back to individual evaluations.",
                    len(batch),
                    batch_exc,
                )
                for z, n, iid in batch_meta:
                    key = (z, n)
                    if key not in results:
                        results[key] = self.has_recent_activity(
                            project_id=project_id,
                            zone=z,
                            instance_name=n,
                            instance_id=iid,
                            since_timestamp=since_timestamp,
                        )

        return results

    def stop_instance(self, project_id: str, zone: str, instance_name: str) -> None:
        """Issue an instance stop API call and wait for operation completion."""
        logger.info("Executing STOP on instance '%s' in zone '%s' (project: %s)...", instance_name, zone, project_id)
        operation = self.instances_client.stop(
            project=project_id,
            zone=zone,
            instance=instance_name,
        )
        if hasattr(operation, "result") and callable(operation.result):
            operation.result(timeout=300)
        logger.info("Successfully stopped instance '%s' in zone '%s'.", instance_name, zone)

    def delete_instance(self, project_id: str, zone: str, instance_name: str) -> None:
        """Issue an instance delete API call and wait for operation completion."""
        logger.info("Executing DELETE on instance '%s' in zone '%s' (project: %s)...", instance_name, zone, project_id)
        operation = self.instances_client.delete(
            project=project_id,
            zone=zone,
            instance=instance_name,
        )
        if hasattr(operation, "result") and callable(operation.result):
            operation.result(timeout=300)
        logger.info("Successfully deleted instance '%s' in zone '%s'.", instance_name, zone)
