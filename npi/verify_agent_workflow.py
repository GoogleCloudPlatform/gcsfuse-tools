import os
import json
import sys

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
    if 'total_tests' not in summary:
        print("Error: 'total_tests' is missing in 'summary'.")
        return False
    
    total_tests = summary['total_tests']
    if total_tests != 2260:
        print(f"Error: Expected 'total_tests' to be 2260, but got {total_tests}.")
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

def main():
    base_dir = "/usr/local/google/home/kislayk/gitproj/gcsfuse-tools/npi"
    conformance_path = os.path.join(base_dir, "conformance_results.json")
    report_path = os.path.join(base_dir, "npi_validation_report.md")
    plan_path = os.path.join(base_dir, "npi_remediation_plan.md")

    conformance_ok = verify_conformance_results(conformance_path)
    
    report_headers = [
        "# GCSFuse NPI Validation Report",
        "## Executive Summary",
        "## Run Details",
        "## Performance Metrics Comparison"
    ]
    report_ok = check_file_headers(report_path, report_headers)

    plan_headers = [
        "# GCSFuse NPI Remediation Plan",
        "## Identified Issues & Gap Analysis",
        "## Recommended Remediation Steps"
    ]
    plan_ok = check_file_headers(plan_path, plan_headers)

    if conformance_ok and report_ok and plan_ok:
        print("VERIFICATION SUCCESSFUL: All NPI validation deliverables are present and valid.")
        sys.exit(0)
    else:
        print("VERIFICATION FAILED: Some checks failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
