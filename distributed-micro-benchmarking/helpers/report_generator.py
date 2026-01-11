"""Report generation for benchmark results"""

import os
import csv
from tabulate import tabulate


def generate_report(metrics, output_file, mode="single-config", separate_configs=False):
    """Generate benchmark report in CSV format and print as table"""
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    
    if mode == "multi-config" and separate_configs:
        # Generate separate reports per config
        generate_separate_reports(metrics, output_file)
    else:
        # Generate combined report
        generate_combined_report(metrics, output_file, mode)


def generate_combined_report(metrics, output_file, mode):
    """Generate a combined report with optional config columns.
    
    In multi-config mode, adds columns for Matrix ID, Test ID, Config, and Commit.
    In single-config mode, only includes Test ID and test parameters.
    
    Writes CSV file and prints formatted table to console.
    """
    
    # Determine headers based on mode
    if mode == "multi-config":
        headers = ["Matrix ID", "Test ID", "Config", "Commit", "IOType|Jobs|FSize|BS|IOD|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", "Read Min (ms)", "Read Max (ms)", "Read Avg (ms)", "Read StdDev (ms)", "Read P50 (ms)", "Read P90 (ms)", "Read P99 (ms)", "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", "Avg PgCache (GB)", "Peak PgCache (GB)", "Avg Sys CPU (%)", "Peak Sys CPU (%)", "Iter"]
    else:
        headers = ["Test ID", "IOType|Jobs|FSize|BS|IOD|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", "Read Min (ms)", "Read Max (ms)", "Read Avg (ms)", "Read StdDev (ms)", "Read P50 (ms)", "Read P90 (ms)", "Read P99 (ms)", "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", "Avg PgCache (GB)", "Peak PgCache (GB)", "Avg Sys CPU (%)", "Peak Sys CPU (%)", "Iter"]
    
    rows = []
    
    for test_key in sorted(metrics.keys()):
        m = metrics[test_key]
        params = m.get('test_params', {})
        param_str = format_params(params)
        
        # Get resource metrics
        avg_cpu = params.get('avg_cpu', '-')
        peak_cpu = params.get('peak_cpu', '-')
        avg_mem = params.get('avg_mem_mb', '-')
        peak_mem = params.get('peak_mem_mb', '-')
        avg_page_cache = params.get('avg_page_cache_gb', '-')
        peak_page_cache = params.get('peak_page_cache_gb', '-')
        avg_sys_cpu = params.get('avg_sys_cpu', '-')
        peak_sys_cpu = params.get('peak_sys_cpu', '-')
        
        if mode == "multi-config":
            # Extract config info
            config_label = params.get('config_label', '-')
            commit = params.get('commit', '-')
            matrix_id = m.get('matrix_id', test_key)
            # Get test_id from either the metric dict or params
            test_id = m.get('test_id') or params.get('test_id', '-')
            
            rows.append([
                matrix_id,
                test_id,
                config_label,
                commit,
                param_str,
                f"{m['read_bw_mbps']:.2f}" if m['read_bw_mbps'] > 0 else "-",
                f"{m['write_bw_mbps']:.2f}" if m['write_bw_mbps'] > 0 else "-",
                f"{m['read_lat_min_ms']:.2f}" if m.get('read_lat_min_ms', 0) > 0 else "-",
                f"{m['read_lat_max_ms']:.2f}" if m.get('read_lat_max_ms', 0) > 0 else "-",
                f"{m['read_lat_avg_ms']:.2f}" if m.get('read_lat_avg_ms', 0) > 0 else "-",
                f"{m['read_lat_stddev_ms']:.2f}" if m.get('read_lat_stddev_ms', 0) > 0 else "-",
                f"{m['read_lat_p50_ms']:.2f}" if m.get('read_lat_p50_ms', 0) > 0 else "-",
                f"{m['read_lat_p90_ms']:.2f}" if m.get('read_lat_p90_ms', 0) > 0 else "-",
                f"{m['read_lat_p99_ms']:.2f}" if m.get('read_lat_p99_ms', 0) > 0 else "-",
                avg_cpu,
                peak_cpu,
                avg_mem,
                peak_mem,
                avg_page_cache,
                peak_page_cache,
                avg_sys_cpu,
                peak_sys_cpu,
                m['iterations']
            ])
        else:
            rows.append([
                test_key,
                param_str,
                f"{m['read_bw_mbps']:.2f}" if m['read_bw_mbps'] > 0 else "-",
                f"{m['write_bw_mbps']:.2f}" if m['write_bw_mbps'] > 0 else "-",
                f"{m['read_lat_min_ms']:.2f}" if m.get('read_lat_min_ms', 0) > 0 else "-",
                f"{m['read_lat_max_ms']:.2f}" if m.get('read_lat_max_ms', 0) > 0 else "-",
                f"{m['read_lat_avg_ms']:.2f}" if m.get('read_lat_avg_ms', 0) > 0 else "-",
                f"{m['read_lat_stddev_ms']:.2f}" if m.get('read_lat_stddev_ms', 0) > 0 else "-",
                f"{m['read_lat_p50_ms']:.2f}" if m.get('read_lat_p50_ms', 0) > 0 else "-",
                f"{m['read_lat_p90_ms']:.2f}" if m.get('read_lat_p90_ms', 0) > 0 else "-",
                f"{m['read_lat_p99_ms']:.2f}" if m.get('read_lat_p99_ms', 0) > 0 else "-",
                avg_cpu,
                peak_cpu,
                avg_mem,
                peak_mem,
                avg_page_cache,
                peak_page_cache,
                avg_sys_cpu,
                peak_sys_cpu,
                m['iterations']
            ])
    
    # Write to CSV file
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    
    # Print table to console
    table = tabulate(rows, headers=headers, tablefmt="grid")
    print("\n" + table)
    print(f"\nReport saved to: {output_file}")


