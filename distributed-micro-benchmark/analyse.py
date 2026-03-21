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
BENCHMARK_ID = "benchmark-1774067490-read"

def run_cmd(cmd):
    """Executes a shell command and returns the output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""

def get_config_signature(io_type, num_jobs, file_size, block_size, io_depth, nr_files, direct):
    """Normalize the config values into a consistent tuple for comparison."""
    return (
        str(io_type).strip().lower(),
        str(num_jobs).strip(),
        str(file_size).strip().lower(),
        str(block_size).strip().lower(),
        str(io_depth).strip(),
        str(nr_files).strip(),
        str(direct).strip()
    )

def main():
    print(f"{Colors.BOLD}Analyzing Benchmark: {BENCHMARK_ID}{Colors.RESET}\n")
    
    # =========================================================================
    # 1. Fetch Source of Truth (Job definitions) from GCS
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
        vm_short = "mig-" + vm_full.split('-')[-1] if "-mig-" in vm_full else vm_full
        
        content = run_cmd(f"gcloud storage cat {line}")
        if not content: continue
        
        try:
            data = json.loads(content)
            entries = data.get("test_entries", [])
            for entry in entries:
                matrix_id = int(entry.get("matrix_id", 0))
                
                # Generate a unique signature for this configuration
                sig = get_config_signature(
                    entry.get("io_type", ""),
                    entry.get("num_jobs", ""),
                    entry.get("file_size", ""),
                    entry.get("block_size", ""),
                    entry.get("io_depth", ""),
                    entry.get("nr_files", entry.get("nrfiles", "")),
                    entry.get("direct", "")
                )

                job_data_by_id[matrix_id] = {
                    "VM": vm_short,
                    "VM_Full": vm_full,
                    "ID": matrix_id,
                    "BS": entry.get("block_size", "?"),
                    "Threads": entry.get("num_jobs", "?"),
                    "ReadType": entry.get("io_type", "?"),
                    "Direct": entry.get("direct", "?"),
                    "FileSize": entry.get("file_size", "?"),
                    "NrFiles": entry.get("nr_files", entry.get("nrfiles", "?")),
                    "IoDepth": entry.get("io_depth", "?"),
                    "Protocol": entry.get("config_label"),
                    "Signature": sig,
                    "Duration": "-",
                    "Status": "PENDING"
                }
                vm_assignments[vm_full] += 1
        except Exception as e:
            print(f"Warning: Failed to parse JSON from {line}: {e}", file=sys.stderr)
            continue

    # =========================================================================
    # 2. Fetch Statuses from fio_durations.csv & Calculate VM Summaries
    # =========================================================================
    vm_summaries = {}
    
    for vm in sorted(vms):
        # Fetch the manifest to know if the VM is generally completely done or not
        manifest_json = run_cmd(f"gcloud storage cat gs://{BUCKET}/{BENCHMARK_ID}/results/{vm}/manifest.json")
        manifest = json.loads(manifest_json) if manifest_json else None
        vm_overall_status = manifest.get('status', 'running') if manifest else "running"
        
        # Download and parse the fio_durations.csv which only contains SUCCESSFUL configs
        csv_path = f"gs://{BUCKET}/{BENCHMARK_ID}/results/{vm}/fio_durations.csv"
        csv_content = run_cmd(f"gcloud storage cat {csv_path}")
        
        completed_signatures = {}
        if csv_content:
            lines = csv_content.splitlines()
            for line in lines[1:]: # Skip the header
                if not line.strip(): continue
                parts = [p.strip() for p in line.split(',')]
                # First 7 columns: io_type,num_jobs,file_size,block_size,io_depth,nr_files,direct
                if len(parts) >= 7:
                    sig = get_config_signature(*parts[0:7])
                    durations = ", ".join([p.replace("sec", "s") for p in parts[7:]])
                    completed_signatures[sig] = durations

        # Calculate Summary Stats & Update Job Details
        done_count = 0
        vm_jobs = [j for j in job_data_by_id.values() if j['VM_Full'] == vm]
        
        for job in vm_jobs:
            if job["Signature"] in completed_signatures:
                job["Status"] = "SUCCESS"
                job["Duration"] = completed_signatures[job["Signature"]]
                done_count += 1
            else:
                # If missing from CSV and the VM finished executing, it definitely failed/timed out
                if vm_overall_status in ["completed", "failed", "cancelled"]:
                    job["Status"] = "FAILED/TIMEOUT"
                else:
                    job["Status"] = "RUNNING/PENDING"
                job["Duration"] = "-"
                
        # Determine overall VM Status
        if done_count == vm_assignments[vm] and vm_assignments[vm] > 0:
            vm_overall_status = "completed"
            
        vm_summaries[vm] = {
            "Completed": done_count,
            "Status": vm_overall_status
        }

    # =========================================================================
    # 3. Print the VM Pass/Fail Summary Table
    # =========================================================================
    print(f"\n{Colors.BOLD}--- VM SUMMARY ---{Colors.RESET}")
    print(f"{'VM Name':<40} | {'Progress':<19} | {'Status':<10} | {'Jobs (S/P/F)'}")
    print("-" * 92)
    for vm in sorted(vms):
        stats = vm_summaries.get(vm, {})
        
        vm_jobs = [j for j in job_data_by_id.values() if j['VM_Full'] == vm]
        t_count = len(vm_jobs)
        s_count = sum(1 for j in vm_jobs if j['Status'] == 'SUCCESS')
        p_count = sum(1 for j in vm_jobs if j['Status'] == 'RUNNING/PENDING')
        f_count = sum(1 for j in vm_jobs if j['Status'] == 'FAILED/TIMEOUT')
        
        tests_str = f"{s_count}/{t_count}"
        prog_bar = get_progress_bar(s_count, p_count, f_count, t_count, 10)
        status = stats.get("Status", "running")
        spf_str = f"{Colors.GREEN}{s_count}S{Colors.RESET} / {Colors.YELLOW}{p_count}P{Colors.RESET} / {Colors.RED}{f_count}F{Colors.RESET}"
        
        status_color = Colors.GREEN if status == "completed" else (Colors.RED if status == "failed" else Colors.YELLOW)
        colored_status = f"{status_color}{status:<10}{Colors.RESET}"
        
        print(f"{vm:<40} | {tests_str:<6} {prog_bar} | {colored_status} | {spf_str}")

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
            header = f"    {'ID':<4} | {'Protocol':<8} | {'Threads':<8} | {'ReadType':<10} | {'Direct':<6} | {'BS':<6} | {'FileSize':<8} | {'NrFiles':<7} | {'IoDepth':<7} | {'Status':<15} | {'Duration'}"
            print("    " + "-" * 120)
            print(header)
            print("    " + "-" * 120)
            for item in jobs_list:
                st = item['Status']
                padded_st = f"{st:<15}"
                if st == 'SUCCESS':
                    c_st = f"{Colors.GREEN}{padded_st}{Colors.RESET}"
                elif st == 'RUNNING/PENDING':
                    c_st = f"{Colors.YELLOW}{padded_st}{Colors.RESET}"
                else:
                    c_st = f"{Colors.RED}{padded_st}{Colors.RESET}"
                print(f"    {item['ID']:<4} | {str(item['Protocol']):<8} | {str(item['Threads']):<8} | {str(item['ReadType']):<10} | {str(item['Direct']):<6} | {str(item['BS']):<6} | {str(item['FileSize']):<8} | {str(item['NrFiles']):<7} | {str(item['IoDepth']):<7} | {c_st} | {item['Duration']}")

        print_jobs_section(single_threaded, "Single-Threaded Tests")
        if single_threaded and multi_threaded: print()
        print_jobs_section(multi_threaded, "Multi-Threaded Tests")

if __name__ == "__main__":
    main()