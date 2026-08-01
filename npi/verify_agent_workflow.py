import os
import json
import sys
import glob
import re

def get_conformance_result_files(base_dir: str) -> list[str]:
    conformance_paths = []
    default_path = os.path.join(base_dir, "conformance_results.json")
    if os.path.exists(default_path):
        conformance_paths.append(default_path)

    scratch_regex = re.compile(r'[\._-](old|backup|tmp|temp|copy|draft|prev|orig|v\d+|test)(\.json$|$)', re.IGNORECASE)
    wildcard_files = []
    for f in sorted(glob.glob(os.path.join(base_dir, "conformance_results_*.json"))):
        basename = os.path.basename(f)
        if basename == "conformance_results.json":
            continue
        if scratch_regex.search(basename):
            continue
        wildcard_files.append(f)

    conformance_paths.extend(wildcard_files)
    return sorted(list(set(conformance_paths)))

def verify_conformance_results(file_path):
    print(f"Checking {file_path}...")
    if not os.path.exists(file_path):
        print(f"Error: {file_path} does not exist.")
        return False
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error: Failed to load {file_path} as JSON: {e}")
        return False

    if not isinstance(data, dict):
        print("Error: JSON root is not a dictionary.")
        return False

    if 'timestamp' not in data or not data['timestamp']:
        print("Error: 'timestamp' key is missing or empty in JSON.")
        return False
    if 'summary' not in data or not data['summary']:
        print("Error: 'summary' key is missing or empty in JSON.")
        return False
    if 'tests' not in data or not isinstance(data['tests'], list) or not data['tests']:
        print("Error: 'tests' key is missing, empty, or not a list in JSON.")
        return False

    summary = data['summary']
    if not isinstance(summary, dict):
        print("Error: 'summary' is not a dictionary.")
        return False
    if 'total_tests' not in summary:
        print("Error: 'total_tests' is missing in 'summary'.")
        return False
    
    total_tests = summary['total_tests']
    if not isinstance(total_tests, int):
        print("Error: 'total_tests' is not an integer.")
        return False
    if total_tests < 1:
        print(f"Error: Expected 'total_tests' to be at least 1, but got {total_tests}.")
        return False

    print(f"Success: {file_path} is valid.")
    return True

def check_file_headers(file_path, expected_headers):
    print(f"Checking {file_path} headers...")
    if not os.path.exists(file_path):
        print(f"Error: {file_path} does not exist.")
        return False
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error: Failed to read {file_path}: {e}")
        return False

    for header in expected_headers:
        if header not in content:
            print(f"Error: Expected header '{header}' was not found in {file_path}.")
            return False
            
    print(f"Success: {file_path} contains all required headers.")
    return True

def check_report_sections(report_path):
    print(f"Checking {report_path} for explicit Sequential Read, Random Read, and Write sections...")
    if not os.path.exists(report_path):
        return False
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error: Failed to read {report_path}: {e}")
        return False

    has_sequential = bool(re.search(r'Sequential Read|`read`', content, re.IGNORECASE))
    has_random = bool(re.search(r'Random Read|`randread`', content, re.IGNORECASE))
    has_write = bool(re.search(r'Write|`write`', content, re.IGNORECASE))

    if not has_sequential:
        print(f"Error: {report_path} is missing dedicated Sequential Read ('read') sections.")
        return False
    if not has_random:
        print(f"Error: {report_path} is missing dedicated Random Read ('randread') sections.")
        return False
    if not has_write:
        print(f"Error: {report_path} is missing dedicated Write ('write') sections.")
        return False

    print(f"Success: {report_path} includes distinct Sequential Read, Random Read, and Write sections.")
    return True

def verify_targets_config(targets_path):
    if not os.path.exists(targets_path):
        return True
    print(f"Checking {targets_path} schema...")
    try:
        with open(targets_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list) or len(data) == 0:
            print(f"Error: {targets_path} must be a non-empty list of target definitions.")
            return False
        for t in data:
            if not isinstance(t, dict) or "name" not in t or "type" not in t:
                print(f"Error: Target definition in {targets_path} missing 'name' or 'type'.")
                return False
        print(f"Success: {targets_path} configuration is valid.")
        return True
    except Exception as e:
        print(f"Error: Failed to validate {targets_path}: {e}")
        return False

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    conformance_paths = get_conformance_result_files(base_dir)
            
    report_path = os.path.join(base_dir, "npi_validation_report.md")
    plan_path = os.path.join(base_dir, "npi_remediation_plan.md")
    targets_path = os.path.join(base_dir, "targets.json")

    if not conformance_paths:
        print("Error: No conformance results JSON file found.")
        conformance_ok = False
    else:
        conformance_ok = all(verify_conformance_results(p) for p in conformance_paths)
    
    report_headers = [
        "# GCSFuse NPI Validation Report",
        "## Executive Summary",
        "## Run Details",
        "## Target Performance Results"
    ]
    report_ok = check_file_headers(report_path, report_headers) and check_report_sections(report_path)

    plan_headers = [
        "# GCSFuse NPI Remediation Plan",
        "## Identified Issues & Gap Analysis",
        "## Recommended Remediation Steps"
    ]
    plan_ok = check_file_headers(plan_path, plan_headers)
    targets_ok = verify_targets_config(targets_path)

    if conformance_ok and report_ok and plan_ok and targets_ok:
        print("VERIFICATION SUCCESSFUL: All NPI validation deliverables are present and valid.")
        sys.exit(0)
    else:
        print("VERIFICATION FAILED: Some checks failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
