#!/usr/bin/env python3
"""Skill for parsing FIO JSON output to extract key performance metrics."""

import json
import logging
import sys
import argparse

def parse_fio_output(filename):
    """Parses FIO JSON output to extract key metrics."""
    try:
        with open(filename, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logging.error(f"Could not read or parse FIO output {filename}: {e}")
        return []

    results = []
    for job in data.get("jobs", []):
        job_name = job.get("jobname", "unnamed_job")
        for op in ["read", "write"]:
            if op in job:
                stats = job[op]
                options = job.get("job options", {})
                # Bandwidth is in KiB/s, convert to MiB/s
                bw_mibps = stats.get("bw", 0) / 1024.0
                if bw_mibps == 0:
                    continue
                iops = stats.get("iops", 0)

                # Latency can be under 'lat_ns', 'clat_ns', etc.
                lat_stats = stats.get("lat_ns") or {}

                # Convert from ns to ms
                mean_lat_ms = lat_stats.get("mean", 0) / 1_000_000.0

                # Percentiles are in a sub-dict with string keys
                percentiles = lat_stats.get("percentiles", {})  # FIO 3.x
                
                p99_key = next((k for k in percentiles if k.startswith("99.00")), None)
                p99_lat_ms = (
                    percentiles.get(p99_key, 0) / 1_000_000.0 if p99_key else 0
                )

                results.append({
                    "job_name": job_name,
                    "block_size": options.get("bs", 0),
                    "file_size": options.get("filesize", 0),
                    "nr_files": options.get("nrfiles", 0),
                    "queue_depth": data.get("global options", {}).get("iodepth", 0),
                    "num_jobs": options.get("numjobs", 0),
                    "operation": data.get("global options", {}).get("rw", "unknown"),
                    "bw_mibps": bw_mibps,
                    "iops": iops,
                    "mean_lat_ms": mean_lat_ms,
                    "p99_lat_ms": p99_lat_ms,
                })
    return results

def main():
    parser = argparse.ArgumentParser(description="Parse FIO JSON output.")
    parser.add_argument("filename", help="Path to the FIO JSON output file.")
    parser.add_argument("--output", choices=["json", "text"], default="text", help="Output format.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    results = parse_fio_output(args.filename)

    if args.output == "json":
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No results found or failed to parse.")
            return
            
        print(f"{'Job Name':<20} {'Op':<8} {'BW (MiB/s)':<12} {'IOPS':<10} {'Mean Lat (ms)':<15} {'P99 Lat (ms)':<15}")
        print("-" * 80)
        for r in results:
            print(f"{r['job_name']:<20} {r['operation']:<8} {r['bw_mibps']:<12.2f} {r['iops']:<10.2f} {r['mean_lat_ms']:<15.4f} {r['p99_lat_ms']:<15.4f}")

if __name__ == "__main__":
    main()
