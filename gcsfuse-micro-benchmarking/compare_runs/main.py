import argparse
import sys
import subprocess
import json
import os
import matplotlib.pyplot as plt
import numpy as np
import re

ARTIFACTS_BUCKET = "gcsfuse-perf-benchmark-artifacts"

def sanitize_filename(filename):
    """Removes or replaces characters potentially problematic for filenames."""
    filename = filename.replace('/', '_per_').replace('\\', '_').replace(' ', '_')
    filename = re.sub(r'[^a-zA-Z0-9._-]', '', filename)
    filename = re.sub(r'_+', '_', filename)
    return filename

def load_results_for_benchmark_id(benchmark_id, bucket):
    """
    Loads result.json from GCS if it exists, using gcloud CLI.

    The path checked is gs://{bucket}/{benchmark_id}/result.json

    Args:
        benchmark_id: The ID of the benchmark.
        bucket: The GCS bucket name.

    Returns:
        A dictionary loaded from the JSON file, or None if the file
        doesn't exist or an error occurs.
    """
    gcs_path = f"gs://{bucket}/{benchmark_id}/result.json"
    # print(f"Attempting to load results from: {gcs_path}")

    describe_command = ["gcloud", "storage", "objects", "describe", gcs_path]
    try:
        subprocess.run(describe_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"File not found or no access: {gcs_path}")
        return None
    except FileNotFoundError:
        print("Error: 'gcloud' command not found. Ensure the Google Cloud SDK is installed and in your PATH.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during describe: {e}")
        return None

    cp_command = ["gcloud", "storage", "cp", gcs_path, "-"]
    try:
        cp_result = subprocess.run(cp_command, check=True, capture_output=True, text=True)
        file_content = cp_result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error copying file content from {gcs_path}: {e.stderr}")
        return None
    except FileNotFoundError:
         print("Error: 'gcloud' command not found.")
         return None
    except Exception as e:
        print(f"An unexpected error occurred during copy: {e}")
        return None

    try:
        data = json.loads(file_content)
        return data
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from {gcs_path}: {e}")
        return None

def get_plot_summary(benchmark_ids, metric_configs, output_dir):
    """Generates a summary of the plots being created."""
    summary = []
    summary.append("--- Benchmark Comparison Plot Summary ---")
    summary.append(f"Comparing Benchmark IDs: {', '.join(benchmark_ids)}")
    summary.append(f"Output Directory: {output_dir}\n")
    summary.append("Plots Generated:")

    for metric_name in metric_configs.keys():
        plot_filename_base = sanitize_filename(metric_name.lower())
        plot_filename = f"{plot_filename_base}.png"
        summary.append(f"  - {metric_name}: {os.path.join(output_dir, plot_filename)}")

    summary.append("\nNote:")
    summary.append("  - Each plot visualizes a specific metric across different test cases (X-axis).")
    summary.append("  - Within each test case, points represent the mean value for each Benchmark ID.")
    summary.append("  - Error bars indicate +/- one Standard Deviation, as provided in the 'fio_metrics'.")
    summary.append("  - These are NOT true box-and-whisker plots as quartile/median data is not available in the input.")
    summary.append("--------------------------------------")
    return "\n".join(summary)

