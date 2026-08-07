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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
import threading
import urllib.request
import urllib.error
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

def run_build(cmd, name, active_builds, active_processes, builds_lock, cancellation_event):
    if cancellation_event.is_set():
        return 1, "Cancelled"
    print(f"[{name}] Starting build...")
    logs_url = None
    output_lines = []
    
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1) as process:
            with builds_lock:
                active_processes[name] = process
            try:
                for line in iter(process.stdout.readline, ''):
                    if cancellation_event.is_set():
                        terminate_process(process, name)
                        return 1, "Cancelled"
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

def resolve_latest_gcsfuse_version():
    """Resolves the latest release tag for GCSFuse from GitHub."""
    # Method 1: Check GitHub release redirect URL (fast and avoids API rate limits)
    try:
        url = "https://github.com/GoogleCloudPlatform/gcsfuse/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            final_url = response.geturl()
            if "/releases/tag/" in final_url:
                tag = final_url.split("/releases/tag/")[-1].strip()
                if tag and re.match(r"^[a-zA-Z0-9/._-]+$", tag):
                    print(f"Resolved latest GCSFuse release tag from redirect: {tag}")
                    return tag
    except Exception as e:
        print(f"Warning: Failed to resolve latest release from redirect ({e}). Trying GitHub API...")

    # Method 2: GitHub API
    try:
        api_url = "https://api.github.com/repos/GoogleCloudPlatform/gcsfuse/releases/latest"
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/vnd.github.v3+json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            tag = data.get("tag_name", "").strip()
            if tag and re.match(r"^[a-zA-Z0-9/._-]+$", tag):
                print(f"Resolved latest GCSFuse release tag from API: {tag}")
                return tag
    except Exception as e:
        print(f"Warning: Failed to resolve latest release from API: {e}")

    # Fallback default
    print("Warning: Could not dynamically resolve latest GCSFuse release. Falling back to default 'v3.11.2'.")
    return "v3.11.2"

