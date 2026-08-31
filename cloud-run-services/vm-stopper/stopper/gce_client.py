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
from typing import Any, List, Optional, Tuple

import google.auth
from google.cloud import compute_v1
from google.cloud import logging_v2

logger = logging.getLogger(__name__)


class GCEClient:
    """Client wrapper for Google Compute Engine and Cloud Logging APIs."""

    def __init__(self, credentials: Optional[google.auth.credentials.Credentials] = None):
        self.credentials = credentials
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
            f'AND timestamp >= "{cutoff_iso}"'
        )

        try:
            client = self.get_logging_client(project_id)
            entries = client.list_entries(
                filter_=log_filter,
                page_size=1,
                max_results=1,
            )
            # Iterate generator to evaluate presence of log entries
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
