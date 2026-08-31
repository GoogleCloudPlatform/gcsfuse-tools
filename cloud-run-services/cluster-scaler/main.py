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

"""HTTP routing and Functions Framework entrypoint for GKE Cluster Scaler."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cluster-scaler")

try:
    import functions_framework
except ImportError:
    # Fallback decorator when functions_framework is not present in local testing
    class _MockFunctionsFramework:
        @staticmethod
        def http(func: Any) -> Any:
            return func

    functions_framework = _MockFunctionsFramework()

from flask import Flask, jsonify, request as flask_request

from scaler.config import ScalerConfig
from scaler.service import ClusterScalerService

app = Flask(__name__)


@functions_framework.http
def check_and_scale_idle_gke(request: Any) -> Tuple[Any, int]:
    """Cloud Functions Gen 2 & HTTP entrypoint for GKE idle cluster sweep."""
    logger.info("Received GKE idle cluster check invocation")
    try:
        config = ScalerConfig.from_request(request)
    except Exception as e:
        logger.error("Configuration validation error: %s", e)
        return jsonify({
            "status": "error",
            "service": "cluster-scaler",
            "message": f"Invalid configuration: {e}",
            "errors": [str(e)],
        }), 400

    service = ClusterScalerService(config=config)
    result = service.run()

    status_code = 200
    if result.get("status") == "error":
        status_code = 500

    return jsonify(result), status_code


# Aliases for compatibility
main_handler = check_and_scale_idle_gke
scale_clusters = check_and_scale_idle_gke


@app.route("/", methods=["GET", "POST"])
def index() -> Tuple[Any, int]:
    """Cloud Run WSGI HTTP route."""
    return check_and_scale_idle_gke(flask_request)


@app.route("/healthz", methods=["GET"])
def healthz() -> Tuple[Any, int]:
    """Health check endpoint for container orchestrators."""
    return jsonify({
        "status": "ok",
        "service": "cluster-scaler",
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting cluster-scaler WSGI application on port %d", port)
    app.run(host="0.0.0.0", port=port)
