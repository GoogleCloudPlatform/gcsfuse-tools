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

"""REST and SDK client for GCE Compute Reservations and Cloud Monitoring API."""

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib3
import google.auth
import google.auth.transport.requests

logger = logging.getLogger(__name__)

COMPUTE_API_BASE = "https://compute.googleapis.com/compute/v1"
MONITORING_API_BASE = "https://monitoring.googleapis.com/v3"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class ReservationClient:
    """Client for querying GCE Compute Reservations and Cloud Monitoring metrics."""

    def __init__(
        self,
        credentials: Optional[google.auth.credentials.Credentials] = None,
        http_pool: Optional[urllib3.PoolManager] = None,
    ):
        self._http = http_pool or urllib3.PoolManager()
        if credentials:
            self._credentials = credentials
        else:
            try:
                self._credentials, _ = google.auth.default(scopes=DEFAULT_SCOPES)
            except Exception as e:
                logger.warning("Could not load default Google Cloud credentials: %s", e)
                self._credentials = None

    def _get_auth_headers(self) -> Dict[str, str]:
        """Obtain valid authorization headers with OAuth 2.0 Bearer token."""
        if not self._credentials:
            return {"Content-Type": "application/json"}

        try:
            if not self._credentials.valid:
                auth_req = google.auth.transport.requests.Request()
                self._credentials.refresh(auth_req)
            token = self._credentials.token
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        except Exception as e:
            logger.error("Failed to refresh Google auth token: %s", e)
            raise RuntimeError(f"Authentication failure: {e}") from e

    def list_aggregated_reservations(self, project_id: str) -> List[Dict[str, Any]]:
        """List all GCE compute reservations across all zones in the specified project."""
        reservations: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            headers = self._get_auth_headers()
            url = f"{COMPUTE_API_BASE}/projects/{project_id}/aggregated/reservations"
            query_params = {}
            if page_token:
                query_params["pageToken"] = page_token

            if query_params:
                url = f"{url}?{urllib.parse.urlencode(query_params)}"

            logger.debug("Fetching aggregated reservations: %s", url)
            response = self._http.request("GET", url, headers=headers, timeout=30.0)

            if response.status != 200:
                error_msg = f"Failed to list aggregated reservations (HTTP {response.status}): {response.data.decode('utf-8')}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            data = json.loads(response.data.decode("utf-8"))
            items = data.get("items", {})

            for zone_key, zone_val in items.items():
                zone_reservations = zone_val.get("reservations", [])
                for res in zone_reservations:
                    # Normalize zone name
                    zone_url = res.get("zone", zone_key)
                    zone_name = zone_url.split("/")[-1] if "/" in zone_url else zone_url
                    res["zone"] = zone_name
                    reservations.append(res)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        logger.info("Discovered %d reservations in project '%s'", len(reservations), project_id)
        return reservations

    def query_reservation_usage(
        self,
        project_id: str,
        reservation_id: str,
        lookback_days: int = 730,
        reference_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Query Cloud Monitoring time-series metric compute.googleapis.com/reservation/used.

        Returns metadata regarding active hours, last used timestamp, and lifetime activity.
        """
        now = reference_time or datetime.now(timezone.utc)
        start_time = now - timedelta(days=lookback_days)

        start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        filter_expr = (
            f'metric.type = "compute.googleapis.com/reservation/used" AND '
            f'resource.labels.reservation_id = "{reservation_id}"'
        )

        params = {
            "filter": filter_expr,
            "interval.startTime": start_iso,
            "interval.endTime": end_iso,
            "aggregation.alignmentPeriod": "3600s",
            "aggregation.perSeriesAligner": "ALIGN_MAX",
        }

        url = f"{MONITORING_API_BASE}/projects/{project_id}/timeSeries?{urllib.parse.urlencode(params)}"
        headers = self._get_auth_headers()

        logger.debug("Querying Monitoring usage for reservation_id %s: %s", reservation_id, url)
        response = self._http.request("GET", url, headers=headers, timeout=30.0)

        if response.status != 200:
            error_msg = f"Failed to query monitoring metrics (HTTP {response.status}): {response.data.decode('utf-8')}"
            logger.error(error_msg)
            return {
                "is_never_used": False,
                "last_used_timestamp": None,
                "first_used_timestamp": None,
                "total_active_hours": 0,
                "max_usage_count": 0,
                "error": error_msg,
            }

        data = json.loads(response.data.decode("utf-8"))
        time_series = data.get("timeSeries", [])

        active_points: List[Dict[str, Any]] = []

        for series in time_series:
            points = series.get("points", [])
            for point in points:
                val_obj = point.get("value", {})
                int_val = int(val_obj.get("int64Value", 0)) if "int64Value" in val_obj else 0
                double_val = float(val_obj.get("doubleValue", 0.0)) if "doubleValue" in val_obj else 0.0
                usage_val = int_val or int(double_val)

                if usage_val > 0:
                    end_time_str = point.get("interval", {}).get("endTime")
                    start_time_str = point.get("interval", {}).get("startTime")
                    active_points.append(
                        {
                            "time": end_time_str or start_time_str,
                            "usage": usage_val,
                        }
                    )

        if not active_points:
            return {
                "is_never_used": True,
                "last_used_timestamp": None,
                "first_used_timestamp": None,
                "total_active_hours": 0,
                "max_usage_count": 0,
                "error": None,
            }

        # Sort points chronologically
        active_points.sort(key=lambda p: p["time"])
        first_used = active_points[0]["time"]
        last_used = active_points[-1]["time"]
        max_usage = max(p["usage"] for p in active_points)
        total_active_hours = len(active_points)

        return {
            "is_never_used": False,
            "last_used_timestamp": last_used,
            "first_used_timestamp": first_used,
            "total_active_hours": total_active_hours,
            "max_usage_count": max_usage,
            "error": None,
        }

    def delete_reservation(self, project_id: str, zone: str, reservation_name: str) -> bool:
        """Delete a compute reservation in a specific zone."""
        headers = self._get_auth_headers()
        url = f"{COMPUTE_API_BASE}/projects/{project_id}/zones/{zone}/reservations/{reservation_name}"

        logger.info("Executing DELETE on reservation: %s", url)
        response = self._http.request("DELETE", url, headers=headers, timeout=30.0)

        if response.status in (200, 202, 204):
            logger.info("Successfully initiated/completed deletion of reservation '%s'", reservation_name)
            return True

        error_msg = f"Failed to delete reservation '{reservation_name}' in zone '{zone}' (HTTP {response.status}): {response.data.decode('utf-8')}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
