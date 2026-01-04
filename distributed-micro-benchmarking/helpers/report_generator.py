"""Report generation for benchmark results"""

import os
import csv
from tabulate import tabulate


def generate_report(metrics, output_file):
    """Generate benchmark report in CSV format and print as table"""
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    
    headers = ["Test ID", "BS|FSize|IOD|IOType|Jobs|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", "Avg CPU (%)", "Peak CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", "Iter"]
    rows = []
    
    for test_id in sorted(metrics.keys()):
        m = metrics[test_id]
        params = m.get('test_params', {})
        param_str = format_params(params)
        
        # Get resource metrics
        avg_cpu = params.get('avg_cpu', '-')
        peak_cpu = params.get('peak_cpu', '-')
        avg_mem = params.get('avg_mem_mb', '-')
        peak_mem = params.get('peak_mem_mb', '-')
        
        rows.append([
            test_id,
            param_str,
            f"{m['read_bw_mbps']:.2f}" if m['read_bw_mbps'] > 0 else "-",
            f"{m['write_bw_mbps']:.2f}" if m['write_bw_mbps'] > 0 else "-",
            avg_cpu,
            peak_cpu,
            avg_mem,
            peak_mem,
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


def format_params(params):
    """Format test parameters into compact string"""
    if not params:
        return "-"
    
    # Extract common FIO parameters (excluding resource metrics)
    parts = []
    for key in ['bs', 'file_size', 'io_depth', 'io_type', 'threads', 'nrfiles']:
        if key in params:
            parts.append(f"{params[key]}")
    
    return "|".join(parts) if parts else str(params)
