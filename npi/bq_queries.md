# BigQuery Performance Analysis Queries

After running the FIO NPI benchmarks and collecting the data in BigQuery, you can use the following queries to extract throughput and latency characteristics.

The benchmark runner uploads the full FIO JSON output into a native `JSON` column named `fio_json_output`. We can use BigQuery's native JSON accessors (e.g., `fio_json_output.jobs[0].read.bw`) to extract the metrics.

*Note: In FIO's JSON output, `bw` is reported in KiB/s, and completion latency `clat_ns.mean` is reported in nanoseconds.*

## 1. Extract Raw Throughput and Latency per Iteration

This query retrieves the read and write throughput (in MiB/s) and the mean completion latency (in ms) for every iteration of your benchmark.

```sql
SELECT
  run_timestamp,
  iteration,
  fio_env,
  
  -- Throughput (FIO 'bw' is in KiB/s -> convert to MiB/s)
  FLOAT64(fio_json_output.jobs[0].read.bw) / 1024 AS read_throughput_mib_s,
  FLOAT64(fio_json_output.jobs[0].write.bw) / 1024 AS write_throughput_mib_s,
  
  -- Latency (FIO 'clat_ns.mean' is in nanoseconds -> convert to ms)
  FLOAT64(fio_json_output.jobs[0].read.clat_ns.mean) / 1000000.0 AS read_clat_mean_ms,
  FLOAT64(fio_json_output.jobs[0].write.clat_ns.mean) / 1000000.0 AS write_clat_mean_ms

FROM
  `YOUR_PROJECT_ID.YOUR_BQ_DATASET_ID.YOUR_TABLE_ID`
ORDER BY
  run_timestamp DESC, 
  iteration ASC;
```

## 2. Average Performance Across All Iterations (Grouped by Environment)

This query is useful when you have run multiple iterations (e.g., `--iterations=5`) and want the average throughput and latency grouped by the FIO environment variables (e.g., block size, threads, etc.).

```sql
SELECT
  fio_env,
  
  -- Average Read Metrics
  AVG(FLOAT64(fio_json_output.jobs[0].read.bw)) / 1024 AS avg_read_throughput_mib_s,
  AVG(FLOAT64(fio_json_output.jobs[0].read.clat_ns.mean)) / 1000000.0 AS avg_read_clat_mean_ms,
  
  -- Average Write Metrics
  AVG(FLOAT64(fio_json_output.jobs[0].write.bw)) / 1024 AS avg_write_throughput_mib_s,
  AVG(FLOAT64(fio_json_output.jobs[0].write.clat_ns.mean)) / 1000000.0 AS avg_write_clat_mean_ms,

  -- Count the number of iterations that made up this average
  COUNT(iteration) as iteration_count

FROM
  `YOUR_PROJECT_ID.YOUR_BQ_DATASET_ID.YOUR_TABLE_ID`
GROUP BY
  fio_env
ORDER BY
  avg_read_throughput_mib_s DESC;
```

## 3. Compare Two Different Tables (e.g., HTTP/1.1 vs gRPC)

If you have emitted HTTP/1.1 results to one table and gRPC results to another, you can use `UNION ALL` to compare them side-by-side.

```sql
WITH combined_results AS (
  SELECT
    'HTTP/1.1' AS protocol,
    fio_env,
    FLOAT64(fio_json_output.jobs[0].read.bw) / 1024 AS read_throughput_mib_s
  FROM
    `YOUR_PROJECT_ID.YOUR_BQ_DATASET_ID.fio_read_http1`

  UNION ALL

  SELECT
    'gRPC' AS protocol,
    fio_env,
    FLOAT64(fio_json_output.jobs[0].read.bw) / 1024 AS read_throughput_mib_s
  FROM
    `YOUR_PROJECT_ID.YOUR_BQ_DATASET_ID.fio_read_grpc`
)

SELECT
  protocol,
  fio_env,
  AVG(read_throughput_mib_s) AS avg_read_throughput_mib_s,
  APPROX_QUANTILES(read_throughput_mib_s, 100)[OFFSET(50)] AS median_read_throughput_mib_s
FROM
  combined_results
GROUP BY
  protocol, fio_env
ORDER BY
  fio_env, protocol;
```
