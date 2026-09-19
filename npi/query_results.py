#!/usr/bin/env python3
"""Query NPI benchmark results from BigQuery with explicit read type disaggregation,
robust MAD outlier filtering, Student's t confidence intervals, and Welch's t-test
baseline comparison.
"""

import argparse
import json
import math
import os
import subprocess
import sys

import convergence

_FALLBACK_METRICS = {
    "seq_read_bw_mbs": 0.0,
    "rand_read_bw_mbs": 0.0,
    "write_bw_mbs": 0.0,
    "seq_read_lat_ms": 0.0,
    "rand_read_lat_ms": 0.0,
    "write_lat_ms": 0.0,
    "fio_version": "N/A",
}

_WORKLOAD_LABELS = (
    ("read", "Sequential Read Performance (read)"),
    ("randread", "Random Read Performance (randread)"),
    ("write", "Streaming Write Performance (write)"),
)


def _safe_float(val):
    """Converts val to float if possible and finite, else returns None."""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _fmt_float(val, precision=2, suffix="", signed=False):
    """Formats a float cleanly, returning 'N/A' for non-finite values."""
    if val is None or not isinstance(val, (int, float)) or not math.isfinite(val):
        return "N/A"
    fmt = f"{{:{'+' if signed else ''}.{precision}f}}"
    return f"{fmt.format(val)}{suffix}"


def _extract_samples_from_rows(results):
    """Extracts disaggregated per-iteration sample lists for ('read', 'randread', 'write').

    Supports:
      1. Per-iteration SQL rows with seq_read_bw_mbs / rand_read_bw_mbs / write_bw_mbs
      2. Legacy pre-aggregated single-row payloads
      3. Raw fio_json_output JSON strings/dicts or flat read_bw_mbps columns
    """
    bw_samples = {"read": [], "randread": [], "write": []}
    lat_samples = {"read": [], "randread": [], "write": []}
    fio_version = "unknown"

    for row in results:
        if not isinstance(row, dict):
            continue

        row_ver = row.get("fio_version")
        if row_ver and fio_version == "unknown":
            fio_version = str(row_ver)

        # Case 1: Raw fio_json_output present in row without pre-extracted columns
        raw_fio = row.get("fio_json_output")
        if raw_fio is not None and all(
            row.get(k) is None for k in ("seq_read_bw_mbs", "rand_read_bw_mbs", "write_bw_mbs")
        ):
            try:
                fio_data = json.loads(raw_fio) if isinstance(raw_fio, str) else raw_fio
                if isinstance(fio_data, dict):
                    if fio_version == "unknown" and fio_data.get("fio version"):
                        fio_version = str(fio_data["fio version"])
                    rw = (fio_data.get("global options") or {}).get("rw") or "read"
                    for job in (fio_data.get("jobs") or []):
                        if not isinstance(job, dict):
                            continue
                        read_dict = job.get("read") or {}
                        write_dict = job.get("write") or {}
                        if rw in ("read", "randread") and read_dict:
                            bw_kib = _safe_float(read_dict.get("bw"))
                            lat_ns = _safe_float((read_dict.get("lat_ns") or {}).get("mean"))
                            if bw_kib is not None and bw_kib > 0.0:
                                bw_samples[rw].append(bw_kib * 1024.0 / 1000000.0)
                                lat_samples[rw].append((lat_ns or 0.0) / 1000000.0)
                        if write_dict:
                            bw_kib = _safe_float(write_dict.get("bw"))
                            lat_ns = _safe_float((write_dict.get("lat_ns") or {}).get("mean"))
                            if bw_kib is not None and bw_kib > 0.0:
                                bw_samples["write"].append(bw_kib * 1024.0 / 1000000.0)
                                lat_samples["write"].append((lat_ns or 0.0) / 1000000.0)
                    continue
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                pass

        # Case 2: Standard SQL per-iteration or legacy pre-aggregated columns
        seq_bw = _safe_float(row.get("seq_read_bw_mbs"))
        if seq_bw is None and "read_bw_mbps" in row:
            seq_bw = _safe_float(row.get("read_bw_mbps"))
        rand_bw = _safe_float(row.get("rand_read_bw_mbs"))
        write_bw = _safe_float(row.get("write_bw_mbs"))

        seq_lat = _safe_float(row.get("seq_read_lat_ms"))
        rand_lat = _safe_float(row.get("rand_read_lat_ms"))
        write_lat = _safe_float(row.get("write_lat_ms"))

        has_positive_workload = any(
            v is not None and v > 0.0 for v in (seq_bw, rand_bw, write_bw)
        )

        for w_key, bw_val, lat_val in (
            ("read", seq_bw, seq_lat),
            ("randread", rand_bw, rand_lat),
            ("write", write_bw, write_lat),
        ):
            if bw_val is not None and (bw_val > 0.0 or (bw_val == 0.0 and not has_positive_workload)):
                bw_samples[w_key].append(bw_val)
                if lat_val is not None and (lat_val > 0.0 or (lat_val == 0.0 and not has_positive_workload)):
                    lat_samples[w_key].append(lat_val)
            elif lat_val is not None and lat_val > 0.0:
                lat_samples[w_key].append(lat_val)

    return bw_samples, lat_samples, fio_version