def compare_and_visualize(results, output_dir="benchmark_plots"):
    """
    Generates and saves plots comparing benchmark results using error bars.

    Args:
        results (dict): A dictionary where keys are benchmark_ids and values
                        are the parsed JSON results.
        output_dir (str): Directory to save the plot images.
    """
    if not results:
        print("No results to compare and visualize.")
        return

    os.makedirs(output_dir, exist_ok=True)
    benchmark_ids = sorted(results.keys())
    if not benchmark_ids:
        print("Benchmark IDs list is empty.")
        return

    sample_bid = benchmark_ids[0]
    test_cases = sorted(results[sample_bid].keys())
    if not test_cases:
        print(f"No test cases found in benchmark {sample_bid}")
        return

    n_benchmarks = len(benchmark_ids)
    n_test_cases = len(test_cases)
    ind = np.arange(n_test_cases)
    width = 0.9 / (n_benchmarks + 1)

    metric_configs = {
        "Read Throughput (KiB/s)": ("fio_metrics", "avg_read_throughput_kibps", "stdev_read_throughput_kibps"),
        "Write Throughput (KiB/s)": ("fio_metrics", "avg_write_throughput_kibps", "stdev_write_throughput_kibps"),
        "Read Latency (ns)": ("fio_metrics", "avg_read_latency_ns", "stdev_read_latency_ns"),
        "Write Latency (ns)": ("fio_metrics", "avg_write_latency_ns", "stdev_write_latency_ns"),
        "CPU per GBps": (None, "cpu_percent_per_gbps", None),
    }

    print(get_plot_summary(benchmark_ids, metric_configs, output_dir))

    for metric_name, (data_group, avg_key, std_key) in metric_configs.items():
        fig, ax = plt.subplots(figsize=(max(12, n_test_cases * n_benchmarks * 0.4), 8))
        has_data_in_metric = False
        all_vals = []

        for i, bid in enumerate(benchmark_ids):
            means = []
            stdevs = []
            for test_case in test_cases:
                test_data = results[bid].get(test_case, {})
                if data_group:
                    source = test_data.get(data_group, {})
                else:
                    source = test_data

                mean_val = source.get(avg_key, 0.0)
                std_val = source.get(std_key, 0.0) if std_key else 0.0
                means.append(mean_val)
                stdevs.append(std_val)
                if mean_val > 0:
                    has_data_in_metric = True
                if mean_val is not None:
                    all_vals.extend([mean_val - std_val, mean_val + std_val])

            offset = (i - (n_benchmarks - 1) / 2) * width
            positions = ind + offset
            ax.errorbar(positions, means, yerr=stdevs, fmt='o', linestyle='', label=bid, capsize=5, markersize=6, elinewidth=1.5)

        if not has_data_in_metric:
            print(f"Skipping plot for {metric_name}: No positive data found.")
            plt.close(fig)
            continue

        ax.set_ylabel(metric_name, fontsize=12)
        ax.set_title(f"Comparison of {metric_name}", fontsize=16)
        ax.set_xticks(ind)
        ax.set_xticklabels(test_cases, rotation=45, ha="right", fontsize=10)
        ax.legend(title="Benchmark ID", bbox_to_anchor=(1.04, 1), loc='upper left', fontsize=9)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        ax.tick_params(axis='x', labelsize=10)
        ax.tick_params(axis='y', labelsize=10)

        if all_vals:
            min_val = min(all_vals)
            max_val = max(all_vals)
            padding = (max_val - min_val) * 0.1
            if padding == 0: padding = max(abs(max_val) * 0.1, 1)
            ax.set_ylim(max(0, min_val - padding), max_val + padding)
        else:
             ax.set_ylim(0, 1)

        plt.tight_layout(rect=[0, 0, 0.85, 1])
        plot_filename_base = sanitize_filename(metric_name.lower())
        plot_filename = f"{plot_filename_base}.png"
        plot_path = os.path.join(output_dir, plot_filename)

        try:
            plt.savefig(plot_path, dpi=150, bbox_inches='tight')
            # print(f"Saved plot: {plot_path}") # Removed this to avoid duplicate info
        except Exception as e:
            print(f"Error saving plot {plot_path}: {e}")
        finally:
            plt.close(fig)

    print(f"\nFinished generating plots in '{output_dir}' directory.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Script to process and visualize benchmark results from GCS."
    )
    parser.add_argument(
        '--benchmark_ids',
        type=str,
        default='',
        required=True,
        help='A comma-separated list of benchmark IDs (e.g., "id1,id2,id3").'
    )
    parser.add_argument(
        '--output_dir',
        type=str,
        default='benchmark_plots',
        help='Directory to save the output plots.'
    )
    args = parser.parse_args()

    benchmark_ids = [item.strip() for item in args.benchmark_ids.split(',') if item.strip()]
    if not benchmark_ids:
        print("Error: No benchmark IDs provided.")
        sys.exit(1)

    results = {}
    print("--- Loading Benchmark Results ---")
    for bid in benchmark_ids:
        result = load_results_for_benchmark_id(bid, ARTIFACTS_BUCKET)
        if result is not None:
            results[bid] = result
            print(f"Successfully loaded results for {bid}")
        else:
            print(f"Failed to load results for {bid}")
    print("--- Finished Loading ---\n")

    if results:
        compare_and_visualize(results, args.output_dir)
    else:
        print("No results were successfully loaded, skipping visualization.")



