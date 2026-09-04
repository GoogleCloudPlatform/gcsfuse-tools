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
    def from_request(
        cls,
        request: Any = None,
        request_data: Optional[Dict[str, Any]] = None,
        query_args: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "CleanerConfig":
        """Resolve configuration hierarchically from JSON body, query params, env, and ADC."""
        req = dict(request_data) if request_data else {}
        args = dict(query_args) if query_args else {}

        if request is not None:
            if hasattr(request, "get_json"):
                try:
                    json_payload = request.get_json(silent=True)
                    if isinstance(json_payload, dict):
                        req = {**json_payload, **req}
                except Exception:
                    pass
            elif isinstance(request, dict):
                req = {**request, **req}

            if hasattr(request, "args") and request.args:
                try:
                    args = {**dict(request.args), **args}
                except Exception:
                    pass

        environ = env if env is not None else os.environ

        def _get_val(key_names: List[str], env_names: Optional[List[str]] = None) -> Any:
            for k in key_names:
                if k in req and req[k] is not None:
                    return req[k]
            for k in key_names:
                if k in args and args[k] is not None:
                    return args[k]
            if env_names:
                for ek in env_names:
                    if ek in environ and environ[ek] is not None and str(environ[ek]).strip():
                        return environ[ek]
            return None

        # 1. Resolve Project ID
        raw_project = _get_val(
            ["project", "project_id", "projectId", "gcp_project"],
            ["PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "GCLOUD_PROJECT"],
        )
        project_id = str(raw_project).strip() if raw_project else ""

        if not project_id:
            try:
                _, adc_project = google.auth.default()
                if adc_project:
                    project_id = adc_project
            except Exception as e:
                logger.debug("Failed to acquire ADC default project: %s", e)

        # 2. Resolve delete_idle_days
        raw_idle_days = _get_val(
            ["delete_idle_days", "idle_days", "idleDaysThreshold"],
            ["DELETE_IDLE_DAYS", "IDLE_DAYS"],
        )
        delete_idle_days = float(raw_idle_days) if raw_idle_days is not None else 60.0

        # 3. Resolve delete_never_used
        raw_never_used = _get_val(
            ["delete_never_used", "deleteNeverUsed"],
            ["DELETE_NEVER_USED"],
        )
        delete_never_used = _parse_bool(raw_never_used, default=True)

        # 4. Resolve max_age_days
        raw_max_age = _get_val(
            ["max_age_days", "maxAgeDays"],
            ["MAX_AGE_DAYS"],
        )
        max_age_days = float(raw_max_age) if raw_max_age is not None else 180.0

        # 5. Resolve lookback_days
        raw_lookback = _get_val(
            ["lookback_days", "days", "lookbackDays"],
            ["LOOKBACK_DAYS"],
        )
        lookback_days = int(raw_lookback) if raw_lookback is not None else 730

        # 6. Resolve dry_run
        raw_dry_run = _get_val(
            ["dry_run", "dryRun"],
            ["DRY_RUN"],
        )
        dry_run = _parse_bool(raw_dry_run, default=False)

        # 7. Resolve max_workers
        raw_max_workers = _get_val(
            ["max_workers", "maxWorkers"],
            ["MAX_WORKERS"],
        )
        max_workers = int(raw_max_workers) if raw_max_workers is not None else 10

        # 8. Resolve zones and reservation_names filters
        raw_zones = _get_val(
            ["zones", "zone"],
            ["ZONES"],
        )
        zones = _parse_list(raw_zones)

        raw_res_names = _get_val(
            ["reservation_names", "reservations", "reservationNames"],
            ["RESERVATION_NAMES"],
        )
        reservation_names = _parse_list(raw_res_names)

        raw_whitelist_names = _get_val(
            ["whitelist_names", "whitelist_reservations", "whitelistNames"],
            ["WHITELIST_NAMES"],
        )
        whitelist_names = _parse_list(raw_whitelist_names)

        # 9. Resolve protection labels and tags
        raw_excl_keys = _get_val(
            ["exclude_label_keys", "exclude_labels", "whitelist_labels", "excludeLabelKeys"],
            ["EXCLUDE_LABEL_KEYS"],
        )
        excl_keys = _parse_list(raw_excl_keys)
        exclude_label_keys = excl_keys if excl_keys is not None else list(DEFAULT_EXCLUDE_LABEL_KEYS)

        raw_whitelist_tags = _get_val(
            ["whitelist_tags", "tags", "whitelistTags"],
            ["WHITELIST_TAGS"],
        )
        whitelist_tags_input = _parse_list(raw_whitelist_tags)
        whitelist_tags = whitelist_tags_input if whitelist_tags_input is not None else list(DEFAULT_EXCLUDE_LABEL_KEYS)

        raw_exclude_values = _get_val(
            ["exclude_label_values", "excludeLabelValues"],
            ["EXCLUDE_LABEL_VALUES"],
        )
        exclude_label_values = raw_exclude_values if isinstance(raw_exclude_values, dict) else {}

        return cls(
            project_id=str(project_id).strip() if project_id else "",
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
    def from_dict(cls, data: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> "CleanerConfig":
        """Construct CleanerConfig from a raw dictionary with fallbacks."""
        return cls.from_request(request_data=data, env=env)