def generate_separate_reports(metrics, base_output_file):
    """Generate separate CSV reports per config.
    
    Groups metrics by config_label and creates one CSV file per config.
    Output files named: <base_name>_<config_label>.csv
    
    Useful for comparing test cases within a single config without clutter from other configs.
    """
    
    # Group metrics by config
    config_groups = {}
    for test_key, m in metrics.items():
        params = m.get('test_params', {})
        config_label = params.get('config_label', 'unknown')
        
        if config_label not in config_groups:
            config_groups[config_label] = {}
        config_groups[config_label][test_key] = m
    
    # Generate report for each config
    base_dir = os.path.dirname(base_output_file)
    base_name = os.path.splitext(os.path.basename(base_output_file))[0]
    
    for config_label, config_metrics in config_groups.items():
        output_file = os.path.join(base_dir, f"{base_name}_{config_label}.csv")
        
        headers = ["Test ID", "BS|FSize|IOD|IOType|Jobs|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", "Read P50 (ms)", "Read P90 (ms)", "Read P99 (ms)", "Read Max (ms)", "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", "Avg PgCache (GB)", "Peak PgCache (GB)", "Avg Sys CPU (%)", "Peak Sys CPU (%)", "Iter"]
        rows = []
        
        for test_key in sorted(config_metrics.keys()):
            m = config_metrics[test_key]
            params = m.get('test_params', {})
            param_str = format_params(params)
            test_id = params.get('test_id', test_key)
            
            # Get resource metrics
            avg_cpu = params.get('avg_cpu', '-')
            peak_cpu = params.get('peak_cpu', '-')
            avg_mem = params.get('avg_mem_mb', '-')
            peak_mem = params.get('peak_mem_mb', '-')
            avg_page_cache = params.get('avg_page_cache_gb', '-')
            peak_page_cache = params.get('peak_page_cache_gb', '-')
            avg_sys_cpu = params.get('avg_sys_cpu', '-')
            peak_sys_cpu = params.get('peak_sys_cpu', '-')
            
            rows.append([
                test_id,
                param_str,
                f"{m['read_bw_mbps']:.2f}" if m['read_bw_mbps'] > 0 else "-",
                f"{m['write_bw_mbps']:.2f}" if m['write_bw_mbps'] > 0 else "-",
                f"{m['read_lat_p50_ms']:.2f}" if m.get('read_lat_p50_ms', 0) > 0 else "-",
                f"{m['read_lat_p90_ms']:.2f}" if m.get('read_lat_p90_ms', 0) > 0 else "-",
                f"{m['read_lat_p99_ms']:.2f}" if m.get('read_lat_p99_ms', 0) > 0 else "-",
                f"{m['read_lat_max_ms']:.2f}" if m.get('read_lat_max_ms', 0) > 0 else "-",
                avg_cpu,
                peak_cpu,
                avg_mem,
                peak_mem,
                avg_page_cache,
                peak_page_cache,
                avg_sys_cpu,
                peak_sys_cpu,
                m['iterations']
            ])
        
        # Write to CSV file
        with open(output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        
        print(f"Report for config '{config_label}' saved to: {output_file}")


def format_params(params):
    """Format test parameters into compact string"""
    if not params:
        return "-"
    
    # Extract common FIO parameters (excluding resource metrics and config info)
    # Order: io_type, threads, file_size, block_size, io_depth, nr_files
    parts = []
    for key in ['io_type', 'threads', 'file_size', 'bs', 'io_depth', 'nrfiles']:
        if key in params:
            parts.append(f"{params[key]}")
    
    return "|".join(parts) if parts else str(params)
