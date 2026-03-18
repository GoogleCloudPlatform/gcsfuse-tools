#!/usr/bin/env python3
import json
import subprocess
import sys
from collections import defaultdict

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def get_progress_bar(success, pending, failed, total, length=10):
    if total == 0: return f"[{' ' * length}]"
    s_len = int(length * success / total)
    f_len = int(length * failed / total)
    p_len = length - s_len - f_len
    bar = (Colors.GREEN + '█' * s_len + Colors.RED + '█' * f_len + Colors.YELLOW + '░' * p_len + Colors.RESET)
    return f"[{bar}]"

# Configuration
BUCKET = "kokoro-perf-artifacts-bucket"
BENCHMARK_ID = "benchmark-1773808356-read"

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
                    "Direct": entry.get("direct", "?"),
                    "FileSize": entry.get("file_size", "?"),
                    "NrFiles": entry.get("nrfiles", "?"),
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
    print(f"\n{Colors.BOLD}--- VM SUMMARY ---{Colors.RESET}")
    print(f"{'VM Name':<40} | {'Progress':<19} | {'Status':<10} | {'Active Dir':<10} | {'Jobs (S/P/F)'}")
    print("-" * 105)
    for vm in sorted(vms):
        stats = vm_summaries.get(vm, {})
        
        vm_jobs = [j for j in job_data_by_id.values() if j['VM_Full'] == vm]
        t_count = len(vm_jobs)
        s_count = sum(1 for j in vm_jobs if j['Status'] == 'SUCCESS')
        p_count = sum(1 for j in vm_jobs if j['Status'] == 'PENDING')
        f_count = sum(1 for j in vm_jobs if j['Status'] == 'FAILED/RUNNING')
        
        tests_str = f"{s_count}/{t_count}"
        prog_bar = get_progress_bar(s_count, p_count, f_count, t_count, 10)
        status = stats.get("Status", "running")
        active_dir = stats.get("ActiveDir", "-")
        spf_str = f"{Colors.GREEN}{s_count}S{Colors.RESET} / {Colors.YELLOW}{p_count}P{Colors.RESET} / {Colors.RED}{f_count}F{Colors.RESET}"
        
        status_color = Colors.GREEN if status == "completed" else (Colors.RED if status == "failed" else Colors.YELLOW)
        colored_status = f"{status_color}{status:<10}{Colors.RESET}"
        
        print(f"{vm:<40} | {tests_str:<6} {prog_bar} | {colored_status} | {active_dir:<10} | {spf_str}")

    # =========================================================================
    # 4. Print the Detailed Job Distribution & Status Table
    # =========================================================================
    print(f"\n{Colors.BOLD}--- DETAILED JOB STATUS (GROUPED BY VM) ---{Colors.RESET}")
    
    jobs_by_vm = defaultdict(list)
    for job in job_data_by_id.values():
        jobs_by_vm[job['VM_Full']].append(job)
        
    for vm in sorted(vms):
        vm_jobs = sorted(jobs_by_vm[vm], key=lambda x: x["ID"])
        if not vm_jobs:
            continue
            
        vm_short = vm_jobs[0]['VM']
        print(f"\n{Colors.BOLD}➤ {vm} ({vm_short}){Colors.RESET}")
        
        single_threaded = [j for j in vm_jobs if str(j['Threads']) == '1']
        multi_threaded = [j for j in vm_jobs if str(j['Threads']) != '1']
        
        def print_jobs_section(jobs_list, title):
            if not jobs_list: return
            print(f"  {Colors.BOLD}{title}{Colors.RESET}")
            header = f"    {'ID':<4} | {'Threads':<8} | {'ReadType':<10} | {'Direct':<6} | {'BS':<6} | {'FileSize':<8} | {'Status'}"
            header = f"    {'ID':<4} | {'Threads':<8} | {'ReadType':<10} | {'Direct':<6} | {'BS':<6} | {'FileSize':<8} | {'NrFiles':<7} | {'Status'}"
            print(header)
            print("    " + "-" * 66)
            print("    " + "-" * 76)
            for item in jobs_list:
                st = item['Status']
                c_st = f"{Colors.GREEN}{st}{Colors.RESET}" if st == 'SUCCESS' else (f"{Colors.YELLOW}{st}{Colors.RESET}" if st == 'PENDING' else f"{Colors.RED}{st}{Colors.RESET}")
                print(f"    {item['ID']:<4} | {str(item['Threads']):<8} | {str(item['ReadType']):<10} | {str(item['Direct']):<6} | {str(item['BS']):<6} | {str(item['FileSize']):<8} | {c_st}")
                print(f"    {item['ID']:<4} | {str(item['Threads']):<8} | {str(item['ReadType']):<10} | {str(item['Direct']):<6} | {str(item['BS']):<6} | {str(item['FileSize']):<8} | {str(item['NrFiles']):<7} | {c_st}")

        print_jobs_section(single_threaded, "Single-Threaded Tests")
        if single_threaded and multi_threaded: print()
        print_jobs_section(multi_threaded, "Multi-Threaded Tests")

if __name__ == "__main__":
    main()