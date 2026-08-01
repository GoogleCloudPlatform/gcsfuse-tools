# GCSFuse NPI Workspace Rules

## Mandatory Dual-Storage (Regional + Zonal RAPID) Invariant
Whenever running or planning a "full NPI suite" or benchmarking any target compute platform (GCE VM or GKE node pool), the agent MUST ALWAYS generate and execute paired targets:
1. **Regional Standard HNS Target**: `is_rapid_bucket: false`, dataset `<prefix>_regional`, Regional HNS bucket (e.g. `npi-smoke-regional-*`).
2. **Zonal RAPID HNS Target**: `is_rapid_bucket: true`, dataset `<prefix>_zonal`, Zonal RAPID HNS bucket (e.g. `npi-smoke-zonal-*`).

Never plan or execute only one storage tier unless the user explicitly requests a single tier.

## Mandatory Explicit Read Type Disaggregation (Sequential vs Random)
Whenever querying BigQuery benchmark results and generating the NPI validation report (`npi_validation_report.md`), the agent MUST ALWAYS:
1. Extract both Sequential Read (`read`) and Random Read (`randread`) workloads by querying `JSON_VALUE(fio_json_output, '$."global options".rw')`.
2. Present dedicated, non-aggregated tables for:
   - **Sequential Read Performance (`read`)**
   - **Random Read Performance (`randread`)**
   - **Streaming Write Performance (`write`)**
3. Never collapse or average `read` and `randread` rows together into a single generic "read" metric.
