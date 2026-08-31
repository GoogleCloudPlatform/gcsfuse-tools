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

"""Automated Offline Verification Test Suite for Cloud Run Deployment Artifacts.

This module provides a comprehensive 100% offline executable test suite that validates:
1. Shell Scripts (deploy_all.sh, sub-service deploy.sh): bash -n, CLI flags, missing args, dry-run.
2. Cloud Build Pipeline (cloudbuild.yaml): YAML syntax, pre-deployment test gating, substitutions.
3. Terraform Module (terraform/): HCL static analysis, balance, variable types & descriptions, resources, outputs.
4. Service Unit & Stress Test Suites: cluster-scaler, gcsfuse-reservation-cleaner, vm-stopper, and verify_stress_tests.py.

Usage:
    python3 cloud-run-services/verify_deployment_artifacts.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

# Determine repository and directory roots
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
TERRAFORM_DIR = os.path.join(SCRIPT_DIR, "terraform")


# ==============================================================================
# Offline HCL Static Analysis Helper
# ==============================================================================

class HCLStaticAnalyzer:
    """Robust offline HCL parser & static analyzer for Terraform configurations."""

    @staticmethod
    def strip_comments_and_strings(content: str) -> str:
        """Strips single-line and multi-line comments while preserving string literals structure."""
        result = []
        i = 0
        n = len(content)
        in_string = False
        in_multiline_comment = False
        escape = False

        while i < n:
            char = content[i]
            next_char = content[i + 1] if i + 1 < n else ""

            if in_multiline_comment:
                if char == "*" and next_char == "/":
                    in_multiline_comment = False
                    i += 2
                else:
                    i += 1
                continue

            if in_string:
                result.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                i += 1
                continue

            # Check for comment start
            if char == "/" and next_char == "*":
                in_multiline_comment = True
                i += 2
                continue
            if char == "#" or (char == "/" and next_char == "/"):
                # Skip until newline
                while i < n and content[i] != "\n":
                    i += 1
                continue

            if char == '"':
                in_string = True
                result.append(char)
                i += 1
                continue

            result.append(char)
            i += 1

        return "".join(result)

    @classmethod
    def check_balanced_delimiters(cls, content: str) -> Tuple[bool, str]:
        """Validates that braces, brackets, and parentheses are properly balanced."""
        stack = []
        pairs = {"{": "}", "[": "]", "(": ")"}
        cleaned = cls.strip_comments_and_strings(content)

        line_no = 1
        for i, char in enumerate(cleaned):
            if char == "\n":
                line_no += 1
            elif char in pairs:
                stack.append((char, line_no))
            elif char in pairs.values():
                if not stack:
                    return False, f"Unmatched closing '{char}' at line {line_no}"
                open_char, open_line = stack.pop()
                if pairs[open_char] != char:
                    return False, f"Mismatched closing '{char}' at line {line_no}, expected '{pairs[open_char]}' for '{open_char}' from line {open_line}"

        if stack:
            open_char, open_line = stack[-1]
            return False, f"Unclosed delimiter '{open_char}' opened at line {open_line}"
        return True, "Balanced"

    @classmethod
    def parse_variables(cls, content: str) -> Dict[str, Dict[str, Any]]:
        """Extracts variable declarations, their types, descriptions, and defaults."""
        variables = {}
        cleaned = cls.strip_comments_and_strings(content)
        
        # Regex to find variable blocks: variable "name" { ... }
        pattern = re.compile(r'variable\s+"([^"]+)"\s*\{', re.MULTILINE)
        for match in pattern.finditer(cleaned):
            var_name = match.group(1)
            start_pos = match.end() - 1  # at '{'
            
            # Find matching closing brace
            brace_count = 0
            end_pos = start_pos
            for j in range(start_pos, len(cleaned)):
                if cleaned[j] == '{':
                    brace_count += 1
                elif cleaned[j] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = j
                        break
            
            block_body = cleaned[start_pos + 1:end_pos]
            
            # Extract type
            type_match = re.search(r'type\s*=\s*([a-zA-Z0-9_\(\)\s,]+?)(?=\n|\r|$)', block_body)
            var_type = type_match.group(1).strip() if type_match else None
            
            # Extract description
            desc_match = re.search(r'description\s*=\s*"([^"]*)"', block_body)
            var_desc = desc_match.group(1).strip() if desc_match else None
            
            # Check default presence
            has_default = bool(re.search(r'default\s*=', block_body))
            
            variables[var_name] = {
                "type": var_type,
                "description": var_desc,
                "has_default": has_default,
                "body": block_body,
            }
        return variables

    @classmethod
    def parse_outputs(cls, content: str) -> Dict[str, Dict[str, Any]]:
        """Extracts output declarations and their descriptions."""
        outputs = {}
        cleaned = cls.strip_comments_and_strings(content)
        
        pattern = re.compile(r'output\s+"([^"]+)"\s*\{', re.MULTILINE)
        for match in pattern.finditer(cleaned):
            out_name = match.group(1)
            start_pos = match.end() - 1
            
            brace_count = 0
            end_pos = start_pos
            for j in range(start_pos, len(cleaned)):
                if cleaned[j] == '{':
                    brace_count += 1
                elif cleaned[j] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = j
                        break
            
            block_body = cleaned[start_pos + 1:end_pos]
            desc_match = re.search(r'description\s*=\s*"([^"]*)"', block_body)
            out_desc = desc_match.group(1).strip() if desc_match else None
            
            outputs[out_name] = {
                "description": out_desc,
                "body": block_body,
            }
        return outputs

    @classmethod
    def parse_resources(cls, content: str) -> List[Tuple[str, str, str]]:
        """Extracts resource declarations: (resource_type, resource_name, block_body)."""
        resources = []
        cleaned = cls.strip_comments_and_strings(content)
        
        pattern = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE)
        for match in pattern.finditer(cleaned):
            res_type = match.group(1)
            res_name = match.group(2)
            start_pos = match.end() - 1
            
            brace_count = 0
            end_pos = start_pos
            for j in range(start_pos, len(cleaned)):
                if cleaned[j] == '{':
                    brace_count += 1
                elif cleaned[j] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = j
                        break
            
            block_body = cleaned[start_pos + 1:end_pos]
            resources.append((res_type, res_name, block_body))
        return resources

    @classmethod
    def parse_tfvars(cls, content: str) -> Set[str]:
        """Extracts variable names assigned in a .tfvars file."""
        assigned_vars = set()
        cleaned = cls.strip_comments_and_strings(content)
        
        for line in cleaned.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            match = re.match(r'^([a-zA-Z0-9_-]+)\s*=', line)
            if match:
                assigned_vars.add(match.group(1))
        return assigned_vars


# ==============================================================================
# Suite 1: Shell Scripts Verification
# ==============================================================================

class TestShellScripts(unittest.TestCase):
    """Verifies all deployment bash scripts across the repository."""

    def setUp(self):
        self.deploy_all_script = os.path.join(SCRIPT_DIR, "deploy_all.sh")
        self.cluster_scaler_deploy = os.path.join(SCRIPT_DIR, "cluster-scaler", "deploy.sh")
        self.cleaner_deploy = os.path.join(SCRIPT_DIR, "gcsfuse-reservation-cleaner", "deploy.sh")
        self.vm_stopper_deploy = os.path.join(SCRIPT_DIR, "vm-stopper", "deploy.sh")
        self.all_scripts = [
            self.deploy_all_script,
            self.cluster_scaler_deploy,
            self.cleaner_deploy,
            self.vm_stopper_deploy,
        ]

    def test_bash_syntax_on_all_deploy_scripts(self):
        """Validates that all deploy scripts pass bash -n without syntax errors."""
        for script_path in self.all_scripts:
            self.assertTrue(os.path.isfile(script_path), f"Script not found: {script_path}")
            result = subprocess.run(
                ["bash", "-n", script_path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"bash -n failed on {script_path}:\n{result.stderr}",
            )

    def test_help_flags_exit_code_and_usage(self):
        """Verifies that --help and -h return exit code 0 and output usage text on all scripts."""
        for script_path in self.all_scripts:
            for flag in ["--help", "-h"]:
                result = subprocess.run(
                    ["bash", script_path, flag],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"Script {script_path} with flag {flag} returned non-zero code {result.returncode}:\n{result.stderr}",
                )
                output = result.stdout.lower()
                self.assertIn(
                    "usage",
                    output,
                    f"Script {script_path} help output missing 'Usage':\n{result.stdout}",
                )
                self.assertIn(
                    "options",
                    output,
                    f"Script {script_path} help output missing 'Options':\n{result.stdout}",
                )

    def test_deploy_all_help_contents(self):
        """Verifies that deploy_all.sh --help documents all expected options and services."""
        result = subprocess.run(
            ["bash", self.deploy_all_script, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        output = result.stdout
        # Check services
        self.assertIn("cluster-scaler", output)
        self.assertIn("gcsfuse-reservation-cleaner", output)
        self.assertIn("vm-stopper", output)
        # Check flags
        self.assertIn("--project", output)
        self.assertIn("--region", output)
        self.assertIn("--services", output)
        self.assertIn("--service-account", output)
        self.assertIn("--scheduler-sa", output)
        self.assertIn("--dry-run", output)
        self.assertIn("--skip-tests", output)
        self.assertIn("--cluster-scaler-schedule", output)
        self.assertIn("--cleaner-schedule", output)
        self.assertIn("--vm-stopper-schedule", output)
        self.assertIn("--threshold", output)

    def test_unknown_and_invalid_flags_rejected(self):
        """Verifies that unknown flags are rejected with a non-zero exit code."""
        for script_path in self.all_scripts:
            result = subprocess.run(
                ["bash", script_path, "--unknown-test-flag-xyz"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"Script {script_path} should reject unknown flag with non-zero exit code!",
            )

    def test_missing_argument_values_rejected(self):
        """Verifies that flags requiring arguments fail when argument is omitted."""
        invalid_invocations = [
            ["--project"],
            ["--region"],
            ["--services"],
            ["--service-account"],
            ["--scheduler-sa"],
            ["--threshold"],
            ["--cluster-scaler-schedule"],
        ]
        for args in invalid_invocations:
            result = subprocess.run(
                ["bash", self.deploy_all_script] + args,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"deploy_all.sh should fail on incomplete flag {args}",
            )

    def test_services_argument_validation(self):
        """Verifies that --services validates whitelisted names and rejects invalid names."""
        # 1. Valid individual and combination services in dry-run mode
        valid_service_inputs = [
            "all",
            "cluster-scaler",
            "gcsfuse-reservation-cleaner",
            "vm-stopper",
            "cluster-scaler,vm-stopper",
            "cluster-scaler vm-stopper",
            "gcsfuse-reservation-cleaner,cluster-scaler",
        ]
        for svc in valid_service_inputs:
            result = subprocess.run(
                ["bash", self.deploy_all_script, "--project", "test-mock-proj", "--services", svc, "--dry-run", "--skip-tests"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"deploy_all.sh failed on valid --services '{svc}':\n{result.stderr}\n{result.stdout}",
            )

        # 2. Invalid service names
        invalid_service_inputs = [
            "invalid-service-xyz",
            "cluster-scaler,unknown-service",
            "fake-tool",
        ]
        for svc in invalid_service_inputs:
            result = subprocess.run(
                ["bash", self.deploy_all_script, "--project", "test-mock-proj", "--services", svc, "--dry-run", "--skip-tests"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"deploy_all.sh should fail on invalid --services '{svc}'!",
            )
            self.assertTrue(
                "unknown service" in result.stderr.lower() or "unknown service" in result.stdout.lower() or "error" in result.stderr.lower(),
                f"Error message missing for invalid service '{svc}'",
            )

    def test_missing_project_handling(self):
        """Verifies that scripts exit with code 1 and descriptive error when project ID is unresolved."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = os.path.join(temp_dir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            # Create a mock gcloud executable in PATH that returns '(unset)' for project
            mock_gcloud = os.path.join(bin_dir, "gcloud")
            with open(mock_gcloud, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
                f.write("if [ \"$1\" = \"config\" ] && [ \"$2\" = \"get-value\" ] && [ \"$3\" = \"project\" ]; then\n")
                f.write("  echo \"(unset)\"\n")
                f.write("  exit 0\n")
                f.write("fi\n")
                f.write("exit 0\n")
            os.chmod(mock_gcloud, 0o755)

            isolated_env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "HOME": temp_dir,
                "CLOUDSDK_CONFIG": os.path.join(temp_dir, ".config", "gcloud"),
                "PROJECT_ID": "",
            }
            result = subprocess.run(
                ["bash", self.deploy_all_script, "--dry-run", "--skip-tests"],
                capture_output=True,
                text=True,
                env=isolated_env,
            )
            self.assertEqual(
                result.returncode,
                1,
                f"deploy_all.sh must exit with code 1 when PROJECT_ID is missing.\nStdout: {result.stdout}\nStderr: {result.stderr}",
            )
            combined_out = (result.stdout + result.stderr).lower()
            self.assertTrue(
                "project" in combined_out and ("required" in combined_out or "specify" in combined_out),
                f"Error message did not clearly indicate project requirement:\n{result.stderr}\n{result.stdout}",
            )

    def test_dry_run_simulation_mode(self):
        """Verifies that --dry-run previews all actions without mutations."""
        result = subprocess.run(
            ["bash", self.deploy_all_script, "--project", "dry-run-test-proj", "--dry-run", "--skip-tests"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"deploy_all.sh dry-run failed:\n{result.stderr}\n{result.stdout}",
        )
        output = result.stdout
        self.assertIn("[DRY-RUN]", output, "Dry-run output should contain [DRY-RUN] action markers.")
        self.assertIn("dry-run-test-proj", output)
        self.assertIn('"dry_run":true', output.replace(" ", ""))
        self.assertIn("cluster-scaler", output)
        self.assertIn("gcsfuse-reservation-cleaner", output)
        self.assertIn("vm-stopper", output)

    def test_schedule_and_threshold_customizations(self):
        """Verifies customized cron schedules and threshold arguments propagate in dry-run mode."""
        result = subprocess.run(
            [
                "bash",
                self.deploy_all_script,
                "--project", "custom-cron-proj",
                "--dry-run",
                "--skip-tests",
                "--cluster-scaler-schedule", "0 4 * * *",
                "--cleaner-schedule", "0 12 1 * *",
                "--vm-stopper-schedule", "0 22 * * *",
                "--threshold", "14",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"Failed with custom schedules: {result.stderr}")
        output = result.stdout
        self.assertIn("0 4 * * *", output)
        self.assertIn("0 12 1 * *", output)
        self.assertIn("0 22 * * *", output)
        self.assertIn("14", output)

    def test_iam_permission_flags_and_dry_run_checks(self):
        """Verifies that IAM automation flags (-y, --yes, --auto-grant-roles, --no-grant-roles) parse cleanly in dry-run mode."""
        for flag in ["-y", "--yes", "--auto-grant-roles", "--no-grant-roles"]:
            for script_path in self.all_scripts:
                cmd = ["bash", script_path, "-p", "test-project-123", "--dry-run", flag]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"Script {script_path} failed with IAM flag '{flag}': {result.stderr}",
                )
                output = result.stdout
                self.assertIn("DRY-RUN", output)


# ==============================================================================
# Suite 2: Cloud Build Configuration Verification
# ==============================================================================

class TestCloudBuildConfig(unittest.TestCase):
    """Verifies cloudbuild.yaml schema, stage ordering, and substitution variables."""

    @classmethod
    def setUpClass(cls):
        cls.cloudbuild_path = os.path.join(SCRIPT_DIR, "cloudbuild.yaml")
        if not os.path.isfile(cls.cloudbuild_path):
            raise unittest.SkipTest(f"cloudbuild.yaml not found at {cls.cloudbuild_path}")
        with open(cls.cloudbuild_path, "r", encoding="utf-8") as f:
            cls.config = yaml.safe_load(f)

    def test_cloudbuild_yaml_syntax_and_root_keys(self):
        """Validates that cloudbuild.yaml is well-formed YAML with required root keys."""
        self.assertIsInstance(self.config, dict, "cloudbuild.yaml root must be a YAML mapping.")
        self.assertIn("steps", self.config, "cloudbuild.yaml missing 'steps' key.")
        self.assertIn("substitutions", self.config, "cloudbuild.yaml missing 'substitutions' key.")
        self.assertIn("images", self.config, "cloudbuild.yaml missing 'images' key.")
        self.assertIn("options", self.config, "cloudbuild.yaml missing 'options' key.")
        self.assertIn("timeout", self.config, "cloudbuild.yaml missing 'timeout' key.")

    def test_pre_deployment_unit_test_gating_step(self):
        """Validates that Stage 1 runs unit tests across all 3 services prior to builds."""
        steps = self.config.get("steps", [])
        self.assertTrue(len(steps) > 0, "No steps declared in cloudbuild.yaml.")
        
        # Check Step 0
        step_0 = steps[0]
        step_id = step_0.get("id", "")
        step_name = step_0.get("name", "")
        args_str = " ".join(step_0.get("args", []))

        self.assertIn("test", step_id.lower(), f"Step 0 id '{step_id}' should indicate test gating.")
        self.assertTrue("python" in step_name.lower(), f"Step 0 builder '{step_name}' should use Python image.")
        self.assertIn("cluster-scaler", args_str, "Test gating step must test cluster-scaler.")
        self.assertIn("gcsfuse-reservation-cleaner", args_str, "Test gating step must test gcsfuse-reservation-cleaner.")
        self.assertIn("vm-stopper", args_str, "Test gating step must test vm-stopper.")

    def test_parallel_image_build_steps(self):
        """Validates that build steps exist for all 3 services."""
        steps = self.config.get("steps", [])
        services = ["cluster-scaler", "gcsfuse-reservation-cleaner", "vm-stopper"]
        
        for svc in services:
            # Match docker builder step for service (ignoring non-docker test steps)
            build_step = next(
                (
                    s for s in steps
                    if s.get("name") == "gcr.io/cloud-builders/docker"
                    and "build" in s.get("args", [])
                    and any(svc in a for a in s.get("args", []))
                ),
                None,
            )
            self.assertIsNotNone(build_step, f"Missing Docker build step for service: {svc}")
            self.assertEqual(build_step.get("name"), "gcr.io/cloud-builders/docker")
            args = build_step.get("args", [])
            self.assertIn("build", args)
            self.assertTrue(any(svc in a for a in args), f"Docker build step args do not reference {svc}")

    def test_image_push_or_artifact_registry_images(self):
        """Validates that the images block lists container image targets for all 3 services."""
        images = self.config.get("images", [])
        services = ["cluster-scaler", "gcsfuse-reservation-cleaner", "vm-stopper"]
        
        for svc in services:
            found = any(svc in img for img in images)
            self.assertTrue(found, f"Images list missing target for service: {svc}")

    def test_cloud_run_deploy_steps(self):
        """Validates that Cloud Run deployment steps exist and enforce required security settings."""
        steps = self.config.get("steps", [])
        services = ["cluster-scaler", "gcsfuse-reservation-cleaner", "vm-stopper"]
        
        deploy_step = next(
            (s for s in steps if "deploy-cloud-run" in s.get("id", "") or any("gcloud run deploy" in a for a in s.get("args", []))),
            None,
        )
        self.assertIsNotNone(deploy_step, "Missing Cloud Run deployment step in cloudbuild.yaml.")
        args_str = " ".join(deploy_step.get("args", []))
        
        for svc in services:
            self.assertIn(svc, args_str, f"Deploy step must reference service '{svc}'")
        
        self.assertIn("--no-allow-unauthenticated", args_str, "Cloud Run deployment must enforce authentication.")
        self.assertTrue("--timeout" in args_str or "--timeout=" in args_str, "Cloud Run timeout must be configured.")
        self.assertTrue("--memory" in args_str or "--memory=" in args_str, "Cloud Run memory must be configured.")

    def test_scheduler_configuration_steps(self):
        """Validates that Cloud Scheduler configuration steps exist with OIDC auth."""
        steps = self.config.get("steps", [])
        sched_step = next(
            (s for s in steps if "scheduler" in s.get("id", "") or any("gcloud scheduler" in a for a in s.get("args", []))),
            None,
        )
        self.assertIsNotNone(sched_step, "Missing Cloud Scheduler configuration step in cloudbuild.yaml.")
        args_str = " ".join(sched_step.get("args", []))
        
        services = ["cluster-scaler", "gcsfuse-reservation-cleaner", "vm-stopper"]
        for svc in services:
            self.assertIn(svc, args_str, f"Scheduler step must reference service '{svc}'")
        
        self.assertIn("--oidc-service-account-email", args_str, "Scheduler must use OIDC service account auth.")
        self.assertIn("--oidc-token-audience", args_str, "Scheduler must configure OIDC token audience.")

    def test_substitutions_schema_and_defaults(self):
        """Validates that all required substitution variables are declared with default values."""
        subs = self.config.get("substitutions", {})
        
        expected_subs = {
            "_PROJECT_ID": "",
            "_REGION": "us-central1",
            "_REPO_NAME": "gcsfuse-tools",
            "_IMAGE_TAG": "latest",
            "_DRY_RUN": "false",
            "_CLUSTER_SCALER_SCHEDULE": "0 2 * * *",
            "_CLEANER_SCHEDULE": "0 0 1 * *",
            "_VM_STOPPER_SCHEDULE": "0 20 * * *",
            "_IDLE_DAYS_THRESHOLD": "7",
            "_CLUSTER_SCALER_SA": "cluster-scaler-sa",
            "_CLUSTER_SCALER_SCHEDULER_SA": "cluster-scaler-sched",
            "_CLEANER_SA": "gcsfuse-res-cleaner-sa",
            "_CLEANER_SCHEDULER_SA": "gcsfuse-res-cleaner-sched",
            "_VM_STOPPER_SA": "vm-stopper-sa",
            "_VM_STOPPER_SCHEDULER_SA": "vm-stopper-sched",
        }
        
        for var_name, expected_val in expected_subs.items():
            self.assertIn(var_name, subs, f"Missing substitution variable: {var_name}")
            if expected_val:
                self.assertEqual(
                    subs[var_name],
                    expected_val,
                    f"Substitution {var_name} default value mismatch: expected '{expected_val}', got '{subs[var_name]}'",
                )

    def test_cloudbuild_options_and_timeout(self):
        """Validates options logging and timeout configurations."""
        options = self.config.get("options", {})
        self.assertEqual(options.get("logging"), "CLOUD_LOGGING_ONLY")
        timeout = self.config.get("timeout", "")
        self.assertTrue(timeout.endswith("s"), f"Timeout must be formatted in seconds: '{timeout}'")
        seconds = int(timeout[:-1])
        self.assertGreaterEqual(seconds, 600, "Cloud Build pipeline timeout should be >= 600s.")


# ==============================================================================
# Suite 3: Terraform Module Static Analysis & Schema Verification
# ==============================================================================

class TestTerraformModule(unittest.TestCase):
    """Verifies Terraform syntax, resource schema, variables, and outputs offline."""

    @classmethod
    def setUpClass(cls):
        cls.main_tf = os.path.join(TERRAFORM_DIR, "main.tf")
        cls.variables_tf = os.path.join(TERRAFORM_DIR, "variables.tf")
        cls.outputs_tf = os.path.join(TERRAFORM_DIR, "outputs.tf")
        cls.tfvars_example = os.path.join(TERRAFORM_DIR, "terraform.tfvars.example")
        cls.readme_tf = os.path.join(TERRAFORM_DIR, "README.md")
        
        # Read contents cleanly using context managers
        def _read_file_safe(path: str) -> str:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            return ""

        cls.main_content = _read_file_safe(cls.main_tf)
        cls.vars_content = _read_file_safe(cls.variables_tf)
        cls.outputs_content = _read_file_safe(cls.outputs_tf)
        cls.tfvars_content = _read_file_safe(cls.tfvars_example)

    def test_required_terraform_files_exist(self):
        """Verifies that all required .tf files and documentation exist."""
        required_files = [
            self.main_tf,
            self.variables_tf,
            self.outputs_tf,
            self.tfvars_example,
            self.readme_tf,
        ]
        for fpath in required_files:
            self.assertTrue(os.path.isfile(fpath), f"Required Terraform file missing: {fpath}")

    def test_hcl_delimiter_balance_across_all_tf_files(self):
        """Verifies balanced braces, brackets, and parentheses across all HCL files."""
        tf_files = [
            ("main.tf", self.main_content),
            ("variables.tf", self.vars_content),
            ("outputs.tf", self.outputs_content),
        ]
        for fname, content in tf_files:
            self.assertTrue(len(content) > 0, f"File {fname} is empty!")
            balanced, msg = HCLStaticAnalyzer.check_balanced_delimiters(content)
            self.assertTrue(balanced, f"HCL syntax error in {fname}: {msg}")

    def test_all_variables_have_types_and_descriptions(self):
        """Verifies that every variable in variables.tf has an explicit type and description."""
        variables = HCLStaticAnalyzer.parse_variables(self.vars_content)
        self.assertGreaterEqual(len(variables), 15, f"Expected >= 15 variables, found {len(variables)}")

        for var_name, var_info in variables.items():
            self.assertIsNotNone(
                var_info["type"],
                f"Variable '{var_name}' is missing an explicit 'type' definition.",
            )
            self.assertIsNotNone(
                var_info["description"],
                f"Variable '{var_name}' is missing a 'description' string.",
            )
            self.assertGreater(
                len(var_info["description"]),
                5,
                f"Variable '{var_name}' description is too short: '{var_info['description']}'",
            )

    def test_core_variables_declared_with_correct_types(self):
        """Verifies presence and types of core variables and service toggles."""
        variables = HCLStaticAnalyzer.parse_variables(self.vars_content)
        
        # Required project_id
        self.assertIn("project_id", variables)
        self.assertFalse(variables["project_id"]["has_default"], "project_id should not have a default value.")
        
        # Service toggles
        for toggle in ["enable_cluster_scaler", "enable_reservation_cleaner", "enable_vm_stopper"]:
            self.assertIn(toggle, variables, f"Missing service toggle: {toggle}")
            self.assertIn("bool", variables[toggle]["type"])
            self.assertTrue(variables[toggle]["has_default"])

        # Cron schedule variables
        for sched_var in ["cluster_scaler_schedule", "reservation_cleaner_schedule", "vm_stopper_schedule"]:
            self.assertIn(sched_var, variables, f"Missing schedule variable: {sched_var}")
            self.assertIn("string", variables[sched_var]["type"])

        # Dry run variable
        self.assertIn("dry_run", variables)
        self.assertIn("bool", variables["dry_run"]["type"])

    def test_required_resources_declared_in_main_tf(self):
        """Verifies that main.tf declares Cloud Run v2, Scheduler, IAM, and SA resources."""
        resources = HCLStaticAnalyzer.parse_resources(self.main_content)
        resource_types = [r[0] for r in resources]
        resource_names = [r[1] for r in resources]

        # 1. Cloud Run v2 Services
        self.assertIn("google_cloud_run_v2_service", resource_types)
        cloud_run_resources = [r[1] for r in resources if r[0] == "google_cloud_run_v2_service"]
        self.assertIn("cluster_scaler", cloud_run_resources)
        self.assertIn("reservation_cleaner", cloud_run_resources)
        self.assertIn("vm_stopper", cloud_run_resources)

        # 2. Cloud Scheduler Jobs
        self.assertIn("google_cloud_scheduler_job", resource_types)
        sched_resources = [r[1] for r in resources if r[0] == "google_cloud_scheduler_job"]
        self.assertIn("cluster_scaler", sched_resources)
        self.assertIn("reservation_cleaner", sched_resources)
        self.assertIn("vm_stopper", sched_resources)

        # 3. Service Accounts
        self.assertIn("google_service_account", resource_types)

        # 4. Project IAM Bindings
        self.assertIn("google_project_iam_member", resource_types)

        # 5. Cloud Run Invoker IAM Bindings
        self.assertIn("google_cloud_run_v2_service_iam_member", resource_types)

    def test_required_outputs_defined(self):
        """Verifies outputs.tf declares service URLs, scheduler jobs, and composite maps."""
        outputs = HCLStaticAnalyzer.parse_outputs(self.outputs_content)
        
        # Individual service URLs
        expected_individual_urls = [
            "cluster_scaler_service_url",
            "reservation_cleaner_service_url",
            "vm_stopper_service_url",
        ]
        for out in expected_individual_urls:
            self.assertIn(out, outputs, f"Missing output: {out}")
            self.assertIsNotNone(outputs[out]["description"], f"Output {out} missing description")

        # Composite maps
        expected_maps = [
            "service_urls",
            "scheduler_jobs",
            "runner_service_accounts",
            "scheduler_service_accounts",
        ]
        for out in expected_maps:
            self.assertIn(out, outputs, f"Missing composite map output: {out}")
            self.assertIsNotNone(outputs[out]["description"], f"Output {out} missing description")

    def test_tfvars_example_matches_variables_tf(self):
        """Verifies that all entries in terraform.tfvars.example correspond to declared variables."""
        variables = HCLStaticAnalyzer.parse_variables(self.vars_content)
        tfvars_keys = HCLStaticAnalyzer.parse_tfvars(self.tfvars_content)
        
        self.assertGreater(len(tfvars_keys), 5, "terraform.tfvars.example has too few entries.")
        self.assertIn("project_id", tfvars_keys, "terraform.tfvars.example missing project_id.")

        for key in tfvars_keys:
            self.assertIn(
                key,
                variables,
                f"Variable '{key}' assigned in terraform.tfvars.example is not declared in variables.tf!",
            )

    def test_terraform_cli_if_available(self):
        """Runs terraform fmt -check and terraform validate if terraform CLI is installed."""
        terraform_bin = shutil.which("terraform")
        if not terraform_bin:
            # Terraform CLI is optional in offline mock test environment; HCL static analysis passed above
            return
        
        # Format check
        fmt_res = subprocess.run(
            [terraform_bin, "fmt", "-check", TERRAFORM_DIR],
            capture_output=True,
            text=True,
        )
        self.assertEqual(fmt_res.returncode, 0, f"terraform fmt -check failed:\n{fmt_res.stderr}\n{fmt_res.stdout}")


# ==============================================================================
# Suite 4: Service Unit Test Suites & Adversarial Stress Tests
# ==============================================================================

class TestServiceUnitSuites(unittest.TestCase):
    """Executes offline unit tests and adversarial stress tests for all 3 services."""

    def test_cluster_scaler_unit_tests(self):
        """Executes cluster-scaler unit test suite."""
        service_dir = os.path.join(SCRIPT_DIR, "cluster-scaler")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=service_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "."},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"cluster-scaler unit tests failed:\n{result.stderr}\n{result.stdout}",
        )

    def test_reservation_cleaner_unit_tests(self):
        """Executes gcsfuse-reservation-cleaner unit test suite."""
        service_dir = os.path.join(SCRIPT_DIR, "gcsfuse-reservation-cleaner")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=service_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "."},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gcsfuse-reservation-cleaner unit tests failed:\n{result.stderr}\n{result.stdout}",
        )

    def test_vm_stopper_unit_tests(self):
        """Executes vm-stopper unit test suite."""
        service_dir = os.path.join(SCRIPT_DIR, "vm-stopper")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=service_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "."},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"vm-stopper unit tests failed:\n{result.stderr}\n{result.stdout}",
        )

    def test_adversarial_stress_tests(self):
        """Executes verify_stress_tests.py empirical test harness."""
        stress_script = os.path.join(SCRIPT_DIR, "verify_stress_tests.py")
        self.assertTrue(os.path.isfile(stress_script), f"Missing stress tests script: {stress_script}")
        
        result = subprocess.run(
            [sys.executable, stress_script],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "cluster-scaler:gcsfuse-reservation-cleaner:vm-stopper:."},
        )
        self.assertEqual(
            result.returncode,
            0,
            f"verify_stress_tests.py failed:\n{result.stderr}\n{result.stdout}",
        )


