# Performance Gating & Threshold Analysis

This directory contains automated performance gating, trend verification, and statistical threshold analysis tools for GCSFuse releases.

## Files

- [`perf_gating.py`](file:///usr/local/google/home/cpranjal/gcsfuse-tools/perf_gating/perf_gating.py): Main CLI script to query BigQuery and verify performance gating criteria against historical minor/patch baselines and daily averages.
- [`threshold_analyzer.py`](file:///usr/local/google/home/cpranjal/gcsfuse-tools/perf_gating/threshold_analyzer.py): Modular statistical analysis script that utilizes core functions from `perf_gating.py` (`bq_query`, `load_workloads`, `get_sql_filter`, `get_workload_key`) to compute historical min, max, mean, variance, standard deviation, coefficient of variation (CV %), and recommended gating thresholds for target workloads.
- [`gating_workloads.csv`](file:///usr/local/google/home/cpranjal/gcsfuse-tools/perf_gating/gating_workloads.csv): CSV configuration file defining the specific target workloads to benchmark and gate on.
- [`test_perf_gating.py`](file:///usr/local/google/home/cpranjal/gcsfuse-tools/perf_gating/test_perf_gating.py): Unit tests for version parsing, baseline selection, CSV workload loading, dynamic SQL query generation, and gating evaluation logic.
- [`test_threshold_analyzer.py`](file:///usr/local/google/home/cpranjal/gcsfuse-tools/perf_gating/test_threshold_analyzer.py): Unit tests for statistical SQL query generation, derived variability metrics, threshold suggestions, and CSV export.

---

## Target Workloads Configuration (`gating_workloads.csv`)

Target workloads to benchmark and gate against are defined in [`gating_workloads.csv`](gating_workloads.csv) rather than being hardcoded in Python.

### Columns
- `io_type`: Operation type (e.g., `read`, `write`)
- `file_size`: Target file size (e.g., `1m`, `1g`)
- `block_size`: Block size for I/O operations (e.g., `1m`)
- `num_jobs`: Number of concurrent jobs/threads (e.g., `48`)
- `config`: Mount configuration / protocol (e.g., `http1`, `grpc`)
- `direct`: Direct I/O flag (`0` for buffered/cache, `1` for direct)

You can add, edit, or remove rows directly in `gating_workloads.csv` to modify the evaluated workload combinations across both `perf_gating.py` and `threshold_analyzer.py`.

---

## Threshold Analyzer (`threshold_analyzer.py`)

`threshold_analyzer.py` queries historical BigQuery benchmark runs (periodic kokoro runs or release metrics) for a given commit hash, release version, or lookback window (e.g., previous 30 days) to empirically determine the appropriate gating threshold.

### Calculated Statistics
For each target workload, the analyzer computes:
- **Count**: Total sample runs evaluated.
- **Mean (MB/s)**: Average throughput.
- **Min / Max (MB/s)**: Minimum and maximum throughput observed.
- **StdDev & Variance**: Sample standard deviation and variance of throughput.
- **CV %**: Coefficient of variation ($\frac{\text{StdDev}}{\text{Mean}} \times 100\%$).
- **Max Drop %**: Worst negative percentage drop from the mean ($\frac{\text{Min} - \text{Mean}}{\text{Mean}} \times 100\%$).
- **Suggested %**: Recommended per-workload threshold percentage based on $\max(|\text{Max Drop \%}|, 2 \times \text{CV\%})$ with a configurable minimum floor (default `5.0%`).
- **Overall Recommended Uniform Gating Threshold**: A single conservative threshold percentage (maximum of suggested thresholds across workloads) that can be passed directly to `perf_gating.py --threshold <value>`.

### Usage Examples

1. **Analyze recent 30-day periodic runs (default):**
   ```bash
   python3 perf_gating/threshold_analyzer.py --days 30
   ```

2. **Analyze runs for a specific commit hash over the past 14 days:**
   ```bash
   python3 perf_gating/threshold_analyzer.py --commit abc1234 --days 14
   ```

3. **Analyze a specific release version from release metrics:**
   ```bash
   python3 perf_gating/threshold_analyzer.py --source release --release-version 3.9.0
   ```

4. **Analyze all sources and export results to CSV:**
   ```bash
   python3 perf_gating/threshold_analyzer.py \
     --source all \
     --days 60 \
     --min-floor 5.0 \
     --output-csv threshold_analysis.csv
   ```

---

## Performance Gating (`perf_gating.py`)

Run automated gating evaluation against historical minor/patch releases and daily averages.

### Usage Examples

1. **Run against a specific release version:**
   ```bash
   python3 perf_gating/perf_gating.py --release-version 3.9.0
   ```

2. **Run with a custom threshold (determined from `threshold_analyzer.py`):**
   ```bash
   python3 perf_gating/perf_gating.py \
     --release-version 3.9.0 \
     --threshold 12.5 \
     --workloads-csv perf_gating/gating_workloads.csv
   ```

---

## Unit Testing

Execute all unit tests from the repository root:

```bash
python3 -m unittest discover -s perf_gating -p "test_*.py"
```
