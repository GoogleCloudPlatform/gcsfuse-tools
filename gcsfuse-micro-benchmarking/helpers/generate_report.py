import json
from tabulate import tabulate
import os


def pretty_print_metrics_table(metrics, output_file=None):
    """
    Prints the metrics dictionary in a fancy table format to the console
    and optionally appends it to a file.

    Args:
        metrics: A dictionary where keys are test case identifiers
                 and values are dictionaries containing test case parameters
                 and nested 'fio_metrics', 'vm_metrics', etc.
        output_file: (Optional) Path to a file where the table output
                     will be appended.
    """
    if not metrics:
        print("Metrics dictionary is empty.")
        return

    # Define the headers for the table, including IOPS and CPU metrics.
    headers = [
        # Test Case Parameters
        "bs", "file_size", "iodepth", "iotype", "threads", "nrfiles",
        # Metrics from 'fio_metrics'
        "Read BW (MB/s)", "Read Lat (ms)", "Read IOPS", "Write BW (MB/s)",
        "Write Lat (ms)", "Write IOPS",
        # Other top-level metrics
        "Avg CPU %", "Stdev CPU %", "CPU % / Gbps"
    ]

    table_data = []
    # Sort the dictionary keys to ensure consistent row order.
    for key in sorted(metrics.keys()):
        value = metrics[key]
        if not isinstance(value, dict):
            print(f"Warning: Skipping key '{key}' as its value is not a dictionary.")
            continue

        row = []

        # Extract test case parameters from the top level of the value
        row.append(value.get("bs", "-"))
        row.append(value.get("file_size", "-"))
        row.append(value.get("iodepth", "-"))
        row.append(value.get("iotype", "-"))
        row.append(value.get("threads", "-"))
        row.append(value.get("nrfiles", "-"))

        # Extract metrics from the nested 'fio_metrics' dictionary
        fio_metrics = value.get("fio_metrics", {})
        row.append(fio_metrics.get("avg_read_throughput_mbps", "-"))

        read_lat_ms = fio_metrics.get("avg_read_latency_ms")
        row.append(f"{read_lat_ms:.2f}" if isinstance(read_lat_ms, (int, float)) else "-")

        read_iops = fio_metrics.get("avg_read_iops")
        row.append(f"{read_iops:.2f}" if isinstance(read_iops, (int, float)) else "-")

        row.append(fio_metrics.get("avg_write_throughput_mbps", "-"))

        write_lat_ms = fio_metrics.get("avg_write_latency_ms")
        row.append(f"{write_lat_ms:.2f}" if isinstance(write_lat_ms, (int, float)) else "-")

        write_iops = fio_metrics.get("avg_write_iops")
        row.append(f"{write_iops:.2f}" if isinstance(write_iops, (int, float)) else "-")

        # Extract metrics from the nested 'vm_metrics' dictionary
        vm_metrics = value.get("vm_metrics", {})

        avg_cpu = vm_metrics.get("avg_cpu_utilization_percent")
        row.append(f"{avg_cpu:.2f}" if isinstance(avg_cpu, (int, float)) else "-")

        stdev_cpu = vm_metrics.get("stdev_cpu_utilization_percent")
        row.append(f"{stdev_cpu:.2f}" if isinstance(stdev_cpu, (int, float)) else "-")

        # Extract top-level 'cpu_percent_per_gbps' metric
        cpu_per_gbps = value.get("cpu_percent_per_gbps")
        row.append(f"{cpu_per_gbps:.4f}" if isinstance(cpu_per_gbps, (int, float)) else cpu_per_gbps if cpu_per_gbps is not None else "-")

        table_data.append(row)

    if not table_data:
        print("No data to display in table.")
        return

    # Generate the table string
    table_string = tabulate(table_data, headers=headers, tablefmt="grid", floatfmt=".2f")

    # Print to the terminal
    print(table_string)

    # Write to the file if specified
    if output_file:
        try:
            with open(output_file, 'a') as f:
                f.write("\n--- Metrics Table ---\n")
                f.write(table_string)
                f.write("\n\n")
            print(f"\nBenchmark results saved to: {output_file}")
        except Exception as e:
            print(f"\nError writing to file {output_file}: {e}")


