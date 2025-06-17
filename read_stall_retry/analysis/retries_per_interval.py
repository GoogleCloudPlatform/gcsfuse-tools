#!/usr/bin/env python3
# Copyright 2025 Google LLC
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

"""
This script reads a CSV file where the first column contains ISO-formatted
timestamps. It groups these timestamps into specified time intervals (e.g., '5m')
and calculates the number of entries per interval. The results are saved to a
new CSV file and a PNG bar chart is generated.

Usage:
    python retries_per_interval.py <log_file_path> [--interval <interval>]

Examples:
    python retries_per_interval.py /tmp/sample-logs.csv
    python retries_per_interval.py /tmp/sample-logs.csv --interval 10m
    python retries_per_interval.py /tmp/sample-logs.csv -i 1h

Sample Output (CSV: sample-retries_1m.csv):

    Interval Start (UTC),Retries
    2025-06-16 09:21:00,684
    2025-06-16 09:28:00,765
    2025-06-16 09:35:00,318
    2025-06-16 09:42:00,344
    2025-06-16 09:49:00,63
    2025-06-16 09:56:00,8
"""

import argparse
import csv
import datetime
import sys
from collections import Counter, defaultdict
import os

def parse_interval_to_seconds(interval_str):
    """Converts an interval string (e.g., '30s', '5m', '1h') to seconds."""
    if interval_str.endswith('s'):
        return int(interval_str[:-1])
    elif interval_str.endswith('m'):
        return int(interval_str[:-1]) * 60
    elif interval_str.endswith('h'):
        return int(interval_str[:-1]) * 3600
    else:
        raise ValueError("Invalid interval format. Use s, m, or h suffix.")

