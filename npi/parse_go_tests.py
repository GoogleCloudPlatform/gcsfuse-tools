#!/usr/bin/env python3
import sys
import json
import re
from datetime import datetime

def parse_log(log_path, gcsfuse_version, target_vm):
    tests = []
    summary = {"total_tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    
    current_test = None
    current_test_buffer = []
    
    # Regexes for test outcomes
    outcome_re = re.compile(r'^--- (PASS|FAIL|SKIP): (\S+) \((\d+\.\d+)s\)')
    run_re = re.compile(r'^=== RUN\s+(\S+)')
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line_str = line.strip()
                
                # Check for RUN start
                run_match = run_re.match(line_str)
                if run_match:
                    current_test = run_match.group(1)
                    current_test_buffer = []
                    continue
                
                # Capture log lines for the active test
                if current_test:
                    current_test_buffer.append(line)
                    
                # Check for outcome
                outcome_match = outcome_re.match(line_str)
                if outcome_match:
                    status = outcome_match.group(1)
                    name = outcome_match.group(2)
                    duration = float(outcome_match.group(3))
                    
                    test_entry = {
                        "name": name,
                        "status": status,
                        "duration_seconds": duration
                    }
                    
                    summary["total_tests"] += 1
                    if status == "PASS":
                        summary["passed"] += 1
                    elif status == "FAIL":
                        summary["failed"] += 1
                        # Join the captured buffer as the error log
                        test_entry["error"] = "".join(current_test_buffer).strip()
                    elif status == "SKIP":
                        summary["skipped"] += 1
                        
                    tests.append(test_entry)
                    current_test = None
                    current_test_buffer = []
                    
    except Exception as e:
        print(f"Error reading log file {log_path}: {e}", file=sys.stderr)
        sys.exit(1)
        
    report = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gcsfuse_version": gcsfuse_version,
        "target_vm": target_vm,
        "summary": summary,
        "tests": tests
    }
    
    return report

def main():
    if len(sys.argv) < 5:
        print("Usage: parse_go_tests.py <log_path> <gcsfuse_version> <target_vm> <output_json_path>", file=sys.stderr)
        sys.exit(1)
        
    log_path = sys.argv[1]
    gcsfuse_version = sys.argv[2]
    target_vm = sys.argv[3]
    output_path = sys.argv[4]
    
    report = parse_log(log_path, gcsfuse_version, target_vm)
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
        print(f"Successfully generated conformance report at {output_path}")
        print(f"Summary: Total: {report['summary']['total_tests']}, Passed: {report['summary']['passed']}, Failed: {report['summary']['failed']}, Skipped: {report['summary']['skipped']}")
    except Exception as e:
        print(f"Failed to write JSON output to {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
