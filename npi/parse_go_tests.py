#!/usr/bin/env python3
import sys
import json
import re
from datetime import datetime

def parse_log(log_path, gcsfuse_version, target_vm):
    raw_tests = []
    summary = {"total_tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    
    test_buffers = {}
    active_tests = set()
    
    # Regexes for test outcomes
    outcome_re = re.compile(r'^--- (PASS|FAIL|SKIP): (\S+) \((\d+(?:\.\d+)?)s\)')
    run_re = re.compile(r'^=== RUN\s+(\S+)')
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line_str = line.strip()
                
                # Check for RUN start
                run_match = run_re.match(line_str)
                if run_match:
                    test_name = run_match.group(1)
                    test_buffers[test_name] = []
                    active_tests.add(test_name)
                    continue
                
                # Capture log lines for all active tests
                for t in list(active_tests):
                    test_buffers[t].append(line)
                    
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
                    
                    if status == "FAIL":
                        # Join the captured buffer as the error log
                        test_entry["error"] = "".join(test_buffers.get(name, [])).strip()
                        
                    raw_tests.append(test_entry)
                    active_tests.discard(name)
                    
    except Exception as e:
        print(f"Error reading log file {log_path}: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Filter parent test suite outcomes from double counting.
    # A test is a parent suite if another test's name starts with `test_name + '/'`.
    parent_suites = {t['name'] for t in raw_tests if any(o['name'].startswith(t['name'] + '/') for o in raw_tests)}
    tests = [t for t in raw_tests if t['name'] not in parent_suites]

    summary["total_tests"] = len(tests)
    summary["passed"] = sum(1 for t in tests if t["status"] == "PASS")
    summary["failed"] = sum(1 for t in tests if t["status"] == "FAIL")
    summary["skipped"] = sum(1 for t in tests if t["status"] == "SKIP")

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
