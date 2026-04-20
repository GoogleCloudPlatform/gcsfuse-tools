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

import sys
import re
import argparse
import subprocess
import tempfile

from pathlib import Path
from collections import defaultdict

# Regex to match Go's test output lines and extract the test name
TEST_PATTERN = re.compile(r'---\s+(PASS|FAIL|SKIP):\s+([^\s]+)')
BENCHMARK_PATTERN = re.compile(r'(Benchmark_[^\s\-]+)')

def get_display_width(s):
    """Calculates the visual width of a string, counting emojis as width 2."""
    width = 0
    for c in s:
        # Common emojis used in report
        if c in ['✅', '❌', '⚠️', '⚪']:
            width += 2
        else:
            width += 1
    return width

def pad_string(s, target_width, align='left'):
    """Pads a string to a target display width."""
    current_width = get_display_width(s)
    padding_needed = target_width - current_width
    if padding_needed <= 0:
        return s
    
    if align == 'left':
        return s + " " * padding_needed
    elif align == 'right':
        return " " * padding_needed + s
    else: # center
        left_pad = padding_needed // 2
        right_pad = padding_needed - left_pad
        return " " * left_pad + s + " " * right_pad

# ==============================================================================

# 1. LOG SYNCHRONIZATION
# ==============================================================================

def sync_logs(bucket_name, release_version, vm_name_prefix, output_dir):
    """Syncs logs from GCS using gcloud storage cp."""
    prefix = f"{release_version}/{vm_name_prefix}"
    
    print(f"Using output directory: {output_dir}")

    print(f"Syncing folders from gs://{bucket_name}/{prefix}* to {output_dir}...")
    
    # Use gcloud storage cp with the recursive flag and wildcard
    cmd = ["gcloud", "storage", "cp", "-r", f"gs://{bucket_name}/{prefix}*", output_dir]
    try:
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("\n" + "!" * 80)
            print(" WARNING: Some downloads have failed! Results may be incomplete.")
            print(f" gcloud storage cp exited with code {result.returncode}")
            print("!" * 80 + "\n")
            return False
    except Exception as e:
        print(f"Error executing gcloud command: {e}")
        return False

    print(f"Sync complete. Files are located in: {output_dir}")
    return True



# ==============================================================================
# 2. RUNTIME STATS ANALYSIS
# ==============================================================================

def analyze_runtime_stats(parent_dir):
    """Parses package_runtime_stats.txt files to extract test results."""
    # results[btype][vm_name][pkg][attempt] = status_emoji
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    files_processed = 0
    parent_path = Path(parent_dir)

    # Find all package_runtime_stats.txt files in the given directory and subdirectories
    for filepath in parent_path.rglob('package_runtime_stats.txt'):
        files_processed += 1
        
        try:
            vm_name = filepath.relative_to(parent_path).parts[0]
        except Exception:
            vm_name = "unknown_vm"
            
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                        
                    parts = line.split()
                    if len(parts) >= 3:
                        pkg = parts[0]
                        btype = parts[1]
                        status_code = parts[2]
                        
                            
                        attempt = int(parts[5]) if len(parts) > 5 else 0
                        status_emoji = '✅' if status_code == '0' else '❌'
                        
                        results[btype][vm_name][pkg][attempt] = status_emoji
        except Exception as e:
            print(f"Error reading {filepath}: {e}")

    return results, files_processed


def generate_runtime_report(results, files_processed):
    """Prints the report for runtime stats."""
    print("\n" + "=" * 80)
    print(" GCSFUSE E2E RUNTIME STATS ANALYSIS REPORT ".center(80, "="))
    print("=" * 80)
    print(f"Total Log Files Processed: {files_processed}")
    print("-" * 80)

    if not results:
        print("\nNo runtime stats found. Please check if package_runtime_stats.txt files were synced.")
        return

    # Process each bucket type separately
    for btype, packages in sorted(results.items()):
        print(f"\n>> BUCKET TYPE: [{btype.upper()}] <<\n")
        
        # Calculate stats and sort
        stats = []
        for pkg, counts in packages.items():
            passed = counts['passed']
            failed = counts['failed']
            flaky = counts.get('flaky', 0)
            total = passed + failed + flaky
            
            # Categorize status enum
            if failed > 0:
                status_enum = 1
                status = "FAILED ❌"
            elif flaky > 0:
                status_enum = 2
                status = "FLAKY ⚠️"
            else:
                status_enum = 3
                status = "PASSED ✅"
                
            stats.append({
                'pkg': pkg,
                'total': total,
                'passed': passed,
                'failed': failed,
                'flaky': flaky,
                'status_enum': status_enum,
                'status': status
            })
            
        # Sort by status enum (FAILED -> FLAKY -> PASSED), then by fails descending
        stats.sort(key=lambda x: (x['status_enum'], -x['failed'], -x['flaky']))
        
        # Print Table Header
        header = f"| {'Package Name':<30} | {'Total':<7} | {'Passed':<6} | {'Flaky':<6} | {'Failed':<8} | {'Status':<12} |"
        print("-" * len(header))
        print(header)
        print("-" * len(header))
        
        # Print Table Rows
        for s in stats:
            row = f"| {s['pkg']:<30} | {s['total']:<7} | {s['passed']:<6} | {s['flaky']:<6} | {s['failed']:<8} | {s['status']:<12} |"
            print(row)
            
        print("-" * len(header))