def pretty_print_metrics_table_concise(metrics, output_file=None):
    """
    Prints the metrics dictionary in a concise table format showing only bandwidth.
    Concatenates test parameters in a single column.

    Args:
        metrics: A dictionary where keys are test case identifiers
                 and values are dictionaries containing test case parameters
                 and nested 'fio_metrics', etc.
        output_file: (Optional) Path to a file where the table output
                     will be appended.
    """
    if not metrics:
        print("Metrics dictionary is empty.")
        return

    # Define the headers for the concise table
    headers = [
        "Test Case",
        "Read BW (MB/s)",
        "Write BW (MB/s)"
    ]

    table_data = []
    # Sort the dictionary keys to ensure consistent row order.
    for key in sorted(metrics.keys()):
        value = metrics[key]
        if not isinstance(value, dict):
            print(f"Warning: Skipping key '{key}' as its value is not a dictionary.")
            continue

        row = []

        # Concatenate test case parameters
        bs = value.get("bs", "-")
        file_size = value.get("file_size", "-")
        iodepth = value.get("iodepth", "-")
        iotype = value.get("iotype", "-")
        threads = value.get("threads", "-")
        nrfiles = value.get("nrfiles", "-")
        
        test_case_str = f"{bs}|{file_size}|io{iodepth}|{iotype}|t{threads}|n{nrfiles}"
        row.append(test_case_str)

        # Extract bandwidth from the nested 'fio_metrics' dictionary
        fio_metrics = value.get("fio_metrics", {})
        
        read_bw = fio_metrics.get("avg_read_throughput_mbps", 0)
        row.append(f"{read_bw:.2f}" if isinstance(read_bw, (int, float)) and read_bw > 0 else "-")

        write_bw = fio_metrics.get("avg_write_throughput_mbps", 0)
        row.append(f"{write_bw:.2f}" if isinstance(write_bw, (int, float)) and write_bw > 0 else "-")

        table_data.append(row)

    if not table_data:
        print("No data to display in table.")
        return

    # Generate the table string
    table_string = tabulate(table_data, headers=headers, tablefmt="grid", floatfmt=".2f")

    # Print to the terminal
    print(table_string)

    # Write to the file if specified
    if output_file:
        try:
            with open(output_file, 'a') as f:
                f.write("\n--- Concise Metrics Table ---\n")
                f.write(table_string)
                f.write("\n\n")
            print(f"\nBenchmark results saved to: {output_file}")
        except Exception as e:
            print(f"\nError writing to file {output_file}: {e}")


# Example usage with your new metrics structure:
if __name__ == '__main__':
    metrics = {
        '4KB_1MB_1_read_1_1': {
            'fio_metrics': {
                'avg_read_throughput_kibps': 3172.0,
                'stdev_read_throughput_kibps': 63.6396,
                'avg_write_throughput_kibps': 0.0,
                'stdev_write_throughput_kibps': 0.0,
                'avg_read_latency_ns': 2705.68,
                'stdev_read_latency_ns': 225.296,
                'avg_write_latency_ns': 0.0,
                'stdev_write_latency_ns': 0.0,
                'avg_read_iops': 793.0,
                'stdev_read_iops': 15.9099
            },
            'vm_metrics': {
                'avg_cpu_utilization_percent': 12.5,
                'stdev_cpu_utilization_percent': 1.1
            },
            'cpu_percent_per_gbps': 0.12345,
            'bs': '4KB',
            'file_size': '1MB',
            'iodepth': '1',
            'iotype': 'read',
            'threads': '1',
            'nrfiles': '1'
        },
        '8KB_2MB_2_write_1_1': {
            'fio_metrics': {
                'avg_read_throughput_kibps': 0.0,
                'stdev_read_throughput_kibps': 0.0,
                'avg_write_throughput_kibps': 5200.0,
                'stdev_write_throughput_kibps': 150.0,
                'avg_read_latency_ns': 0.0,
                'stdev_read_latency_ns': 0.0,
                'avg_write_latency_ns': 1800.0,
                'stdev_write_latency_ns': 100.0,
                'avg_write_iops': 650.0,
                'stdev_write_iops': 18.75
            },
            'vm_metrics': {
                'avg_cpu_utilization_percent': 15.8,
                'stdev_cpu_utilization_percent': 2.3
            },
            'cpu_percent_per_gbps': 0.23456,
            'bs': '8KB',
            'file_size': '2MB',
            'iodepth': '2',
            'iotype': 'write',
            'threads': '1',
            'nrfiles': '1'
        }
    }

    output_filename = "metrics_summary.txt"
    # Clear the file for demonstration purposes
    if os.path.exists(output_filename):
        os.remove(output_filename)

    print("--- Metrics Table (Trial 1) ---")
    pretty_print_metrics_table(metrics, output_file=output_filename)

    # Example of calling it again, appending to the same file
    print("\n--- Metrics Table (Trial 2) ---")
    pretty_print_metrics_table(metrics, output_file=output_filename)