def resolve_go_version(gcsfuse_version):
    # Sanitize the input version to prevent path traversal or URL manipulation
    if ".." in gcsfuse_version or not all(c.isalnum() or c in ".-_/" for c in gcsfuse_version):
        print("Warning: Invalid GCSFuse version format. Using default fallback Go version.")
        return None

    url = f"https://raw.githubusercontent.com/GoogleCloudPlatform/gcsfuse/{gcsfuse_version}/go.mod"
    print(f"Attempting to resolve Go version from: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8')
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("go "):
                    parts = line.split()
                    if len(parts) >= 2:
                        go_ver = parts[1]
                        # Validate the Go version format to prevent injection into build substitutions
                        if re.match(r"^\d+(\.\d+)*([a-zA-Z0-9.-]+)?$", go_ver):
                            print(f"Detected Go version {go_ver} in GCSFuse {gcsfuse_version} go.mod")
                            return go_ver
    except urllib.error.HTTPError as e:
        print(f"Warning: Failed to fetch go.mod (HTTP {e.code}). Using default fallback Go version.")
    except Exception as e:
        print(f"Warning: Error resolving Go version: {e}. Using default fallback Go version.")
    return None

def main():
    parser = argparse.ArgumentParser(description="Orchestrate building NPI Docker images.")
    parser.add_argument("--gcsfuse-version", default=None, help="GCSFuse version to build (default: resolved from latest GitHub release)")
    parser.add_argument("--go-version", default=None, help="Go version to use (default: resolved from GCSFuse go.mod, fallback to 1.26.5)")
    parser.add_argument("--ubuntu-version", default="24.04", help="Ubuntu version to use")
    parser.add_argument("--registry", default="us-docker.pkg.dev", help="Docker registry")
    parser.add_argument("--project", default="gcs-fuse-test", help="GCP Project ID")
    parser.add_argument("--image-version", default=None, help="Image version tag (default: matches GCSFuse version)")
    parser.add_argument("--arm-worker-pool", default=None, help="Cloud Build ARM worker pool resource name")
    parser.add_argument("--smoke-mode", action="store_true", help="Build container images using trimmed smoke test FIO matrices.")

    args = parser.parse_args()

    if not args.gcsfuse_version:
        args.gcsfuse_version = resolve_latest_gcsfuse_version()

    if not re.match(r"^[a-zA-Z0-9/._-]+$", args.gcsfuse_version):
        print(f"Error: Invalid GCSFuse version format: {args.gcsfuse_version}", file=sys.stderr)
        sys.exit(1)

    if not args.image_version:
        args.image_version = args.gcsfuse_version

    if not args.go_version:
        resolved_go = resolve_go_version(args.gcsfuse_version)
        if resolved_go:
            args.go_version = resolved_go
        else:
            args.go_version = "1.26.5"

    if not re.match(r"^\d+(\.\d+)*([a-zA-Z0-9.-]+)?$", args.go_version):
        print(f"Error: Invalid Go version format: {args.go_version}", file=sys.stderr)
        sys.exit(1)

    for param_name, param_val in [
        ("ubuntu-version", args.ubuntu_version),
        ("registry", args.registry),
        ("project", args.project),
        ("image-version", args.image_version)
    ]:
        if not re.match(r"^[a-zA-Z0-9/._-]+$", param_val):
            print(f"Error: Invalid parameter format for --{param_name}: {param_val}", file=sys.stderr)
            sys.exit(1)

    print(f"Target GCSFuse version: {args.gcsfuse_version}")
    print(f"Target Image version: {args.image_version}")
    print(f"Using Go version: {args.go_version} to compile GCSFuse performance test base image.")

    read_matrix_backup = None
    write_matrix_backup = None
    if args.smoke_mode:
        print("Smoke mode enabled: packaging trimmed smoke matrices into build context.")
        if os.path.exists("fio/read_matrix.csv"):
            with open("fio/read_matrix.csv", "r") as f:
                read_matrix_backup = f.read()
        if os.path.exists("fio/write_matrix.csv"):
            with open("fio/write_matrix.csv", "r") as f:
                write_matrix_backup = f.read()
        shutil.copy("fio/smoke_read_matrix.csv", "fio/read_matrix.csv")
        shutil.copy("fio/smoke_write_matrix.csv", "fio/write_matrix.csv")

    try:
        _run_builds(args)
    finally:
        if args.smoke_mode:
            if read_matrix_backup is not None:
                with open("fio/read_matrix.csv", "w") as f:
                    f.write(read_matrix_backup)
            if write_matrix_backup is not None:
                with open("fio/write_matrix.csv", "w") as f:
                    f.write(write_matrix_backup)
            print("Restored original FIO matrix files.")

def _run_builds(args):

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
            r'(^[ \t]*options:\s*\n(?:(?:[ \t]+.*|^[ \t]*)(?:\n|$))*)',
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
            # e.g., projects/gcs-fuse-test/locations/us-central1/workerPools/my-privatepool
            region_match = re.search(r'locations/([^/]+)/workerPools', args.arm_worker_pool)
            if region_match:
                region = region_match.group(1)
                arm_cmd.extend(["--region", region])

            active_builds = {}
            active_processes = {}
            builds_lock = threading.Lock()
            cancellation_event = threading.Event()

            def teardown(exclude_name=None):
                cancellation_event.set()
                # Immediately terminate all local subprocesses to unblock threads
                with builds_lock:
                    procs_to_terminate = list(active_processes.items())
                for name, proc in procs_to_terminate:
                    if name != exclude_name:
                        terminate_process(proc, name)

                # Cancel all remote active builds on GCP
                with builds_lock:
                    builds_to_cancel = list(active_builds.items())
                for name, build_info in builds_to_cancel:
                    if name != exclude_name:
                        print(f"Cancelling remote active build [{name}] ({build_info['id']})...", file=sys.stderr)
                        cancel_cmd = ["gcloud", "builds", "cancel", build_info["id"], "--project", args.project]
                        if build_info["region"] != "global":
                            cancel_cmd.extend(["--region", build_info["region"]])
                        subprocess.run(cancel_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            try:
                # Run both AMD and ARM builds in parallel, managed manually to handle Ctrl+C safely without hangs
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {
                        executor.submit(run_build, amd_cmd, "AMD", active_builds, active_processes, builds_lock, cancellation_event): "AMD",
                        executor.submit(run_build, arm_cmd, "ARM", active_builds, active_processes, builds_lock, cancellation_event): "ARM"
                    }

                    for future in as_completed(futures):
                        build_name = futures[future]
                        return_code, stdout = future.result()
                        if return_code != 0:
                            print(f"\n[{build_name}] Build failed with exit code {return_code}\n")
                            teardown(exclude_name=build_name)
                            sys.exit(1)
            except BaseException as e:
                if isinstance(e, SystemExit):
                    raise
                print(f"\nExecution interrupted: {type(e).__name__}. Initiating emergency teardown...\n", file=sys.stderr)
                teardown()
                raise

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
