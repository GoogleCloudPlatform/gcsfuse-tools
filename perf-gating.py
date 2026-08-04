import sys
import json
import subprocess
import re
import argparse
from datetime import datetime, timedelta

def bq_query(query):
    cmd = ["bq", "query", "--project_id=gcs-fuse-test-ml", "--use_legacy_sql=false", "--format=json", query]
    try:
        res = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return json.loads(res.decode('utf-8'))
    except subprocess.CalledProcessError as e:
        print(f"Error executing bq query: {e.output.decode('utf-8')}")
        sys.exit(1)

def parse_version(v_str):
    """Parses a version string into a tuple of integers."""
    m = re.match(r'^v?(\d+)\.(\d+)\.(\d+)$', v_str)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)

def get_minor_baselines(curr_ver, all_versions):
    """
    Determines baselines for a minor release:
    - Last 2 minor releases
    - Latest patch version of the last minor release
    """
    baselines = []
    baseline_timestamp_query_version = None
    
    minor_releases = [v for v in all_versions if v[0][2] == 0 and v[0] < curr_ver]
    
    if len(minor_releases) >= 1:
        prev_minor = minor_releases[-1]
        baselines.append(prev_minor[1])
        baseline_timestamp_query_version = prev_minor[1]
        
        # Find latest patch of the previous minor release
        patches_of_prev = [
            v for v in all_versions 
            if v[0][0] == prev_minor[0][0] and v[0][1] == prev_minor[0][1] and v[0][2] > 0
        ]
        if patches_of_prev:
            baselines.append(patches_of_prev[-1][1])
            
    if len(minor_releases) >= 2:
        baselines.append(minor_releases[-2][1])
        
    return baselines, baseline_timestamp_query_version

def get_patch_baselines(curr_ver, all_versions):
    """
    Determines baselines for a patch release:
    - Last 2 patch releases, or fallback to the minor version.
    """
    baselines = []
    
    # Minor versions (e.g., 3.9.0) will naturally be included in this filter
    patch_releases = [
        v for v in all_versions 
        if v[0][0] == curr_ver[0] and v[0][1] == curr_ver[1] and v[0] < curr_ver
    ]
    
    if len(patch_releases) >= 1:
        baselines.append(patch_releases[-1][1])
    if len(patch_releases) >= 2:
        baselines.append(patch_releases[-2][1])
        
    return baselines

def determine_baselines(curr_ver, all_versions):
    """
    Routes to the correct baseline strategy based on release type.
    Returns: (list of baseline version strings, the version string used for timestamp, use_daily_avg bool)
    """
    is_major = (curr_ver[1] == 0 and curr_ver[2] == 0)
    is_minor = (curr_ver[2] == 0 and not is_major)
    
    if is_major:
        baselines = []
        ts_version = None
        use_daily_avg = False
    elif is_minor:
        baselines, ts_version = get_minor_baselines(curr_ver, all_versions)
        use_daily_avg = True
    else:
        baselines = get_patch_baselines(curr_ver, all_versions)
        ts_version = None
        use_daily_avg = False
        
    return baselines, ts_version, use_daily_avg

def build_query(release_version, baselines, daily_avg_timestamp, use_daily_avg):
    """Builds the unified BigQuery SQL query."""
    daily_avg_query = ""
    if use_daily_avg:
        daily_avg_query = f"""
  UNION ALL
  SELECT 
    io_type, file_size, block_size, num_jobs, config, direct,
    CAST(write_bw_mbs AS FLOAT64) as write_bw_mbs,
    CAST(read_bw_mbs AS FLOAT64) as read_bw_mbs,
    'daily_avg' as source
  FROM `gcs-fuse-test-ml.periodic_benchmarks.kokoro_run_*`
  WHERE run_timestamp > '{daily_avg_timestamp}'
"""

    return f"""
WITH target_workloads AS (
  SELECT 
    io_type, file_size, block_size, num_jobs, config, direct,
    CAST(write_bw_mbs AS FLOAT64) as write_bw_mbs,
    CAST(read_bw_mbs AS FLOAT64) as read_bw_mbs,
    'current' as source
  FROM `gcs-fuse-test-ml.gcsfuse_release_performance_metrics.v*`
  WHERE release_version = '{release_version}'
  UNION ALL
  SELECT 
    io_type, file_size, block_size, num_jobs, config, direct,
    CAST(write_bw_mbs AS FLOAT64) as write_bw_mbs,
    CAST(read_bw_mbs AS FLOAT64) as read_bw_mbs,
    release_version as source
  FROM `gcs-fuse-test-ml.gcsfuse_release_performance_metrics.v*`
  WHERE release_version IN ({','.join(["'" + b + "'" for b in baselines]) if baselines else "'NONEXISTENT'"}){daily_avg_query}
)
SELECT 
  io_type, file_size, block_size, num_jobs, config, direct, source,
  AVG(IFNULL(write_bw_mbs, 0) + IFNULL(read_bw_mbs, 0)) as avg_bw
FROM target_workloads
WHERE io_type IN ('read', 'write')
  AND file_size IN ('1m', '1g') 
  AND block_size = '1m' 
  AND num_jobs = '48' 
  AND config IN ('http1', 'grpc') 
  AND direct = '0'
GROUP BY io_type, file_size, block_size, num_jobs, config, direct, source
"""

