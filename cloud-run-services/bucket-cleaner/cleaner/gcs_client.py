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

"""Google Cloud Storage API client wrapper supporting multi-project operations."""

import logging
from typing import Any, Dict, List, Optional, Union

try:
    from google.cloud import storage
except ImportError:
    storage = None

logger = logging.getLogger(__name__)


class GCSClient:
    """Wrapper around Google Cloud Storage client supporting multi-project operations."""

    def __init__(self, client_factory: Optional[Any] = None) -> None:
        self._client_factory = client_factory or (
            lambda project_id: storage.Client(project=project_id) if storage else None
        )
        self._clients: Dict[str, Any] = {}

    def get_client(self, project_id: str) -> Any:
        if project_id not in self._clients:
            if self._client_factory:
                self._clients[project_id] = self._client_factory(project_id)
            if not self._clients.get(project_id):
                raise RuntimeError(
                    f"Google Cloud Storage client could not be initialized for project '{project_id}'. "
                    "Ensure 'google-cloud-storage' is installed and GCP credentials are configured."
                )
        return self._clients[project_id]

    def list_buckets(self, project_id: str, prefix: str) -> List[Any]:
        """List all buckets in project matching the given prefix."""
        client = self.get_client(project_id)
        logger.info("Querying GCS buckets in '%s' matching prefix '%s'...", project_id, prefix)
        return list(client.list_buckets(prefix=prefix))

    def delete_bucket(self, bucket: Any, force: bool = True) -> None:
        """Delete a bucket directly, optionally forcing deletion of remaining objects."""
        bucket.delete(force=force)

    def apply_lifecycle_rule(self, bucket: Any, age_days: Union[int, float] = 1) -> None:
        """Apply an Object Lifecycle Management (OLM) rule to auto-delete objects and bucket."""
        days_int = max(1, int(round(age_days)))
        rule = {
            "action": {"type": "Delete"},
            "condition": {"age": days_int},
        }
        existing_rules = list(getattr(bucket, "lifecycle_rules", []) or [])
        existing_rules.append(rule)
        bucket.lifecycle_rules = existing_rules
        bucket.patch()
        logger.info("Applied fallback %d-day OLM deletion rule to gs://%s", days_int, bucket.name)