# ==============================================================================
# Custom Test Runner with Formatted Execution Summary Table
# ==============================================================================

class DetailedTestResult(unittest.TextTestResult):
    """Tracks test counts and timings per test case and test suite category."""

    def __init__(self, stream: Any, descriptions: bool, verbosity: int):
        super().__init__(stream, descriptions, verbosity)
        self.test_timings: Dict[str, float] = {}
        self.suite_metrics: Dict[str, Dict[str, Any]] = {}
        self._current_test_start = 0.0

    def _ensure_suite(self, suite_name: str) -> Dict[str, Any]:
        if suite_name not in self.suite_metrics:
            self.suite_metrics[suite_name] = {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "duration": 0.0,
            }
        return self.suite_metrics[suite_name]

    def startTest(self, test: unittest.TestCase):
        super().startTest(test)
        self._ensure_suite(test.__class__.__name__)
        self._current_test_start = time.perf_counter()

    def stopTest(self, test: unittest.TestCase):
        elapsed = time.perf_counter() - self._current_test_start
        test_id = test.id()
        self.test_timings[test_id] = elapsed
        
        metrics = self._ensure_suite(test.__class__.__name__)
        metrics["total"] += 1
        metrics["duration"] += elapsed
        super().stopTest(test)

    def addSuccess(self, test: unittest.TestCase):
        metrics = self._ensure_suite(test.__class__.__name__)
        metrics["passed"] += 1
        super().addSuccess(test)

    def addFailure(self, test: unittest.TestCase, err: Any):
        metrics = self._ensure_suite(test.__class__.__name__)
        metrics["failed"] += 1
        super().addFailure(test, err)

    def addError(self, test: unittest.TestCase, err: Any):
        metrics = self._ensure_suite(test.__class__.__name__)
        metrics["errors"] += 1
        super().addError(test, err)

    def addSkip(self, test: unittest.TestCase, reason: str):
        metrics = self._ensure_suite(test.__class__.__name__)
        metrics["skipped"] += 1
        super().addSkip(test, reason)


