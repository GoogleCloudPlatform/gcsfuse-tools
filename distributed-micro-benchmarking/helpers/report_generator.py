"""Report generation for benchmark results"""

import os
from tabulate import tabulate


def generate_report(metrics, output_file):
    """Generate concise benchmark report"""
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    
    headers = ["Test ID", "BS|FSize|IOD|IOType|Jobs|NrFiles", "Read BW (MB/s)", "Write BW (MB/s)", "Iter"]
    rows = []
    
    for test_id in sorted(metrics.keys()):
        m = metrics[test_id]
        params = m.get('test_params', {})
        param_str = format_params(params)
        
        rows.append([
            test_id,
            param_str,
            f"{m['read_bw_mbps']:.2f}" if m['read_bw_mbps'] > 0 else "-",
            f"{m['write_bw_mbps']:.2f}" if m['write_bw_mbps'] > 0 else "-",
            m['iterations']
        ])
    
    table = tabulate(rows, headers=headers, tablefmt="grid")
    
    # Print to console
    print("\n" + table)
    
    # Write to file
    with open(output_file, 'w') as f:
        f.write("Distributed Benchmark Results\n")
        f.write("=" * 80 + "\n\n")
        f.write(table)
        f.write("\n")
    
    print(f"Report saved to: {output_file}")


def format_params(params):
    """Format test parameters into compact string"""
    if not params:
        return "-"
    
    # Extract common FIO parameters
    parts = []
    for key in ['bs', 'file_size', 'iodepth', 'iotype', 'threads', 'nrfiles']:
        if key in params:
            parts.append(f"{params[key]}")
    
    return "|".join(parts) if parts else str(params)
