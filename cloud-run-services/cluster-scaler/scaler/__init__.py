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

"""GKE Cluster Scaler package.

Monitors GKE clusters across projects, tracks idle state based on workload pods,
and safely scales down standard node pools to 0 when idle thresholds are exceeded.
"""

from scaler.config import ScalerConfig
from scaler.gke_client import GKEClient
from scaler.cluster_processor import ClusterProcessor, parse_idle_since
from scaler.service import ClusterScalerService

__all__ = [
    "ScalerConfig",
    "GKEClient",
    "ClusterProcessor",
    "ClusterScalerService",
    "parse_idle_since",
]