def evaluate_performance(data, baselines, threshold):
    """
    Evaluates the performance data and prints the output table.
    Returns True if gating fails (regression), False otherwise.
    """
    workloads = {}
    for row in data:
        w_key = f"{row['io_type']}_{row['file_size']}_{row['block_size']}_{row['num_jobs']}_{row['config']}_{str(row['direct'])}"
        if w_key not in workloads:
            workloads[w_key] = {}
        workloads[w_key][row['source']] = float(row['avg_bw'])

    failed = False

    print(f"{'Workload':<45} | {'Current':<10} | {'Daily Avg':<10} | {'Diff %':<8} | {'Baselines...'}")
    print("-" * 120)

    for w_key, sources in workloads.items():
        current = sources.get('current')
        if current is None:
            continue
        daily_avg = sources.get('daily_avg')
        
        diff_daily_pct = ((current - daily_avg) / daily_avg * 100) if daily_avg else 0
        
        baseline_strs = []
        for b in baselines:
            b_val = sources.get(b)
            if b_val:
                diff_b = ((current - b_val) / b_val * 100)
                baseline_strs.append(f"{b}: {b_val:.2f} ({diff_b:+.2f}%)")
                if diff_b < -threshold:
                    print(f"FAILED: {w_key} vs {b} baseline ({diff_b:+.2f}% is worse than -{threshold}%)")
                    failed = True
                    
        if daily_avg and diff_daily_pct < -threshold:
            print(f"FAILED: {w_key} vs daily_avg ({diff_daily_pct:+.2f}% is worse than -{threshold}%)")
            failed = True
            
        daily_str = f"{daily_avg:.2f}" if daily_avg else "N/A"
        diff_str = f"{diff_daily_pct:+.2f}%" if daily_avg else "N/A"
        
        print(f"{w_key:<45} | {current:<10.2f} | {daily_str:<10} | {diff_str:<8} | {' | '.join(baseline_strs)}")

    print("-" * 120)
    return failed

def fetch_all_versions():
    """Fetches and sorts all release versions available in BigQuery."""
    all_releases_query = "SELECT DISTINCT release_version FROM `gcs-fuse-test-ml.gcsfuse_release_performance_metrics.v*`"
    all_releases_data = bq_query(all_releases_query)
    
    all_versions = []
    for row in all_releases_data:
        v_str = row.get('release_version')
        if v_str:
            all_versions.append((parse_version(v_str), v_str))
            
    all_versions.sort(key=lambda x: x[0])
    return all_versions

def get_daily_avg_timestamp(baseline_timestamp_query_version, use_daily_avg):
    """Calculates the start timestamp for the daily average query (capped at 60 days)."""
    daily_avg_timestamp = "2026-05-01 00:00:00"
    if use_daily_avg:
        if baseline_timestamp_query_version:
            ts_query = f"SELECT run_timestamp FROM `gcs-fuse-test-ml.gcsfuse_release_performance_metrics.v*` WHERE release_version='{baseline_timestamp_query_version}' ORDER BY run_timestamp DESC LIMIT 1"
            ts_data = bq_query(ts_query)
            if ts_data and len(ts_data) > 0 and ts_data[0].get('run_timestamp'):
                daily_avg_timestamp = ts_data[0]['run_timestamp']
        
        # Enforce max 60 days sane limit
        sixty_days_ago = (datetime.utcnow() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        daily_avg_timestamp = max(daily_avg_timestamp, sixty_days_ago)
        
    return daily_avg_timestamp

def main():
    parser = argparse.ArgumentParser(description="Performance Gating trends generator.")
    parser.add_argument("--release-version", required=True, help="Target Release Version")
    parser.add_argument("--commit-hash", required=False, help="Commit hash (for logging/metadata)")
    parser.add_argument("--threshold", type=float, default=10.0, help="Threshold percentage for pass/fail (e.g., 10.0 for +/- 10%)")
    args = parser.parse_args()
    
    curr_ver = parse_version(args.release_version)
    print(f"Target commit: {args.commit_hash} (Release: {args.release_version})")
    
    print(f"Fetching all releases from BigQuery to determine baselines for {args.release_version}...")
    all_versions = fetch_all_versions()
    
    baselines, baseline_timestamp_query_version, use_daily_avg = determine_baselines(curr_ver, all_versions)
    
    if not baselines:
        print("No historical baselines found. This looks like a new release lineage!")
        
    daily_avg_timestamp = get_daily_avg_timestamp(baseline_timestamp_query_version, use_daily_avg)
            
    query = build_query(args.release_version, baselines, daily_avg_timestamp, use_daily_avg)
    print("Executing performance metric unified query...")
    data = bq_query(query)
    
    is_failed = evaluate_performance(data, baselines, args.threshold)
    if is_failed:
        print("Result: FAIL. Regression detected.")
        sys.exit(1)
    else:
        print(f"Result: PASS. All workloads within bounds.")
        sys.exit(0)
        
if __name__ == "__main__":
    main()