# ==============================================================================
# 3. TEST LEVEL ANALYSIS
# ==============================================================================

def analyze_test_level_logs(parent_dir):
    """Parses individual test logs to extract specific test failures."""
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {'passed': 0, 'failed': 0, 'skipped': 0})))
    failures = []
    target_folders = ['failed_package_logs', 'success_package_logs']
    files_processed = 0
    parent_path = Path(parent_dir)

    for filepath in parent_path.rglob('*.txt'):
        parts = filepath.parts
        log_type = None
        for tf in target_folders:
            if tf in parts:
                log_type = tf
                break
                
        if not log_type:
            continue
            
        bucket_type = filepath.parent.name
        package_name = filepath.stem
        
        try:
            vm_name = filepath.relative_to(parent_path).parts[0]
        except Exception:
            vm_name = "unknown_vm"

        files_processed += 1
        has_failures = False

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "flags empty: no tests to run" in line:
                        test_name = "<INCOMPATIBLE>"
                        results[package_name][bucket_type][test_name]['skipped'] += 1
                        continue

                    test_match = TEST_PATTERN.search(line)

                    if test_match:
                        status = test_match.group(1).upper()
                        test_name = test_match.group(2)
                        
                            
                        if status == 'PASS':
                            results[package_name][bucket_type][test_name]['passed'] += 1
                        elif status == 'FAIL':
                            results[package_name][bucket_type][test_name]['failed'] += 1
                            has_failures = True
                            failures.append({'vm': vm_name, 'pkg': package_name, 'bucket': bucket_type, 'test': test_name})
                        elif status == 'SKIP':
                            results[package_name][bucket_type][test_name]['skipped'] += 1
                        continue

                    bench_match = BENCHMARK_PATTERN.search(line)
                    if bench_match:
                        test_name = bench_match.group(1)
                            
                        if log_type == 'success_package_logs':
                            results[package_name][bucket_type][test_name]['passed'] += 1
                        elif log_type == 'failed_package_logs':
                            results[package_name][bucket_type][test_name]['failed'] += 1
                            has_failures = True
                            failures.append({'vm': vm_name, 'pkg': package_name, 'bucket': bucket_type, 'test': test_name})

                            
            # Edge Case: If the package log was dumped in the 'failed' directory but no specific 
            # '--- FAIL:' lines were printed, the test binary panicked, failed to compile, or timed out.
            if log_type == 'failed_package_logs' and not has_failures:
                dummy_name = "[PACKAGE_CRASH_OR_TIMEOUT]"
                if dummy_name not in results[package_name][bucket_type]:
                    results[package_name][bucket_type][dummy_name] = {'passed': 0, 'failed': 0, 'skipped': 0}
                results[package_name][bucket_type][dummy_name]['failed'] += 1
                failures.append({'vm': vm_name, 'pkg': package_name, 'bucket': bucket_type, 'test': dummy_name})

        except Exception as e:
            print(f"Error reading {filepath}: {e}")

    return results, failures, files_processed


