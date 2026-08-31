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

"""VM Stopper: Automated Cloud Run / Functions Gen 2 Service for Stopping Idle VMs."""

import logging
import os
import sys
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request as flask_request
import functions_framework

from stopper.service import process_request

# Configure standard ISO-8601 logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("vm-stopper")

app = Flask(__name__)


def _extract_request_data(req: Any) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Extract JSON payload and query arguments from request object."""
    json_data = None
    args_data = None

    if req is not None:
        # Extract query parameters
        if hasattr(req, "args") and req.args:
            try:
                args_data = req.args.to_dict()
            except Exception:
                args_data = dict(req.args)

        # Extract JSON body
        if hasattr(req, "get_json"):
            try:
                json_data = req.get_json(silent=True)
            except Exception:
                pass

        # Fallback to form data or raw data
        if not json_data and hasattr(req, "form") and req.form:
            try:
                json_data = req.form.to_dict()
            except Exception:
                pass

    return json_data, args_data


@functions_framework.http
def check_and_stop_idle_vms(request: Any) -> Any:
    """HTTP entrypoint for Google Cloud Functions Gen 2 & Functions Framework."""
    logger.info("Received VM Stopper invocation request.")
    json_data, args_data = _extract_request_data(request)
    result, status_code = process_request(request_data=json_data, query_args=args_data)
    return jsonify(result), status_code


# Backward compatibility alias
vm_stopper_handler = check_and_stop_idle_vms


@app.route("/", methods=["GET", "POST"])
def index() -> Any:
    """HTTP routing for Cloud Run container hosting."""
    return check_and_stop_idle_vms(flask_request)


@app.route("/healthz", methods=["GET"])
def healthz() -> Any:
    """Health check endpoint for container orchestrators."""
    return jsonify({"status": "healthy", "service": "vm-stopper"}), 200


if __name__ == "__main__":
    server_port = int(os.environ.get("PORT", 8080))
    logger.info("Starting VM Stopper Flask server on port %d...", server_port)
    app.run(host="0.0.0.0", port=server_port)