def get_table_metrics(
    project_id,
    dataset_id,
    table_id,
    convergence_threshold=0.05,
    confidence_level=0.95,
    min_iterations=3,
    mad_threshold=3.5,
):
    """Queries BigQuery table for per-iteration sequential read, random read, and write metrics."""
    if table_id.startswith("go_client_"):
        query = f"""
    SELECT
      iteration,
      COALESCE(JSON_VALUE(fio_json_output, '$."fio version"'), 'go-client') AS fio_version,
      COALESCE(
        SAFE_CAST(JSON_VALUE(fio_json_output, '$.jobs[0].read.bw') AS FLOAT64) * 1024.0 / 1000000.0,
        SAFE_CAST(read_bw_mbps AS FLOAT64)
      ) AS seq_read_bw_mbs,
      0.0 AS rand_read_bw_mbs,
      0.0 AS write_bw_mbs,
      COALESCE(
        SAFE_CAST(JSON_VALUE(fio_json_output, '$.jobs[0].read.lat_ns.mean') AS FLOAT64) / 1000000.0,
        0.0
      ) AS seq_read_lat_ms,
      0.0 AS rand_read_lat_ms,
      0.0 AS write_lat_ms
    FROM
      `{project_id}.{dataset_id}.{table_id}`
    ORDER BY iteration ASC
    """
    else:
        query = f"""
    SELECT
      iteration,
      JSON_VALUE(fio_json_output, '$."fio version"') AS fio_version,
      IF(COALESCE(JSON_VALUE(fio_json_output, '$."global options".rw'), 'read') = 'read',
         SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64) * 1024.0 / 1000000.0, NULL) AS seq_read_bw_mbs,
      IF(JSON_VALUE(fio_json_output, '$."global options".rw') = 'randread',
         SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64) * 1024.0 / 1000000.0, NULL) AS rand_read_bw_mbs,
      SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64) * 1024.0 / 1000000.0 AS write_bw_mbs,
      IF(COALESCE(JSON_VALUE(fio_json_output, '$."global options".rw'), 'read') = 'read',
         SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64) / 1000000.0, NULL) AS seq_read_lat_ms,
      IF(JSON_VALUE(fio_json_output, '$."global options".rw') = 'randread',
         SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64) / 1000000.0, NULL) AS rand_read_lat_ms,
      SAFE_CAST(JSON_VALUE(job.write.lat_ns.mean) AS FLOAT64) / 1000000.0 AS write_lat_ms
    FROM
      `{project_id}.{dataset_id}.{table_id}`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
    ORDER BY iteration ASC
    """
    cmd = [
        "bq",
        "query",
        f"--project_id={project_id}",
        "--use_legacy_sql=false",
        "--format=json",
        query,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        try:
            results = json.loads(res.stdout)
        except json.JSONDecodeError:
            results, _ = json.JSONDecoder().raw_decode(res.stdout)
        if results and isinstance(results, list):
            bw_samples, lat_samples, fio_ver = _extract_samples_from_rows(results)
            workloads = {}
            for w in ("read", "randread", "write"):
                bw_summary = convergence.evaluate_convergence(
                    bw_samples[w],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                lat_summary = convergence.evaluate_convergence(
                    lat_samples[w],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                workloads[w] = {
                    "bw_summary": bw_summary,
                    "lat_summary": lat_summary,
                    "bw_samples": list(bw_samples[w]),
                    "lat_samples": list(lat_samples[w]),
                }
            return {
                "seq_read_bw_mbs": workloads["read"]["bw_summary"].representative_value,
                "rand_read_bw_mbs": workloads["randread"]["bw_summary"].representative_value,
                "write_bw_mbs": workloads["write"]["bw_summary"].representative_value,
                "seq_read_lat_ms": workloads["read"]["lat_summary"].representative_value,
                "rand_read_lat_ms": workloads["randread"]["lat_summary"].representative_value,
                "write_lat_ms": workloads["write"]["lat_summary"].representative_value,
                "fio_version": fio_ver,
                "workloads": workloads,
            }
    except Exception as e:
        sys.stderr.write(f"Error retrieving table metrics: {e}\n")
    return dict(_FALLBACK_METRICS)


def get_detailed_table_metrics(
    project_id,
    dataset_id,
    table_id,
    convergence_threshold=0.05,
    confidence_level=0.95,
    min_iterations=3,
    mad_threshold=3.5,
):
    """Queries BigQuery table for granular per-(workload_type, block_size, file_size) breakdown."""
    if table_id.startswith("go_client_"):
        query = f"""
    SELECT
      iteration,
      COALESCE(JSON_VALUE(fio_json_output, '$.jobs[0]."job options".bs'), '1M') AS block_size,
      COALESCE(JSON_VALUE(fio_json_output, '$.jobs[0]."job options".filesize'), 'N/A') AS file_size,
      COALESCE(JSON_VALUE(fio_json_output, '$."global options".rw'), 'read') AS workload_type,
      COALESCE(
        SAFE_CAST(JSON_VALUE(fio_json_output, '$.jobs[0].read.bw') AS FLOAT64) * 1024.0 / 1000000.0,
        SAFE_CAST(read_bw_mbps AS FLOAT64)
      ) AS read_bw_mbs,
      COALESCE(
        SAFE_CAST(JSON_VALUE(fio_json_output, '$.jobs[0].read.lat_ns.mean') AS FLOAT64) / 1000000.0,
        0.0
      ) AS read_lat_ms,
      COALESCE(SAFE_CAST(JSON_VALUE(fio_json_output, '$.jobs[0].read.iops') AS FLOAT64), 0.0) AS read_iops,
      0.0 AS write_bw_mbs,
      0.0 AS write_lat_ms,
      0.0 AS write_iops
    FROM
      `{project_id}.{dataset_id}.{table_id}`
    ORDER BY iteration ASC
    """
    else:
        query = f"""
    SELECT
      iteration,
      JSON_VALUE(job, '$.\"job options\".bs') AS block_size,
      JSON_VALUE(job, '$.\"job options\".filesize') AS file_size,
      COALESCE(JSON_VALUE(fio_json_output, '$.\"global options\".rw'), 'write') AS workload_type,
      SAFE_CAST(JSON_VALUE(job.read.bw) AS FLOAT64) * 1024.0 / 1000000.0 AS read_bw_mbs,
      SAFE_CAST(JSON_VALUE(job.read.lat_ns.mean) AS FLOAT64) / 1000000.0 AS read_lat_ms,
      SAFE_CAST(JSON_VALUE(job.read.iops) AS FLOAT64) AS read_iops,
      SAFE_CAST(JSON_VALUE(job.write.bw) AS FLOAT64) * 1024.0 / 1000000.0 AS write_bw_mbs,
      SAFE_CAST(JSON_VALUE(job.write.lat_ns.mean) AS FLOAT64) / 1000000.0 AS write_lat_ms,
      SAFE_CAST(JSON_VALUE(job.write.iops) AS FLOAT64) AS write_iops
    FROM
      `{project_id}.{dataset_id}.{table_id}`,
      UNNEST(JSON_EXTRACT_ARRAY(fio_json_output.jobs)) AS job
    ORDER BY
      CASE COALESCE(JSON_VALUE(fio_json_output, '$.\"global options\".rw'), 'write')
        WHEN 'read' THEN 1 WHEN 'randread' THEN 2 ELSE 3
      END,
      iteration ASC
    """
    cmd = [
        "bq",
        "query",
        f"--project_id={project_id}",
        "--use_legacy_sql=false",
        "--format=json",
        query,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        try:
            results = json.loads(res.stdout)
        except json.JSONDecodeError:
            results, _ = json.JSONDecoder().raw_decode(res.stdout)
        if results and isinstance(results, list):
            grouped = {}
            group_order = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                w_type = r.get("workload_type") or "write"
                bs = r.get("block_size") or "N/A"
                fs = r.get("file_size") or "N/A"
                key = (w_type, bs, fs)
                if key not in grouped:
                    grouped[key] = {
                        "read_bw": [],
                        "read_lat": [],
                        "read_iops": [],
                        "write_bw": [],
                        "write_lat": [],
                        "write_iops": [],
                    }
                    group_order.append(key)
                rbw = _safe_float(r.get("read_bw_mbs"))
                rlat = _safe_float(r.get("read_lat_ms"))
                riops = _safe_float(r.get("read_iops"))
                wbw = _safe_float(r.get("write_bw_mbs"))
                wlat = _safe_float(r.get("write_lat_ms"))
                wiops = _safe_float(r.get("write_iops"))
                if rbw is not None and rbw > 0.0:
                    grouped[key]["read_bw"].append(rbw)
                if rlat is not None and rlat > 0.0:
                    grouped[key]["read_lat"].append(rlat)
                if riops is not None and riops > 0.0:
                    grouped[key]["read_iops"].append(riops)
                if wbw is not None and wbw > 0.0:
                    grouped[key]["write_bw"].append(wbw)
                if wlat is not None and wlat > 0.0:
                    grouped[key]["write_lat"].append(wlat)
                if wiops is not None and wiops > 0.0:
                    grouped[key]["write_iops"].append(wiops)

            workload_rank = {"read": 1, "randread": 2, "write": 3}
            bs_rank = {"16K": 1, "128K": 2, "1M": 3}
            fs_rank = {
                "128K": 1, "256K": 2, "1M": 3, "5M": 4, "10M": 5,
                "50M": 6, "100M": 7, "200M": 8, "1G": 9,
            }
            group_order.sort(
                key=lambda k: (
                    workload_rank.get(k[0], 4),
                    bs_rank.get(k[1], 4),
                    fs_rank.get(k[2], 10),
                )
            )

            out_rows = []
            for key in group_order:
                w_type, bs, fs = key
                data = grouped[key]
                rbw_sum = convergence.evaluate_convergence(
                    data["read_bw"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                rlat_sum = convergence.evaluate_convergence(
                    data["read_lat"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                riops_sum = convergence.evaluate_convergence(
                    data["read_iops"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                wbw_sum = convergence.evaluate_convergence(
                    data["write_bw"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                wlat_sum = convergence.evaluate_convergence(
                    data["write_lat"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                wiops_sum = convergence.evaluate_convergence(
                    data["write_iops"],
                    min_iterations=min_iterations,
                    convergence_threshold=convergence_threshold,
                    confidence_level=confidence_level,
                    mad_threshold=mad_threshold,
                )
                primary_bw_sum = wbw_sum if w_type == "write" else rbw_sum
                primary_lat_sum = wlat_sum if w_type == "write" else rlat_sum
                primary_bw_samples = list(data["write_bw"] if w_type == "write" else data["read_bw"])
                primary_lat_samples = list(data["write_lat"] if w_type == "write" else data["read_lat"])

                out_rows.append({
                    "workload_type": w_type,
                    "block_size": bs,
                    "file_size": fs,
                    "read_bw_mbs": round(rbw_sum.representative_value, 2),
                    "read_lat_ms": round(rlat_sum.representative_value, 2),
                    "read_iops": round(riops_sum.representative_value, 2),
                    "write_bw_mbs": round(wbw_sum.representative_value, 2),
                    "write_lat_ms": round(wlat_sum.representative_value, 2),
                    "write_iops": round(wiops_sum.representative_value, 2),
                    "bw_summary": primary_bw_sum,
                    "lat_summary": primary_lat_sum,
                    "bw_samples": primary_bw_samples,
                    "lat_samples": primary_lat_samples,
                    "ci_half_width": primary_bw_sum.ci_half_width,
                    "relative_margin_of_error": primary_bw_sum.relative_margin_of_error,
                    "converged": primary_bw_sum.converged,
                })
            return out_rows
    except Exception as e:
        sys.stderr.write(f"Error retrieving detailed table metrics: {e}\n")
    return []


def main():
    parser = argparse.ArgumentParser(
        description="Query NPI benchmark results from BigQuery with explicit read type disaggregation."
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("PROJECT_ID", "gcs-fuse-test"),
        help="GCP Project ID",
    )
    parser.add_argument(
        "--dataset-id",
        required=True,
        help="Current run BigQuery dataset ID",
    )
    parser.add_argument(
        "--baseline-dataset-id",
        default=None,
        help="Optional baseline BigQuery dataset ID for comparison",
    )
    parser.add_argument(
        "--table-types",
        nargs="+",
        default=[
            "fio_read_http1",
            "fio_read_grpc",
            "fio_write_http1",
            "fio_write_grpc",
            "go_client_read_http1",
            "go_client_read_grpc",
        ],
        help="Table IDs to query",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Print granular metrics broken down by file size and block size",
    )
    parser.add_argument(
        "--convergence-threshold",
        type=float,
        default=0.05,
        help="Target relative margin of error threshold (default: 0.05)",
    )
    parser.add_argument(
        "--confidence-level",
        type=float,
        default=0.95,
        help="Confidence level for Student's t intervals (default: 0.95)",
    )
    parser.add_argument(
        "--significance-alpha",
        type=float,
        default=0.05,
        help="Two-sided p-value threshold for Welch's t-test (default: 0.05)",
    )
    parser.add_argument(
        "--min-effect-pct",
        type=float,
        default=2.0,
        help="Minimum practical effect size percentage (default: 2.0)",
    )

    args = parser.parse_args()

    print("\n=========================================================================================================")
    print("===                                 NPI BENCHMARK QUERY RESULTS                                       ===")
    print("=========================================================================================================")
    print(f"Project ID: {args.project_id}")
    print(f"Dataset ID: {args.dataset_id}")
    if args.baseline_dataset_id:
        print(f"Baseline Dataset ID: {args.baseline_dataset_id}")
    print("-" * 105)

    header = (
        f"{'Table ID':<22} | {'Seq Read (MB/s)':<16} | {'Rand Read (MB/s)':<16} | "
        f"{'Write (MB/s)':<14} | {'Base Seq':<10} | {'Base Rand':<10} | {'Delta Seq':<10}"
    )
    print(header)
    print("-" * 105)

    cand_metrics_by_table = {}
    base_metrics_by_table = {}

    for table_id in args.table_types:
        metrics = get_table_metrics(
            args.project_id,
            args.dataset_id,
            table_id,
            convergence_threshold=args.convergence_threshold,
            confidence_level=args.confidence_level,
        )
        cand_metrics_by_table[table_id] = metrics
        seq_read_str = f"{metrics['seq_read_bw_mbs']:.2f}" if metrics["seq_read_bw_mbs"] > 0 else "N/A"
        rand_read_str = f"{metrics['rand_read_bw_mbs']:.2f}" if metrics["rand_read_bw_mbs"] > 0 else "N/A"
        write_str = f"{metrics['write_bw_mbs']:.2f}" if metrics["write_bw_mbs"] > 0 else "N/A"

        base_seq_str = "N/A"
        base_rand_str = "N/A"
        delta_seq_str = "N/A"

        if args.baseline_dataset_id:
            base_metrics = get_table_metrics(
                args.project_id,
                args.baseline_dataset_id,
                table_id,
                convergence_threshold=args.convergence_threshold,
                confidence_level=args.confidence_level,
            )
            base_metrics_by_table[table_id] = base_metrics
            if base_metrics["seq_read_bw_mbs"] > 0:
                base_seq_str = f"{base_metrics['seq_read_bw_mbs']:.2f}"
                if metrics["seq_read_bw_mbs"] > 0:
                    delta_seq = (
                        (metrics["seq_read_bw_mbs"] - base_metrics["seq_read_bw_mbs"])
                        / base_metrics["seq_read_bw_mbs"]
                    ) * 100.0
                    delta_seq_str = f"{delta_seq:+.1f}%"
            if base_metrics["rand_read_bw_mbs"] > 0:
                base_rand_str = f"{base_metrics['rand_read_bw_mbs']:.2f}"

        print(
            f"{table_id:<22} | {seq_read_str:<16} | {rand_read_str:<16} | "
            f"{write_str:<14} | {base_seq_str:<10} | {base_rand_str:<10} | {delta_seq_str:<10}"
        )

    # Dedicated Non-Aggregated Disaggregated Workload Tables (read, randread, write)
    for w_key, w_label in _WORKLOAD_LABELS:
        print("\n" + "=" * 105)
        print(f"=== {w_label:<97} ===")
        print("=" * 105)

        if not args.baseline_dataset_id:
            w_header = (
                f"{'Table ID':<22} | {'BW Est (MB/s)':<14} | {'Median (MB/s)':<14} | "
                f"{'95% CI (+/-)':<13} | {'RMoE (%)':<10} | {'Lat Est (ms)':<13} | {'Converged':<10}"
            )
            print(w_header)
            print("-" * 105)
            for table_id in args.table_types:
                m = cand_metrics_by_table[table_id]
                w_info = m.get("workloads", {}).get(w_key)
                if not w_info or w_info["bw_summary"].n_total == 0:
                    print(
                        f"{table_id:<22} | {'N/A':<14} | {'N/A':<14} | "
                        f"{'N/A':<13} | {'N/A':<10} | {'N/A':<13} | {'N/A':<10}"
                    )
                    continue
                bw_sum = w_info["bw_summary"]
                lat_sum = w_info["lat_summary"]
                bw_est_s = _fmt_float(bw_sum.representative_value, 2)
                med_s = _fmt_float(bw_sum.median, 2)
                ci_s = _fmt_float(bw_sum.ci_half_width, 2)
                rmoe_s = _fmt_float(bw_sum.relative_margin_of_error * 100.0, 2, "%")
                lat_s = _fmt_float(lat_sum.representative_value, 2) if lat_sum.n_total > 0 else "N/A"
                conv_s = "YES" if bw_sum.converged else "NO"
                print(
                    f"{table_id:<22} | {bw_est_s:<14} | {med_s:<14} | "
                    f"{ci_s:<13} | {rmoe_s:<10} | {lat_s:<13} | {conv_s:<10}"
                )
        else:
            w_header = (
                f"{'Table ID':<22} | {'Cand BW (MB/s)':<15} | {'Base BW (MB/s)':<15} | "
                f"{'Delta (%)':<11} | {'p-value':<10} | {'BW Verdict':<24} | {'Lat Verdict':<24}"
            )
            print(w_header)
            print("-" * 105)
            for table_id in args.table_types:
                cand_m = cand_metrics_by_table[table_id]
                base_m = base_metrics_by_table.get(table_id, {})
                cand_w = cand_m.get("workloads", {}).get(w_key, {})
                base_w = base_m.get("workloads", {}).get(w_key, {})

                cand_bw_samples = cand_w.get("bw_samples", [])
                base_bw_samples = base_w.get("bw_samples", [])
                cand_lat_samples = cand_w.get("lat_samples", [])
                base_lat_samples = base_w.get("lat_samples", [])

                bw_comp = convergence.compare_samples_welch(
                    baseline_samples=base_bw_samples,
                    candidate_samples=cand_bw_samples,
                    alpha=args.significance_alpha,
                    min_effect_pct=args.min_effect_pct,
                    higher_is_better=True,
                    confidence_level=args.confidence_level,
                )
                lat_comp = convergence.compare_samples_welch(
                    baseline_samples=base_lat_samples,
                    candidate_samples=cand_lat_samples,
                    alpha=args.significance_alpha,
                    min_effect_pct=args.min_effect_pct,
                    higher_is_better=False,
                    confidence_level=args.confidence_level,
                )

                cand_bw_s = _fmt_float(bw_comp.candidate_value, 2) if cand_bw_samples else "N/A"
                base_bw_s = _fmt_float(bw_comp.baseline_value, 2) if base_bw_samples else "N/A"
                delta_s = _fmt_float(bw_comp.delta_pct, 2, "%", signed=True)
                pval_s = _fmt_float(bw_comp.p_value, 6)
                print(
                    f"{table_id:<22} | {cand_bw_s:<15} | {base_bw_s:<15} | "
                    f"{delta_s:<11} | {pval_s:<10} | {bw_comp.verdict:<24} | {lat_comp.verdict:<24}"
                )

    if args.detailed:
        print("\n" + "=" * 105)
        print("===                               DETAILED METRICS BREAKDOWN                                          ===")
        print("=" * 105)
        for table_id in args.table_types:
            rows = get_detailed_table_metrics(
                args.project_id,
                args.dataset_id,
                table_id,
                convergence_threshold=args.convergence_threshold,
                confidence_level=args.confidence_level,
            )
            if not rows:
                continue
            print(f"\n--- Detailed Table: {table_id} ---")
            det_header = (
                f"{'Type':<12} | {'Block Size':<12} | {'File Size':<12} | "
                f"{'Read (MB/s)':<14} | {'Read Lat (ms)':<14} | {'Write (MB/s)':<14} | {'Write Lat (ms)':<14}"
            )
            print(det_header)
            print("-" * 105)
            for r in rows:
                w_type = r.get("workload_type") or "N/A"
                bs = r.get("block_size") or "N/A"
                fs = r.get("file_size") or "N/A"
                rbw_v = float(r.get("read_bw_mbs") or 0)
                rlat_v = float(r.get("read_lat_ms") or 0)
                wbw_v = float(r.get("write_bw_mbs") or 0)
                wlat_v = float(r.get("write_lat_ms") or 0)
                rbw = f"{rbw_v:.2f}" if rbw_v > 0 else "N/A"
                rlat = f"{rlat_v:.2f}" if rlat_v > 0 else "N/A"
                wbw = f"{wbw_v:.2f}" if wbw_v > 0 else "N/A"
                wlat = f"{wlat_v:.2f}" if wlat_v > 0 else "N/A"
                print(
                    f"{w_type:<12} | {bs:<12} | {fs:<12} | "
                    f"{rbw:<14} | {rlat:<14} | {wbw:<14} | {wlat:<14}"
                )


if __name__ == "__main__":
    main()