def generate_test_level_report(results, files_processed):
    """Prints the report for test-level logs."""
    print("\n" + "=" * 115)
    print(" GCSFUSE E2E PACKAGE x BUCKET TYPE ANALYSIS ".center(115, "="))
    print("=" * 115)
    print(f"Total Log Files Processed: {files_processed}")
    print("-" * 115)

    if not results:
        print("\nNo detailed test logs found. Ensure you are pointing to the correct root directory.")
        return

    # Pre-calculate total failures per (package, bucket) combination
    groups = []
    for package_name, buckets in results.items():
        for bucket_type, tests in buckets.items():
            total_failures = sum(t['failed'] for t in tests.values())
            groups.append((package_name, bucket_type, tests, total_failures))

    # Sort groups: total_failures descending, then package alpha, then bucket type alpha
    groups.sort(key=lambda x: (-x[3], x[0], x[1]))

    for package_name, bucket_type, tests, _ in groups:
            
            # Print the Header for this Package x Bucket combination
            print(f"\n" + "=" * 115)
            print(f" PACKAGE: {package_name.upper()} | BUCKET TYPE: {bucket_type.upper()} ".center(115, "="))
            print("=" * 115)
            
            stats = []
            for test_name, counts in tests.items():
                passed = counts['passed']
                failed = counts['failed']
                skipped = counts['skipped']
                
                total_exec = passed + failed + skipped
                
                if failed > 0 and passed == 0:
                    status_val = 1
                    status_label = "FAILED ❌"
                elif failed > 0 and passed > 0:
                    status_val = 2
                    status_label = "FLAKY ⚠️"
                elif passed > 0:
                    status_val = 3
                    status_label = "PASSED ✅"
                else:
                    status_val = 4
                    status_label = "SKIPPED"
                
                stats.append({
                    'test': test_name,
                    'total_exec': total_exec,
                    'passed': passed,
                    'failed': failed,
                    'skipped': skipped,
                    'status_val': status_val,
                    'status_label': status_label
                })
            
            # Sort by Status (FAILED -> FLAKY -> PASSED -> SKIPPED), then by total failed descending
            stats.sort(key=lambda x: (x['status_val'], -x['failed']))
            
            # Dynamically calculate column width for test names
            max_name_len = max([len(s['test']) for s in stats] + [18])
            header = f"| {'Specific Test Name':<{max_name_len}} | {'Total':<7} | {'Passed':<6} | {'Failed':<8} | {'Skipped':<7} | {'Status':<12} |"
            print("-" * len(header))
            print(header)
            print("-" * len(header))
            
            for s in stats:
                row = f"| {s['test']:<{max_name_len}} | {s['total_exec']:<7} | {s['passed']:<6} | {s['failed']:<8} | {s['skipped']:<7} | {s['status_label']:<12} |"
                print(row)
                
            print("-" * len(header))
# ==============================================================================
# 4. MATRIX AND FAILURE REPORTING
# ==============================================================================

def generate_matrix_report(results, vm_name_prefix):
    """Prints a matrix view of results per VM and Package."""
    print("\n" + "=" * 80)
    print(" GCSFUSE E2E MATRIX SUMMARY ".center(80, "="))
    print("=" * 80)

    if not results:
        print("\nNo results to display.")
        return

    for btype, vms in sorted(results.items()):
        print(f"\n>> BUCKET TYPE: [{btype.upper()}] <<\n")
        
        # Get all unique packages and VMs
        all_packages = set()
        all_vms = sorted(vms.keys())
        for vm_name, pkgs in vms.items():
            all_packages.update(pkgs.keys())
        all_packages = sorted(list(all_packages))
        
        if not all_packages:
            print("No packages found.")
            continue
            
        # Shorten VM names for display
        display_vms = []
        for vm in all_vms:
            if vm.startswith(vm_name_prefix):
                d_vm = vm[len(vm_name_prefix):]
                if d_vm.startswith("-"):
                    d_vm = d_vm[1:]
                display_vms.append(d_vm)
            else:
                display_vms.append(vm)
        
        # Calculate width for shortened VM name
        max_vm_len = max([get_display_width(vm) for vm in display_vms] + [15])
        
        # Limit package names to 15 chars for vertical display
        limit = 15
        pkg_headers = [pkg[:limit] for pkg in all_packages]
        pkg_headers_padded = [f"{pkg:<{limit}}" for pkg in pkg_headers]
        
        # Build vertical headers
        header_lines = []
        for row_idx in range(limit):
            if row_idx == limit - 1:
                parts = [f"{'VM Name':<{max_vm_len}}"]
            else:
                parts = [f"{' ':<{max_vm_len}}"]
                
            for pkg in pkg_headers_padded:
                parts.append(f"{pkg[row_idx]:^6}")
            header_lines.append("| " + " | ".join(parts) + " |")
            
        header_display_width = get_display_width(header_lines[0])
        
        print("-" * header_display_width)
        for line in header_lines:
            print(line)
        print("-" * header_display_width)
        
        for i, vm in enumerate(all_vms):
            display_vm = display_vms[i]
            row_parts = [pad_string(display_vm, max_vm_len)]
            
            for pkg in all_packages:
                attempts = vms[vm].get(pkg, {})
                if not attempts:
                    status_str = "⚪"
                else:
                    # Sort attempts by key
                    sorted_attempts = [attempts[k] for k in sorted(attempts.keys())]
                    # Take at most last 3
                    last_attempts = sorted_attempts[-3:]
                    status_str = "".join(last_attempts)
                    
                row_parts.append(pad_string(status_str, 6))
                
            row = "| " + " | ".join(row_parts) + " |"
            print(row)
            
        print("-" * header_display_width)


