#!/usr/bin/env python3
# Copyright 2026 Google LLC

import os
import sys
import re
import argparse
import subprocess
import shutil
from pathlib import Path

# ==============================================================================
# 1. LOG SYNCHRONIZATION
# ==============================================================================

def sync_logs(bucket_name, release_version, vm_name_prefix, output_dir):
    """Syncs logs from GCS using gcloud storage cp."""
    prefix = f"{release_version}/{vm_name_prefix}"
    
    # Cleaning step: Remove the folder if it exists, then recreate it
    print(f"Cleaning local output directory: {output_dir}")
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

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
# 2. RUNTIME STATS ANALYSIS (Incorporates analyze_logs.py)
# ==============================================================================

def analyze_runtime_stats(parent_dir):
    """Parses package_runtime_stats.txt files to extract test results."""
    results = {}
    files_processed = 0

    # Find all package_runtime_stats.txt files in the given directory and subdirectories
    for filepath in Path(parent_dir).rglob('package_runtime_stats.txt'):
        files_processed += 1
        
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
                        
                        if btype not in results:
                            results[btype] = {}
                        if pkg not in results[btype]:
                            results[btype][pkg] = {'passed': 0, 'failed': 0}
                            
                        if status_code == '0':
                            results[btype][pkg]['passed'] += 1
                        else:
                            results[btype][pkg]['failed'] += 1
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
            total = passed + failed
            fail_rate = (failed / total * 100) if total > 0 else 0
            stats.append({
                'pkg': pkg,
                'total': total,
                'passed': passed,
                'failed': failed,
                'fail_rate': fail_rate
            })
            
        # Sort by Failure Rate (Highest to Lowest), then by Total Failed
        stats.sort(key=lambda x: (x['fail_rate'], x['failed']), reverse=True)
        
        # Print Table Header
        header = f"| {'Package Name':<30} | {'Total Runs':<10} | {'Passed':<8} | {'Failed':<8} | {'Failure %':<10} |"
        print("-" * len(header))
        print(header)
        print("-" * len(header))
        
        # Print Table Rows
        for s in stats:
            fail_pct_str = f"{s['fail_rate']:.1f}%"
            # Highlight failures for better readability
            if s['failed'] > 0:
                fail_str = f"{s['failed']} ❌"
                fail_pct_str = f"{fail_pct_str} ⚠️"
            else:
                fail_str = str(s['failed'])
                
            row = f"| {s['pkg']:<30} | {s['total']:<10} | {s['passed']:<8} | {fail_str:<8} | {fail_pct_str:<10} |"
            print(row)
            
        print("-" * len(header))


# ==============================================================================
# 3. TEST LEVEL ANALYSIS (Incorporates package_bucket_analyzer.py)
# ==============================================================================

