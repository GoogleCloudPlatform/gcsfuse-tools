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

"""Dynamic configuration resolution for GCE Reservation Cleaner."""

from dataclasses import dataclass, field
import json
import logging
import os
from typing import Any, Dict, List, Optional
import google.auth

logger = logging.getLogger(__name__)


DEFAULT_EXCLUDE_LABEL_KEYS: List[str] = [
    "keep-alive",
    "keep_alive",
    "do-not-delete",
    "do_not_delete",
    "do-not-stop",
    "do_not_stop",
    "dont-delete",
    "dont-stop",
    "no-auto-delete",
    "no_auto_delete",
    "no-auto-stop",
    "no_auto_stop",
    "permanent",
    "whitelisted",
    "protected",
    "skip-lifecycle",
    "skip_lifecycle",
]


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Safely parse boolean values from strings, ints, or bools."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val_lower = value.strip().lower()
        if val_lower in ("true", "1", "t", "yes", "y"):
            return True
        if val_lower in ("false", "0", "f", "no", "n"):
            return False
    return default


def _parse_list(value: Any) -> Optional[List[str]]:
    """Parse comma-separated strings or lists into a list of strings."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",") if v.strip()]
        return items if items else None
    return None


@dataclass
class CleanerConfig:
    """Runtime configuration for Reservation Cleaner."""

    project_id: str
    delete_idle_days: float = 60.0
    delete_never_used: bool = True
    max_age_days: Optional[float] = 180.0
    lookback_days: int = 730
    dry_run: bool = False
    max_workers: int = 10
    zones: Optional[List[str]] = None
    reservation_names: Optional[List[str]] = None
    whitelist_names: Optional[List[str]] = None
    exclude_label_keys: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_LABEL_KEYS))
    exclude_label_values: Dict[str, str] = field(default_factory=dict)
    whitelist_tags: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_LABEL_KEYS))

    def __post_init__(self):
        """Validate configuration."""
        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError(
                "Missing required parameter 'project_id'. Please specify via request payload, "
                "query parameter, PROJECT_ID environment variable, or Google Cloud Application Default Credentials."
            )
        self.project_id = self.project_id.strip()

        if self.delete_idle_days < 0:
            raise ValueError(f"delete_idle_days must be non-negative, got {self.delete_idle_days}")

        if self.max_age_days is not None and self.max_age_days < 0:
            raise ValueError(f"max_age_days must be non-negative, got {self.max_age_days}")

        if self.lookback_days <= 0:
            raise ValueError(f"lookback_days must be positive, got {self.lookback_days}")

        if self.max_workers <= 0:
            raise ValueError(f"max_workers must be positive, got {self.max_workers}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CleanerConfig":
        """Construct CleanerConfig from a raw dictionary with fallbacks."""
        # 1. Resolve Project ID: data -> env vars -> ADC
        project_id = (
            data.get("project")
            or data.get("project_id")
            or data.get("projectId")
            or data.get("gcp_project")
            or os.environ.get("PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
        )

        if not project_id:
            try:
                _, adc_project = google.auth.default()
                if adc_project:
                    project_id = adc_project
            except Exception as e:
                logger.debug("Failed to acquire ADC default project: %s", e)

        # 2. Resolve delete_idle_days
        delete_idle_days_raw = data.get("delete_idle_days") or data.get("idle_days") or os.environ.get("DELETE_IDLE_DAYS")
        delete_idle_days = float(delete_idle_days_raw) if delete_idle_days_raw is not None else 60.0

        # 3. Resolve delete_never_used
        delete_never_used_raw = data.get("delete_never_used")
        if delete_never_used_raw is None:
            delete_never_used_raw = os.environ.get("DELETE_NEVER_USED")
        delete_never_used = _parse_bool(delete_never_used_raw, default=True)

        # 4. Resolve max_age_days
        max_age_days_raw = data.get("max_age_days") or os.environ.get("MAX_AGE_DAYS")
        max_age_days = float(max_age_days_raw) if max_age_days_raw is not None else 180.0

        # 5. Resolve lookback_days
        lookback_days_raw = data.get("lookback_days") or data.get("days") or os.environ.get("LOOKBACK_DAYS")
        lookback_days = int(lookback_days_raw) if lookback_days_raw is not None else 730

        # 6. Resolve dry_run
        dry_run_raw = data.get("dry_run")
        if dry_run_raw is None:
            dry_run_raw = os.environ.get("DRY_RUN")
        dry_run = _parse_bool(dry_run_raw, default=False)

        # 7. Resolve max_workers
        max_workers_raw = data.get("max_workers") or os.environ.get("MAX_WORKERS")
        max_workers = int(max_workers_raw) if max_workers_raw is not None else 10

        # 8. Resolve zones and reservation_names filters
        zones = _parse_list(data.get("zones") or data.get("zone") or os.environ.get("ZONES"))
        reservation_names = _parse_list(
            data.get("reservation_names") or data.get("reservations") or os.environ.get("RESERVATION_NAMES")
        )
        whitelist_names = _parse_list(
            data.get("whitelist_names") or data.get("whitelist_reservations") or os.environ.get("WHITELIST_NAMES")
        )

        # 9. Resolve protection labels and tags
        excl_keys = _parse_list(
            data.get("exclude_label_keys")
            or data.get("exclude_labels")
            or data.get("whitelist_labels")
            or os.environ.get("EXCLUDE_LABEL_KEYS")
        )
        exclude_label_keys = excl_keys if excl_keys is not None else list(DEFAULT_EXCLUDE_LABEL_KEYS)

        whitelist_tags_input = _parse_list(
            data.get("whitelist_tags")
            or data.get("tags")
            or os.environ.get("WHITELIST_TAGS")
        )
        whitelist_tags = whitelist_tags_input if whitelist_tags_input is not None else list(DEFAULT_EXCLUDE_LABEL_KEYS)

        exclude_label_values = data.get("exclude_label_values") or {}
        if not isinstance(exclude_label_values, dict):
            exclude_label_values = {}

        return cls(
            project_id=str(project_id) if project_id else "",
            delete_idle_days=delete_idle_days,
            delete_never_used=delete_never_used,
            max_age_days=max_age_days,
            lookback_days=lookback_days,
            dry_run=dry_run,
            max_workers=max_workers,
            zones=zones,
            reservation_names=reservation_names,
            whitelist_names=whitelist_names,
            exclude_label_keys=exclude_label_keys,
            exclude_label_values=exclude_label_values,
            whitelist_tags=whitelist_tags,
        )

    @classmethod
    def from_request(cls, request: Any) -> "CleanerConfig":
        """Construct CleanerConfig from a Flask or Functions Framework request."""
        merged_data: Dict[str, Any] = {}

        # 1. Query parameters
        if hasattr(request, "args") and request.args:
            merged_data.update(dict(request.args))

        # 2. JSON request body (higher precedence than query args)
        if request is not None:
            try:
                if hasattr(request, "is_json") and request.is_json:
                    json_payload = request.get_json(silent=True)
                    if isinstance(json_payload, dict):
                        merged_data.update(json_payload)
                elif hasattr(request, "get_json"):
                    json_payload = request.get_json(silent=True)
                    if isinstance(json_payload, dict):
                        merged_data.update(json_payload)
            except Exception as e:
                logger.debug("Could not parse request JSON payload: %s", e)

        return cls.from_dict(merged_data)