def process_logs(log_file_path, interval_seconds):
    """
    Reads timestamps from the log file, groups them into intervals,
    and counts retries per interval.
    """
    retry_counts = Counter()
    all_epochs = []

    print(f"Attempting to read log file: {log_file_path}")
    try:
        with open(log_file_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader) # Read header

            # Sanitize header for case-insensitive and whitespace-proof comparison
            sanitized_header = [h.strip().lower() for h in header]

            # Validate that the header has the expected columns in the correct order.
            if len(sanitized_header) < 2 or sanitized_header[0] != 'timestamp' or sanitized_header[1] != 'textpayload':
                print(f"Error: Invalid CSV header in '{log_file_path}'.", file=sys.stderr)
                print("Expected header to start with 'timestamp,textPayload'.", file=sys.stderr)
                print(f"Actual header: {','.join(header)}", file=sys.stderr)
                sys.exit(1)

            for i, row in enumerate(reader):
                if not row:  # Skip empty rows
                    continue
                timestamp_str = row[0]
                try:
                    # Attempt to parse common ISO-like formats
                    try:
                        dt_obj = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    except ValueError:
                        dt_obj = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')

                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=datetime.timezone.utc)
                    else:
                        dt_obj = dt_obj.astimezone(datetime.timezone.utc)

                    epoch = int(dt_obj.timestamp())
                    all_epochs.append(epoch)
                    bucket = (epoch // interval_seconds) * interval_seconds
                    retry_counts[bucket] += 1
                except ValueError as e:
                    print(f"Warning: Could not parse timestamp '{timestamp_str}' on line {i+2}: {e}", file=sys.stderr)
                    continue
    except FileNotFoundError:
        print(f"Error: Log file {log_file_path} not found.", file=sys.stderr)
        sys.exit(1)
    except StopIteration:
        print(f"Error: Log file {log_file_path} appears to be empty or only contains a header.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading or processing log file {log_file_path}: {e}", file=sys.stderr)
        sys.exit(1)


    if not all_epochs:
        return None, None

    min_epoch = min(all_epochs)
    max_epoch = max(all_epochs)

    min_bucket = (min_epoch // interval_seconds) * interval_seconds
    max_bucket = (max_epoch // interval_seconds) * interval_seconds

    full_retry_data = defaultdict(int)
    full_retry_data.update(retry_counts)

    return full_retry_data, min_bucket, max_bucket

def write_csv(output_csv_path, data, min_bucket, max_bucket, interval_seconds):
    """Writes the aggregated retry counts to a CSV file."""
    try:
        with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Interval Start (UTC)", "Retries"])

            current_bucket = min_bucket
            while current_bucket <= max_bucket:
                count = data.get(current_bucket, 0)
                bucket_time_utc = datetime.datetime.fromtimestamp(current_bucket, tz=datetime.timezone.utc)
                writer.writerow([bucket_time_utc.strftime('%Y-%m-%d %H:%M:%S'), count])
                current_bucket += interval_seconds
        print(f"Successfully created CSV: {output_csv_path}")
    except IOError as e:
        print(f"Error: Could not write CSV file to {output_csv_path}: {e}", file=sys.stderr)
        sys.exit(1)


def generate_graph(csv_file_path, output_png_path, title_prefix, interval_str):
    """Generates a bar chart from the CSV data using matplotlib."""
    try:
        import matplotlib
        # Use a non-interactive backend suitable for saving files without a GUI
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("Matplotlib not found. Skipping graph generation.", file=sys.stderr)
        print("To install matplotlib: pip install matplotlib", file=sys.stderr)
        return

    timestamps = []
    retries = []

    try:
        with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    ts = datetime.datetime.strptime(row["Interval Start (UTC)"], '%Y-%m-%d %H:%M:%S')
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
                    timestamps.append(ts)
                    retries.append(int(row["Retries"]))
                except (ValueError, KeyError) as e:
                    print(f"Warning: Could not parse row from CSV for plotting: {row} ({e})", file=sys.stderr)
                    continue
    except FileNotFoundError:
        print(f"Error: Output CSV file {csv_file_path} not found for plotting. Cannot generate graph.", file=sys.stderr)
        return
    except Exception as e:
        print(f"Error reading CSV for plotting {csv_file_path}: {e}", file=sys.stderr)
        return

    if not timestamps:
        print("No data to plot after reading CSV.", file=sys.stderr)
        return

    fig, ax = plt.subplots(figsize=(15, 7))

    interval_duration_in_days = parse_interval_to_seconds(interval_str) / (24*60*60)
    bars = ax.bar(timestamps, retries, width=interval_duration_in_days, align='edge', color='skyblue', edgecolor='black') # Store the bars

    ax.set_xlabel("Time Interval Start (UTC)")
    ax.set_ylabel("Number of Retries")
    ax.set_title(f"Retries per Interval for {title_prefix}\nInterval: {interval_str}")

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d\n%H:%M', tz=datetime.timezone.utc))
    plt.xticks(rotation=45, ha="right")

    # Force a tick for every timestamp in your data
    ax.set_xticks(timestamps)

    if retries: # Ensure retries list is not empty
        y_tick_step = max(1, (max(retries) // 10) or 1)
        # Ensure y-axis upper limit is slightly above the max retry count to make space for text
        ax.set_yticks(range(0, max(retries) + y_tick_step + (y_tick_step //2) , y_tick_step))
        ax.set_ylim(top=max(retries) * 1.15 if max(retries) > 0 else 1) # Add 15% padding for text
    else:
        ax.set_yticks([0])
        ax.set_ylim(top=1)


    ax.grid(True, axis='y', linestyle='--')

    # Add text labels on top of each bar
    for bar in bars:
        yval = bar.get_height()
        if yval > 0: # Only add text if value is greater than 0
            plt.text(bar.get_x() + bar.get_width()/2.0, yval + (max(retries) * 0.01 if retries else 0.01), # Position text slightly above the bar
                     int(yval), # The text to display (integer value)
                     ha='center', va='bottom', # Horizontal and vertical alignment
                     fontsize=9, color='dimgray')


    plt.tight_layout()

    try:
        plt.savefig(output_png_path)
        print(f"Successfully created PNG graph: {output_png_path}")
    except Exception as e:
        print(f"Error saving graph to {output_png_path}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Processes log files to calculate and visualize the number of retries over time.\n"
            "Reads a CSV log file with timestamps, groups retries into intervals, "
            "outputs a new CSV, and generates a PNG graph."
        ),
        epilog=(
            "Examples:\n"
            "  python retries_per_interval.py /tmp/sample-logs.csv\n"
            "  python retries_per_interval.py /data/logs.csv --interval 10m\n"
            "  python retries_per_interval.py my-logs.csv -i 1h\n\n"
            "Notes:\n"
            "  - The '--interval' argument is optional. If not specified, the default interval is '1m'.\n"
            "  - The input file must be a CSV with a header, and the first column must be the timestamp."
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "log_file_path",
        help="Path to the input CSV log file (e.g., '/tmp/sample-logs.csv')."
    )
    parser.add_argument(
        "-i", "--interval",
        dest="interval",
        default="1m",
        help="Time interval for grouping retries (e.g., 30s, 5m, 1h). Default: 1m."
    )

    args = parser.parse_args()

    log_file_path = args.log_file_path
    interval_str = args.interval

    # Create a prefix for output files from the input file path
    base_name = os.path.basename(log_file_path)
    if base_name.endswith("-logs.csv"):
        output_prefix = base_name[:-len("-logs.csv")]
    elif base_name.endswith(".csv"):
        output_prefix = base_name[:-len(".csv")]
    else:
        output_prefix = base_name

    try:
        interval_seconds = parse_interval_to_seconds(interval_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # Add the interval to the output filenames to make them unique and descriptive
    output_csv_path = f"{output_prefix}-retries_{interval_str}.csv"
    output_png_path = f"{output_prefix}-retries_{interval_str}.png"

    print(f"Input Log File: {log_file_path}")
    print(f"Aggregation Interval: {interval_str} ({interval_seconds} seconds)")
    print(f"Output CSV: {output_csv_path}")
    print(f"Output PNG: {output_png_path}")

    retry_data, min_bucket, max_bucket = process_logs(log_file_path, interval_seconds)

    if retry_data is None:
        print("No valid retry data found in log file. Exiting.", file=sys.stderr)
        sys.exit(0)

    write_csv(output_csv_path, retry_data, min_bucket, max_bucket, interval_seconds)
    generate_graph(output_csv_path, output_png_path, output_prefix, interval_str)

if __name__ == "__main__":
    main()
