#!/usr/bin/env python3
import json
import subprocess
import sys
from collections import defaultdict

# Configuration
BUCKET = "kokoro-perf-artifacts-bucket"
BENCHMARK_ID = "benchmark-1772947897-read"

def run_cmd(cmd):
    """Executes a shell command and returns the output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""

def get_test_status(vm, test_id):
    """Determines if a test finished by checking for the FIO output file."""
    path = f"gs://{BUCKET}/{BENCHMARK_ID}/results/{vm}/test-{test_id}/"
    files = run_cmd(f"gcloud storage ls {path}")
    if not files:
        return "PENDING"
    if "fio_output" in files:
        return "SUCCESS"
    return "FAILED/RUNNING"

def main():
    print(f"Analyzing Benchmark: {BENCHMARK_ID}\n")
    
    # =========================================================================
    # 1. Fetch Source of Truth (Job definitions) to eliminate '?' marks
    # =========================================================================
    jobs_path = f"gs://{BUCKET}/{BENCHMARK_ID}/jobs/*.json"
    files_output = run_cmd(f"gcloud storage ls {jobs_path}")
    
    job_data_by_id = {}
    vm_assignments = defaultdict(int)
    vms = []

    if not files_output:
        print(f"No job assignment files found in {jobs_path}")
        return

    for line in files_output.splitlines():
        if not line: continue
        
        vm_full = line.split('/')[-1].replace('.json', '')
        vms.append(vm_full)
        vm_short = "mig-" + vm_full.split('-')[-1]
        
        content = run_cmd(f"gcloud storage cat {line}")
        if not content: continue
        
        try:
            data = json.loads(content)
            entries = data.get("test_entries", [])
            for entry in entries:
                # Use matrix_id as the primary key
                matrix_id = int(entry.get("matrix_id", 0))
                job_data_by_id[matrix_id] = {
                    "VM": vm_short,
                    "VM_Full": vm_full,
                    "ID": matrix_id,
                    "BS": entry.get("block_size", "?"),
                    "Threads": entry.get("num_jobs", "?"),
                    "ReadType": entry.get("io_type", "?"),
                    "Status": "PENDING"
                }
                vm_assignments[vm_full] += 1
        except Exception:
            continue

    # =========================================================================
    # 2. Fetch Statuses & Calculate VM Summaries
    # =========================================================================
    vm_summaries = {}
    
    for vm in sorted(vms):
        # Load Manifest for VM Status
        manifest_json = run_cmd(f"gcloud storage cat gs://{BUCKET}/{BENCHMARK_ID}/results/{vm}/manifest.json")
        manifest = json.loads(manifest_json) if manifest_json else None
        
        # Discover physical folders in GCS
        res_path = f"gs://{BUCKET}/{BENCHMARK_ID}/results/{vm}/"
        raw_output = run_cmd(f"gcloud storage ls {res_path}")
        
        # Safely extract folder IDs (converting them to integers to match matrix_id)
        found_test_ids = []
        for l in raw_output.splitlines():
            if 'test-' in l:
                try:
                    folder_name = [part for part in l.split('/') if part.startswith('test-')][-1]
                    tid = int(folder_name.replace('test-', ''))
                    if tid not in found_test_ids:
                        found_test_ids.append(tid)
                except ValueError:
                    continue
        
        found_test_ids.sort()
        last_dir = f"test-{found_test_ids[-1]}" if found_test_ids else "None"
        
        # Calculate Summary Stats & Update Job Details
        done_count = 0
        for tid in found_test_ids:
            status = get_test_status(vm, tid)
            if status == "SUCCESS":
                done_count += 1
            
            # Map the status back to our detailed dictionary using matrix_id
            if tid in job_data_by_id:
                job_data_by_id[tid]["Status"] = status
                
        # Determine overall VM Status
        vm_status = manifest.get('status', 'running') if manifest else ("failed" if found_test_ids else "running")
        if done_count == vm_assignments[vm] and vm_assignments[vm] > 0:
            vm_status = "completed"
            
        vm_summaries[vm] = {
            "Completed": done_count,
            "Status": vm_status,
            "ActiveDir": last_dir
        }

    # =========================================================================
    # 3. Print the VM Pass/Fail Summary Table
    # =========================================================================
    print(f"{'VM Name':<45} | {'Tests':<10} | {'Status':<10} | {'Active Dir'}")
    print("-" * 85)
    for vm in sorted(vms):
        stats = vm_summaries.get(vm, {})
        total_assigned = vm_assignments.get(vm, 0)
        tests_str = f"{stats.get('Completed', 0)}/{total_assigned}"
        status = stats.get("Status", "running")
        active_dir = stats.get("ActiveDir", "-")
        print(f"{vm:<45} | {tests_str:<10} | {status:<10} | {active_dir}")

    # =========================================================================
    # 4. Print the Detailed Job Distribution & Status Table
    # =========================================================================
    print("\n--- DETAILED JOB STATUS ---")
    header = f"{'VM (Short)':<12} | {'ID':<4} | {'BS':<8} | {'Threads':<8} | {'ReadType':<10} | {'Status'}"
    print(header)
    print("-" * len(header))
    
    # Sort by ID
    sorted_jobs = sorted(job_data_by_id.values(), key=lambda x: x["ID"])
    
    for item in sorted_jobs:
        print(f"{item['VM']:<12} | {item['ID']:<4} | {item['BS']:<8} | {item['Threads']:<8} | {item['ReadType']:<10} | {item['Status']}")

if __name__ == "__main__":
    main()