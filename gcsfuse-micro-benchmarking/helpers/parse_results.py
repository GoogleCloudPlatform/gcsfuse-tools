import csv
import subprocess
import os
import tempfile
import shutil
import json
import fnmatch
import statistics
import datetime
from .vm_metrics import get_vm_cpu_utilization_points


def calculate_stats(data):
    if not data: return None, None
    return (data[0], 0.0) if len(data) == 1 else (statistics.fmean(data), statistics.stdev(data))


def process_fio_metrics_and_vm_metrics(fio_metrics, timestamps, vm_cfg):
    if len(fio_metrics) != len(timestamps): print("Mismatch in metrics/timestamps"); exit()
    
    def get_vals(d, m, scale=1.0):
        return [job[d][m]/scale for x in fio_metrics for job in x['jobs'] if d in job and m in job[d]]

    rep = {}
    for d in ['read', 'write']:
        for m, k, s in [('bw', 'throughput_mbps', 1000.0), ('lat_ns', 'latency_ms', 1e6), ('iops', 'iops', 1.0)]:
            avg, std = calculate_stats(get_vals(d, m, s))
            if avg is not None: rep[f'avg_{d}_{k}'], rep[f'stdev_{d}_{k}'] = avg, std

    vm_name, proj, zone = vm_cfg['instance_name'], vm_cfg['project'], vm_cfg['zone']
    cpus = []
    for ts in timestamps:
        try:
            st = datetime.datetime.strptime(ts['start_time'], "%Y-%m-%dT%H:%M:%S%z")
            et = datetime.datetime.strptime(ts['end_time'], "%Y-%m-%dT%H:%M:%S%z")
            pts = get_vm_cpu_utilization_points(vm_name, proj, zone, st, et)
            if pts: cpus.append(max(pts))
        except Exception as e: print(f"VM metrics error: {e}")

    avg_cpu, std_cpu = calculate_stats(cpus)
    vm_rep = {'avg_cpu_utilization_percent': avg_cpu * 100 if avg_cpu else None,
              'stdev_cpu_utilization_percent': std_cpu * 100 if std_cpu else None,
              'cpu_data_point_count': len(cpus)}

    final = {'fio_metrics': rep, 'vm_metrics': vm_rep}
    total_mbps = rep.get('avg_read_throughput_mbps', 0) + rep.get('avg_write_throughput_mbps', 0)
    avg_cpu_pct = vm_rep.get('avg_cpu_utilization_percent')
    
    final['cpu_percent_per_gbps'] = None
    if total_mbps > 0 and avg_cpu_pct is not None:
        gbps = total_mbps / 1000.0
        final['cpu_percent_per_gbps'] = (avg_cpu_pct / gbps) if gbps > 1e-9 else float('inf')
    return final


def download_artifacts_from_bucket(benchmark_id: str, artifacts_bucket: str):
    if not benchmark_id or not artifacts_bucket: raise ValueError("Missing ID or bucket")
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["gcloud", "storage", "cp", "-r", f"gs://{artifacts_bucket}/{benchmark_id}", tmp], check=True, capture_output=True, text=True)
        path = os.path.join(tmp, benchmark_id)
        if not os.path.isdir(path): raise FileNotFoundError(f"Folder not found: {path}")
        print(f"Artifacts downloaded to: {path}")
        return path
    except Exception: shutil.rmtree(tmp); raise


def clean_load_json_to_object(filepath: str):
    if not os.path.exists(filepath): return None
    try:
        with open(filepath, 'r') as f: lines = f.readlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith(('{', '[')): return json.loads("".join(lines[i:]))
    except Exception as e: print(f"JSON error {filepath}: {e}")
    return None


def process_fio_output_files(file_pattern, directory_path: str):
    if not os.path.isdir(directory_path): return []
    objs = []
    for f in os.listdir(directory_path):
        if fnmatch.fnmatch(f, file_pattern) and os.path.isfile(os.path.join(directory_path, f)):
            o = clean_load_json_to_object(os.path.join(directory_path, f))
            if o is not None: objs.append(o)
    return objs


def get_avg_perf_metrics_for_job(case, artifacts_dir, vm_cfg):
    raw = f"{artifacts_dir}/raw-results/fio_output_{case['bs']}_{case['file_size']}_{case['iodepth']}_{case['iotype']}_{case['threads']}_{case['nrfiles']}"
    fio_metrics = process_fio_output_files("fio_output_iter*.json", raw)
    with open(f"{raw}/timestamps.csv", 'r') as f: timestamps = list(csv.DictReader(f))
    metrics = process_fio_metrics_and_vm_metrics(fio_metrics, timestamps, vm_cfg)
    metrics.update({k: case[k] for k in ['bs', 'file_size', 'iodepth', 'iotype', 'threads', 'nrfiles']})
    return metrics


def parse_benchmark_results(benchmark_id, ARTIFACTS_BUCKET, cfg):
    vm_cfg = {'instance_name': cfg['bench_env']['gce_env']['vm_name'], 'zone': cfg['bench_env']['zone'], 'project': cfg['bench_env']['project']}
    artifacts = download_artifacts_from_bucket(benchmark_id, ARTIFACTS_BUCKET)
    with open(f'{artifacts}/fio_job_cases.csv', 'r') as f: testcases = list(csv.DictReader(f))
    metrics = {f"{tc['bs']}_{tc['file_size']}_{tc['iodepth']}_{tc['iotype']}_{tc['threads']}_{tc['nrfiles']}": get_avg_perf_metrics_for_job(tc, artifacts, vm_cfg) for tc in testcases}
    return artifacts, metrics
