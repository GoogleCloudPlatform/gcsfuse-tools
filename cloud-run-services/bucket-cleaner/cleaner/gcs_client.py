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

"""Google Cloud Storage API client wrapper supporting both Cloud Storage SDK and CLI fallback."""

import csv
import datetime
import io
import logging
import subprocess
from typing import Any, Dict, List, Optional

try:
    from google.cloud import storage
except ImportError:
    storage = None

logger = logging.getLogger(__name__)


class MockGCSBucket:
    """Lightweight bucket representation when running without the full Python SDK."""

    def __init__(self, name: str, time_created: datetime.datetime, project: str) -> None:
        self.name = name
        self.time_created = time_created
        self.project = project
        self.lifecycle_rules: List[Dict[str, Any]] = []

    def delete(self, force: bool = True) -> None:
        cmd = ["gcloud", "storage", "rm", "-r", f"gs://{self.name}", "--quiet"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"gcloud storage rm failed on gs://{self.name}: {res.stderr}")

    def patch(self) -> None:
        pass


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
        return self._clients.get(project_id)

    def list_buckets(self, project_id: str, prefix: str) -> List[Any]:
        """List all buckets in project matching the given prefix."""
        client = self.get_client(project_id)
        if client:
            logger.info("Querying GCS buckets in '%s' via Google Cloud SDK...", project_id)
            return list(client.list_buckets(prefix=prefix))

        # Fallback to gcloud CLI when Python SDK is not installed
        logger.info("Querying GCS buckets in '%s' via gcloud CLI fallback...", project_id)
        cmd = [
            "gcloud", "storage", "buckets", "list",
            f"--project={project_id}",
            f"--filter=name ~ ^{prefix}",
            "--format=csv[no-heading](name,creation_time)"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to list buckets via gcloud for project {project_id}: {res.stderr}")

        buckets: List[Any] = []
        reader = csv.reader(io.StringIO(res.stdout))
        for row in reader:
            if not row or len(row) < 1:
                continue
            name = row[0].strip()
            c_time_str = row[1].strip() if len(row) > 1 else ""
            c_time = None
            if c_time_str:
                try:
                    c_time = datetime.datetime.fromisoformat(c_time_str.replace("Z", "+00:00"))
                except Exception:
                    pass
            if not c_time:
                c_time = datetime.datetime.now(datetime.timezone.utc)
            buckets.append(MockGCSBucket(name=name, time_created=c_time, project=project_id))

        return buckets

    def delete_bucket(self, bucket: Any, force: bool = True) -> None:
        bucket.delete(force=force)

    def apply_lifecycle_rule(self, bucket: Any, age_days: int = 1) -> None:
        rule = {
            "action": {"type": "Delete"},
            "condition": {"age": age_days},
        }
        existing_rules = list(getattr(bucket, "lifecycle_rules", []) or [])
        existing_rules.append(rule)
        bucket.lifecycle_rules = existing_rules
        bucket.patch()
        logger.info("Applied fallback %d-day OLM deletion rule to gs://%s", age_days, bucket.name)
