# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Report generation for benchmark results"""

import os
import csv
from tabulate import tabulate


def _extract_resource_metrics(params):
    """Extract resource metrics from params dict"""
    return {
        'avg_cpu': params.get('avg_cpu', '-'),
        'peak_cpu': params.get('peak_cpu', '-'),
        'avg_mem': params.get('avg_mem_mb', '-'),
        'peak_mem': params.get('peak_mem_mb', '-'),
        'avg_page_cache': params.get('avg_page_cache_gb', '-'),
        'peak_page_cache': params.get('peak_page_cache_gb', '-'),
        'avg_sys_cpu': params.get('avg_sys_cpu', '-'),
        'peak_sys_cpu': params.get('peak_sys_cpu', '-'),
        'avg_net_rx': params.get('avg_net_rx_mbps', '-'),
        'peak_net_rx': params.get('peak_net_rx_mbps', '-'),
        'avg_net_tx': params.get('avg_net_tx_mbps', '-'),
        'peak_net_tx': params.get('peak_net_tx_mbps', '-'),
    }


def _format_metric(value, default="-"):
    """Format a metric value with 2 decimal places or return default"""
    return f"{value:.2f}" if value > 0 else default


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
    """Generate a combined report with optional config columns"""
    
    # Determine headers based on mode
    base_headers = ["IOType|Jobs|FSize|BS|IOD|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", 
                    "Read Min (ms)", "Read Max (ms)", "Read Avg (ms)", "Read StdDev (ms)", 
                    "Read P50 (ms)", "Read P90 (ms)", "Read P99 (ms)", 
                    "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", 
                    "Avg PgCache (GB)", "Peak PgCache (GB)", "Avg Sys CPU (%)", "Peak Sys CPU (%)", 
                    "Avg Net RX (MB/s)", "Peak Net RX (MB/s)", "Avg Net TX (MB/s)", "Peak Net TX (MB/s)", "Iter"]
    
    if mode == "multi-config":
        headers = ["Matrix ID", "Test ID", "Config", "Commit"] + base_headers
    else:
        headers = ["Test ID"] + base_headers
    
    rows = []
    for test_key in sorted(metrics.keys()):
        m = metrics[test_key]
        params = m.get('test_params', {})
        resources = _extract_resource_metrics(params)
        
        # Build metric values
        metric_values = [
            format_params(params),
            _format_metric(m['read_bw_mbps']),
            _format_metric(m['write_bw_mbps']),
            _format_metric(m.get('read_lat_min_ms', 0)),
            _format_metric(m.get('read_lat_max_ms', 0)),
            _format_metric(m.get('read_lat_avg_ms', 0)),
            _format_metric(m.get('read_lat_stddev_ms', 0)),
            _format_metric(m.get('read_lat_p50_ms', 0)),
            _format_metric(m.get('read_lat_p90_ms', 0)),
            _format_metric(m.get('read_lat_p99_ms', 0)),
            resources['avg_cpu'], resources['peak_cpu'],
            resources['avg_mem'], resources['peak_mem'],
            resources['avg_page_cache'], resources['peak_page_cache'],
            resources['avg_sys_cpu'], resources['peak_sys_cpu'],
            resources['avg_net_rx'], resources['peak_net_rx'],
            resources['avg_net_tx'], resources['peak_net_tx'],
            m['iterations']
        ]
        
        if mode == "multi-config":
            config_label = params.get('config_label', '-')
            commit = params.get('commit', '-')
            matrix_id = m.get('matrix_id', test_key)
            test_id = m.get('test_id') or params.get('test_id', '-')
            rows.append([matrix_id, test_id, config_label, commit] + metric_values)
        else:
            rows.append([test_key] + metric_values)
    
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
    """Generate separate CSV reports per config"""
    
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
    
    headers = ["Test ID", "IOType|Jobs|FSize|BS|IOD|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)" 
               "Read P50 (ms)", "Read P90 (ms)", "Read P99 (ms)", "Read Max (ms)", 
               "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", 
               "Avg PgCache (GB)", "Peak PgCache (GB)", "Avg Sys CPU (%)", "Peak Sys CPU (%)", 
               "Avg Net RX (MB/s)", "Peak Net RX (MB/s)", "Avg Net TX (MB/s)", "Peak Net TX (MB/s)", "Iter"]
    
    for config_label, config_metrics in config_groups.items():
        output_file = os.path.join(base_dir, f"{base_name}_{config_label}.csv")
        rows = []
        
        for test_key in sorted(config_metrics.keys()):
            m = config_metrics[test_key]
            params = m.get('test_params', {})
            resources = _extract_resource_metrics(params)
            test_id = params.get('test_id', test_key)
            
            rows.append([
                test_id,
                format_params(params),
                _format_metric(m['read_bw_mbps']),
                _format_metric(m['write_bw_mbps']),
                _format_metric(m.get('read_lat_p50_ms', 0)),
                _format_metric(m.get('read_lat_p90_ms', 0)),
                _format_metric(m.get('read_lat_p99_ms', 0)),
                _format_metric(m.get('read_lat_max_ms', 0)),
                resources['avg_cpu'], resources['peak_cpu'],
                resources['avg_mem'], resources['peak_mem'],
                resources['avg_page_cache'], resources['peak_page_cache'],
                resources['avg_sys_cpu'], resources['peak_sys_cpu'],
                resources['avg_net_rx'], resources['peak_net_rx'],
                resources['avg_net_tx'], resources['peak_net_tx'],
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