def analyze_test_level_logs(parent_dir):
    """Parses individual test logs to extract specific test failures."""
    results = {}
    target_folders = ['failed_package_logs', 'success_package_logs']
    files_processed = 0

    # Regex to match Go's test output lines and extract the test name
    test_pattern = re.compile(r'---\s+(PASS|FAIL|SKIP):\s+([^\s]+)')
    benchmark_pattern = re.compile(r'(Benchmark_[^\s\-]+)')


    for filepath in Path(parent_dir).rglob('*.txt'):
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
        
        # Initialize nested dictionaries
        if package_name not in results:
            results[package_name] = {}
        if bucket_type not in results[package_name]:
            results[package_name][bucket_type] = {}

        files_processed += 1
        has_parsed_tests = False
        has_failures = False

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "flags empty: no tests to run" in line:
                        has_parsed_tests = True
                        test_name = "<INCOMPATIBLE>"
                        if test_name not in results[package_name][bucket_type]:

                            results[package_name][bucket_type][test_name] = {'passed': 0, 'failed': 0, 'skipped': 0}
                        results[package_name][bucket_type][test_name]['skipped'] += 1
                        continue

                    test_match = test_pattern.search(line)

                    if test_match:
                        has_parsed_tests = True
                        status = test_match.group(1).upper()
                        test_name = test_match.group(2)
                        
                        if test_name not in results[package_name][bucket_type]:
                            results[package_name][bucket_type][test_name] = {'passed': 0, 'failed': 0, 'skipped': 0}
                            
                        if status == 'PASS':
                            results[package_name][bucket_type][test_name]['passed'] += 1
                        elif status == 'FAIL':
                            results[package_name][bucket_type][test_name]['failed'] += 1
                            has_failures = True
                        elif status == 'SKIP':
                            results[package_name][bucket_type][test_name]['skipped'] += 1
                        continue

                    bench_match = benchmark_pattern.search(line)
                    if bench_match:
                        has_parsed_tests = True
                        test_name = bench_match.group(1)
                        if test_name not in results[package_name][bucket_type]:
                            results[package_name][bucket_type][test_name] = {'passed': 0, 'failed': 0, 'skipped': 0}
                            
                        if log_type == 'success_package_logs':
                            results[package_name][bucket_type][test_name]['passed'] += 1
                        elif log_type == 'failed_package_logs':
                            results[package_name][bucket_type][test_name]['failed'] += 1
                            has_failures = True

                            
            # Edge Case: If the package log was dumped in the 'failed' directory but no specific 
            # '--- FAIL:' lines were printed, the test binary panicked, failed to compile, or timed out.
            if log_type == 'failed_package_logs' and not has_failures:
                dummy_name = "<PACKAGE_CRASH_OR_TIMEOUT>"
                if dummy_name not in results[package_name][bucket_type]:
                    results[package_name][bucket_type][dummy_name] = {'passed': 0, 'failed': 0, 'skipped': 0}
                results[package_name][bucket_type][dummy_name]['failed'] += 1

        except Exception as e:
            print(f"Error reading {filepath}: {e}")

    return results, files_processed


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

    # Iterate through packages alphabetically
    for package_name in sorted(results.keys()):
        # Iterate through bucket types (flat, hns) for the current package
        for bucket_type in sorted(results[package_name].keys()):
            tests = results[package_name][bucket_type]
            
            # Print the Header for this Package x Bucket combination
            print(f"\n" + "=" * 115)
            print(f" PACKAGE: {package_name.upper()} | BUCKET TYPE: {bucket_type.upper()} ".center(115, "="))
            print("=" * 115)
            
            stats = []
            for test_name, counts in tests.items():
                passed = counts['passed']
                failed = counts['failed']
                skipped = counts['skipped']
                
                total_exec = passed + failed
                fail_rate = (failed / total_exec * 100) if total_exec > 0 else 0
                
                stats.append({
                    'test': test_name,
                    'total_exec': total_exec,
                    'passed': passed,
                    'failed': failed,
                    'skipped': skipped,
                    'fail_rate': fail_rate
                })
            
            stats.sort(key=lambda x: (x['fail_rate'], x['failed']), reverse=True)
            
            # Dynamically calculate column width for test names
            max_name_len = max([len(s['test']) for s in stats] + [18])
            header = f"| {'Specific Test Name':<{max_name_len}} | {'Total':<7} | {'Passed':<6} | {'Failed':<8} | {'Skipped':<7} | {'Fail %':<8} |"
            print("-" * len(header))
            print(header)
            print("-" * len(header))
            
            for s in stats:
                fail_pct_str = f"{s['fail_rate']:.1f}%"
                
                if s['failed'] > 0:
                    fail_str = f"{s['failed']} ❌"
                    fail_pct_str = f"{fail_pct_str} ⚠️"
                else:
                    fail_str = str(s['failed'])
                    
                row = f"| {s['test']:<{max_name_len}} | {s['total_exec']:<7} | {s['passed']:<6} | {fail_str:<8} | {s['skipped']:<7} | {fail_pct_str:<8} |"
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
    script_dir = os.path.dirname(os.path.realpath(__file__))
    output_dir = os.path.join(script_dir, "output")


    # Step 1: Sync
    sync_success = sync_logs(args.release_bucket_name, args.release_version, args.vm_name_prefix, output_dir)

    if not sync_success:
        print("\n" + "!" * 80)
        print(" WARNING: Sync failed or was incomplete! Results below may be incomplete.".center(80))
        print("!" * 80 + "\n")


    # Step 2: Runtime Stats Analysis
    results_rt, count_rt = analyze_runtime_stats(output_dir)
    generate_runtime_report(results_rt, count_rt)

    # Step 3: Test Level Logs Analysis
    results_tl, count_tl = analyze_test_level_logs(output_dir)
    generate_test_level_report(results_tl, count_tl)


if __name__ == "__main__":
    main()