def generate_consolidated_failures_table(failures, bucket_name, release_version, vm_name_prefix):
    """Prints a consolidated table of failures with HTTPS log links."""
    print("\n" + "=" * 120)
    print(" CONSOLIDATED FAILURES TABLE ".center(120, "="))
    print("=" * 120)

    if not failures:
        print("\nNo failures found! 🎉")
        return

    formatted_failures = []
    for f in failures:
        vm = f['vm']
        if vm.startswith(vm_name_prefix):
            display_vm = vm[len(vm_name_prefix):]
            if display_vm.startswith("-"):
                display_vm = display_vm[1:]
        else:
            display_vm = vm
            
        link = f"https://console.cloud.google.com/storage/browser/{bucket_name}/{release_version}/{f['vm']}/failed-package-gcsfuse-logs"
        
        test_name = f['test']
        if len(test_name) > 35:
            test_name = test_name[:32] + "..."
            
        formatted_failures.append({
            'bucket': f['bucket'].upper(),
            'vm': display_vm,
            'pkg': f['pkg'],
            'test': test_name,
            'link': link
        })
        
    formatted_failures.sort(key=lambda x: (x['vm'], x['pkg'], x['test']))
        
    w_bucket = max([len(f['bucket']) for f in formatted_failures] + [6])
    w_vm = max([len(f['vm']) for f in formatted_failures] + [15])
    w_pkg = max([len(f['pkg']) for f in formatted_failures] + [20])
    w_test = max([len(f['test']) for f in formatted_failures] + [35])
    w_link = max([len(f['link']) for f in formatted_failures] + [50])
    
    header = f"| {'Bucket':<{w_bucket}} | {'VM':<{w_vm}} | {'Package':<{w_pkg}} | {'Test':<{w_test}} | {'Logs Link':<{w_link}} |"
    
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    
    for f in formatted_failures:
        row = f"| {f['bucket']:<{w_bucket}} | {f['vm']:<{w_vm}} | {f['pkg']:<{w_pkg}} | {f['test']:<{w_test}} | {f['link']:<{w_link}} |"
        print(row)
        
    print("-" * len(header))


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Portable GCSFUSE E2E Result Analyzer (Python)")
    parser.add_argument("--release-version", required=True, help="Release version (e.g., v999.999.987)")
    parser.add_argument("--release-bucket-name", default="gcsfuse-release-packages", help="Release bucket name")
    parser.add_argument("--vm-name-prefix", required=True, help="VM name prefix (e.g., mky-release-test)")
    parser.add_argument("--output-file", help="Path to save final output (optional, defaults to stdout)")
    args = parser.parse_args()

    # Redirect stdout if output file is specified
    if args.output_file:
        try:
            sys.stdout = open(args.output_file, 'w', encoding='utf-8')
        except Exception as e:
            print(f"Error opening output file {args.output_file}: {e}", file=sys.stderr)

    # Determine paths
    with tempfile.TemporaryDirectory(prefix="gcsfuse_output_") as output_dir:
        # Step 1: Sync
        sync_success = sync_logs(args.release_bucket_name, args.release_version, args.vm_name_prefix, output_dir)

        if not sync_success:
            print("\n" + "!" * 80)
            print(" WARNING: Sync failed or was incomplete! Results below may be incomplete.".center(80))
            print("!" * 80 + "\n")


        # Step 2: Runtime Stats Analysis
        results_rt, count_rt = analyze_runtime_stats(output_dir)
        
        # Aggregate results for the old runtime report to avoid breaking it
        from collections import defaultdict
        aggregated_rt = defaultdict(lambda: defaultdict(lambda: {'passed': 0, 'failed': 0, 'flaky': 0}))
        for btype, vms in results_rt.items():
            for vm, pkgs in vms.items():
                for pkg, attempts in pkgs.items():
                    sorted_atts = sorted(attempts.keys())
                    last_status = attempts[sorted_atts[-1]]
                    if last_status == '✅':
                        if len(sorted_atts) > 1:
                            aggregated_rt[btype][pkg]['flaky'] += 1
                        else:
                            aggregated_rt[btype][pkg]['passed'] += 1
                    else:
                        aggregated_rt[btype][pkg]['failed'] += 1
                        
        generate_runtime_report(aggregated_rt, count_rt)

        # Step 3: Matrix Summary
        generate_matrix_report(results_rt, args.vm_name_prefix)

        # Step 4: Test Level Logs Analysis
        results_tl, failures, count_tl = analyze_test_level_logs(output_dir)
        
        # Step 5: Consolidated Failures Table (Placed ABOVE detailed report)
        generate_consolidated_failures_table(failures, args.release_bucket_name, args.release_version, args.vm_name_prefix)
        
        # Step 6: Detailed Test Level Report
        generate_test_level_report(results_tl, count_tl)



if __name__ == "__main__":
    main()