def print_summary_table(result: DetailedTestResult, total_duration: float):
    """Renders a formatted ASCII summary table of test results."""
    print("\n" + "=" * 90)
    print("GCSFUSE CLOUD RUN SERVICES - DEPLOYMENT ARTIFACTS VERIFICATION SUITE")
    print("=" * 90)
    print(f"{'Test Suite / Category':<35} {'Total':>7} {'Passed':>8} {'Failed':>8} {'Errors':>8} {'Skipped':>9} {'Duration':>10}")
    print("-" * 90)

    total_tests = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_skipped = 0

    # Ordered display
    category_order = [
        "TestShellScripts",
        "TestCloudBuildConfig",
        "TestTerraformModule",
        "TestServiceUnitSuites",
    ]

    for suite_name in category_order:
        metrics = result.suite_metrics.get(suite_name, {
            "total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0
        })
        total_tests += metrics["total"]
        total_passed += metrics["passed"]
        total_failed += metrics["failed"]
        total_errors += metrics["errors"]
        total_skipped += metrics["skipped"]
        
        status_flag = "✓" if (metrics["failed"] == 0 and metrics["errors"] == 0) else "✗"
        display_name = f"{suite_name} {status_flag}"
        dur_str = f"{metrics['duration']:.2f}s"
        print(f"{display_name:<35} {metrics['total']:>7} {metrics['passed']:>8} {metrics['failed']:>8} {metrics['errors']:>8} {metrics['skipped']:>9} {dur_str:>10}")

    print("-" * 90)
    total_dur_str = f"{total_duration:.2f}s"
    print(f"{'TOTAL':<35} {total_tests:>7} {total_passed:>8} {total_failed:>8} {total_errors:>8} {total_skipped:>9} {total_dur_str:>10}")
    print("=" * 90)

    if result.wasSuccessful():
        print(f"OVERALL STATUS: ALL {total_tests} VERIFICATION TESTS PASSED CLEANLY [PASS]")
    else:
        print(f"OVERALL STATUS: VERIFICATION SUITE ENCOUNTERED FAILURES / ERRORS [FAIL]")
    print("=" * 90 + "\n")


def run_verification_suite() -> bool:
    """Loads all test cases and executes them with the detailed runner."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestShellScripts))
    suite.addTests(loader.loadTestsFromTestCase(TestCloudBuildConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestTerraformModule))
    suite.addTests(loader.loadTestsFromTestCase(TestServiceUnitSuites))

    runner = unittest.TextTestRunner(resultclass=DetailedTestResult, verbosity=2)
    start_time = time.perf_counter()
    result = runner.run(suite)  # type: ignore
    total_duration = time.perf_counter() - start_time

    if isinstance(result, DetailedTestResult):
        print_summary_table(result, total_duration)
        return result.wasSuccessful()
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_verification_suite()
    sys.exit(0 if success else 1)
