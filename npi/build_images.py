#!/usr/bin/env python3
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

import argparse
import os
import subprocess
import sys
import tempfile
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

def terminate_process(process, name):
    print(f"Terminating local subprocess for [{name}]...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"Subprocess for [{name}] did not terminate in time. Killing it forcefully...")
        process.kill()
        process.wait()

def run_build(cmd, name, active_builds, active_processes, builds_lock):
    print(f"[{name}] Starting build...")
    logs_url = None
    output_lines = []
    
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
            with builds_lock:
                active_processes[name] = process
            try:
                for line in iter(process.stdout.readline, ''):
                    output_lines.append(line)
                    
                    # Stream log line to console in real-time
                    print(f"[{name}] {line}", end='', flush=True)
                    
                    # Parse build ID and location/region
                    if "/builds/" in line:
                        match = re.search(r'(?:locations/([^/]+)/)?builds/([a-f0-9\-]+)', line)
                        if match:
                            region = match.group(1) or "global"
                            build_id = match.group(2)
                            with builds_lock:
                                active_builds[name] = {"id": build_id, "region": region}
                                
                    # Search for logs URL
                    if "Logs are available at" in line:
                        match = re.search(r'\[\s*(https://[^\s\]]+)\s*\]', line)
                        if match:
                            logs_url = match.group(1)
                            print(f"\n[{name}] Detected Build log URL: {logs_url}\n", flush=True)
            except BaseException:
                terminate_process(process, name)
                raise
            
            return_code = process.wait()
    except Exception as e:
        return 1, f"Local wrapper error: {str(e)}"
    finally:
        with builds_lock:
            active_processes.pop(name, None)
        
    return return_code, "".join(output_lines)

def main():
    parser = argparse.ArgumentParser(description="Orchestrate building NPI Docker images.")
    parser.add_argument("--gcsfuse-version", default="v3.9.0", help="GCSFuse version to build")
    parser.add_argument("--go-version", default="1.26.4", help="Go version to use")
    parser.add_argument("--ubuntu-version", default="24.04", help="Ubuntu version to use")
    parser.add_argument("--registry", default="us-docker.pkg.dev", help="Docker registry")
    parser.add_argument("--project", default="gcs-fuse-test", help="GCP Project ID")
    parser.add_argument("--image-version", default="latest", help="Image version tag")
    parser.add_argument("--arm-worker-pool", default=None, help="Cloud Build ARM worker pool resource name")

    args = parser.parse_args()

    if args.arm_worker_pool:
        print(f"Worker pool specified: {args.arm_worker_pool}")
        print("Building AMD and ARM images separately and merging them...")
        
        # Read the original cloudbuild.yaml
        with open("cloudbuild.yaml", "r") as f:
            yaml_content = f.read()

        # Remove the entire options block for the ARM build (runs on worker pool)
        # since regional worker pools do not support machineType and leaving an empty options:
        # block can cause YAML parsing errors.
        arm_yaml_content = re.sub(
            r'(^\s*options:\s*\n(?:\s+.*(?:\n|$))*)',
            '',
            yaml_content,
            flags=re.MULTILINE
        )

        # Create temporary YAML file for the ARM build in the system temporary directory
        # to avoid polluting the workspace and uploading unnecessary files to GCS context.
        # Uses NamedTemporaryFile for safe file descriptor management.
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix=".yaml", delete=False)
        temp_path = temp_file.name
        try:
            with temp_file:
                temp_file.write(arm_yaml_content)

            # AMD Build command (uses default pool, needs E2_HIGHCPU_32, uses original cloudbuild.yaml)
            amd_substitutions = (
                f"^;^_GCSFUSE_VERSION={args.gcsfuse_version};"
                f"_GO_VERSION={args.go_version};"
                f"_UBUNTU_VERSION={args.ubuntu_version};"
                f"_REGISTRY={args.registry};"
                f"_PROJECT={args.project};"
                f"_IMAGE_VERSION={args.image_version};"
                f"_PLATFORM=linux/amd64;"
                f"_ARCH_SUFFIX=-amd64"
            )
            amd_cmd = [
                "gcloud", "builds", "submit",
                "--project", args.project,
                "--config", "cloudbuild.yaml",
                "--substitutions", amd_substitutions,
                "."
            ]

            # ARM Build command (uses private pool, uses modified cloudbuild without machineType)
            arm_substitutions = (
                f"^;^_GCSFUSE_VERSION={args.gcsfuse_version};"
                f"_GO_VERSION={args.go_version};"
                f"_UBUNTU_VERSION={args.ubuntu_version};"
                f"_REGISTRY={args.registry};"
                f"_PROJECT={args.project};"
                f"_IMAGE_VERSION={args.image_version};"
                f"_PLATFORM=linux/arm64;"
                f"_ARCH_SUFFIX=-arm64"
            )
            arm_cmd = [
                "gcloud", "builds", "submit",
                "--project", args.project,
                "--config", temp_path,
                "--worker-pool", args.arm_worker_pool,
                "--substitutions", arm_substitutions,
                "."
            ]

            # Extract region from worker pool path if present
            # e.g., projects/gcs-fuse-test/locations/us-central1/workerPools/kislayk-privatepool
            region_match = re.search(r'locations/([^/]+)/workerPools', args.arm_worker_pool)
            if region_match:
                region = region_match.group(1)
                arm_cmd.extend(["--region", region])

            active_builds = {}
            active_processes = {}
            builds_lock = threading.Lock()
            # Run both AMD and ARM builds in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(run_build, amd_cmd, "AMD", active_builds, active_processes, builds_lock): "AMD",
                    executor.submit(run_build, arm_cmd, "ARM", active_builds, active_processes, builds_lock): "ARM"
                }
                
                for future in as_completed(futures):
                    build_name = futures[future]
                    return_code, stdout = future.result()
                    if return_code != 0:
                        print(f"\n[{build_name}] Build failed with exit code {return_code}\n")
                        
                        # Immediately terminate all other local subprocesses
                        with builds_lock:
                            procs_to_terminate = list(active_processes.items())
                        for name, proc in procs_to_terminate:
                            if name != build_name:
                                terminate_process(proc, name)
                                
                        # Immediately try to cancel the other remote builds to save resources
                        with builds_lock:
                            builds_to_cancel = list(active_builds.items())
                        for name, build_info in builds_to_cancel:
                            if name != build_name:
                                print(f"Cancelling remote active build [{name}] ({build_info['id']})...")
                                cancel_cmd = ["gcloud", "builds", "cancel", build_info["id"], "--project", args.project]
                                if build_info["region"] != "global":
                                    cancel_cmd.extend(["--region", build_info["region"]])
                                subprocess.run(cancel_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        sys.exit(1)

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        print("AMD and ARM builds completed successfully. Starting merge...")
        merge_substitutions = (
            f"^;^_REGISTRY={args.registry};"
            f"_PROJECT={args.project};"
            f"_IMAGE_VERSION={args.image_version}"
        )
        merge_cmd = [
            "gcloud", "builds", "submit",
            "--project", args.project,
            "--config", "cloudbuild-merge.yaml",
            "--substitutions", merge_substitutions,
            "."
        ]
        
        # Run merge step synchronously and stream logs directly
        print(f"Running merge command: {' '.join(merge_cmd)}")
        merge_proc = subprocess.run(merge_cmd)
        if merge_proc.returncode != 0:
            print("--- MERGE STEP FAILED ---", file=sys.stderr)
            sys.exit(1)
        
        print("--- MULTI-ARCH BUILD SUCCESSFUL ---")
        
    else:
        print("No worker pool specified. Building multi-arch images on default pool...")
        # Single build command
        substitutions = (
            f"^;^_GCSFUSE_VERSION={args.gcsfuse_version};"
            f"_GO_VERSION={args.go_version};"
            f"_UBUNTU_VERSION={args.ubuntu_version};"
            f"_REGISTRY={args.registry};"
            f"_PROJECT={args.project};"
            f"_IMAGE_VERSION={args.image_version};"
            f"_PLATFORM=linux/amd64,linux/arm64;"
            f"_ARCH_SUFFIX="
        )
        cmd = [
            "gcloud", "builds", "submit",
            "--project", args.project,
            "--config", "cloudbuild.yaml",
            "--substitutions", substitutions,
            "."
        ]
        print(f"Running build command: {' '.join(cmd)}")
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print("--- MULTI-ARCH BUILD FAILED ---", file=sys.stderr)
            sys.exit(1)
        
        print("--- MULTI-ARCH BUILD SUCCESSFUL ---")

if __name__ == "__main__":
    main()
