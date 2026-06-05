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
from concurrent.futures import ThreadPoolExecutor

def run_build(cmd, name):
    print(f"[{name}] Starting build...")
    logs_url = None
    output_lines = []
    
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
            try:
                for line in iter(process.stdout.readline, ''):
                    output_lines.append(line)
                    # Search for logs URL
                    if "Logs are available at" in line:
                        match = re.search(r'\[\s*(https://[^\s\]]+)\s*\]', line)
                        if match:
                            logs_url = match.group(1)
                            print(f"[{name}] Build log URL: {logs_url}")
            except BaseException:
                process.terminate()
                process.wait()
                raise
            
            return_code = process.wait()
    except Exception as e:
        return 1, f"Local wrapper error: {str(e)}"
        
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

        # Comment out the machineType configuration for the ARM build (runs on worker pool)
        # so it doesn't cause conflicting machineType option errors.
        # Uses a robust regex to match any variation of whitespace and quotes.
        arm_yaml_content = re.sub(
            r'(^\s*machineType\s*:.*$)',
            r'# \1',
            yaml_content,
            flags=re.MULTILINE
        )

        # Create temporary YAML file for the ARM build in the current directory
        # (needs to be in the uploaded directory so Cloud Build can find it)
        # Uses NamedTemporaryFile for safe file descriptor management.
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix=".yaml", dir=".", delete=False)
        temp_path = temp_file.name
        try:
            with temp_file:
                temp_file.write(arm_yaml_content)
            
            temp_config_name = os.path.basename(temp_path)

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
                "--config", temp_config_name,
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

            # Run both AMD and ARM builds in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                amd_future = executor.submit(run_build, amd_cmd, "AMD")
                arm_future = executor.submit(run_build, arm_cmd, "ARM")
                
                amd_code, amd_out = amd_future.result()
                arm_code, arm_out = arm_future.result()

            if amd_code != 0:
                print("--- AMD BUILD FAILED ---", file=sys.stderr)
                print(amd_out, file=sys.stderr)
            if arm_code != 0:
                print("--- ARM BUILD FAILED ---", file=sys.stderr)
                print(arm_out, file=sys.stderr)

            if amd_code != 0 or arm_code != 0:
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
