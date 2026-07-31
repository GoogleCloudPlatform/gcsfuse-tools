# GCSFuse NPI Workspace Rules

## Mandatory Dual-Storage (Regional + Zonal RAPID) Invariant
Whenever running or planning a "full NPI suite" or benchmarking any target compute platform (GCE VM or GKE node pool), the agent MUST ALWAYS generate and execute paired targets:
1. **Regional Standard HNS Target**: `is_rapid_bucket: false`, dataset `<prefix>_regional`, Regional HNS bucket (e.g. `npi-smoke-regional-*`).
2. **Zonal RAPID HNS Target**: `is_rapid_bucket: true`, dataset `<prefix>_zonal`, Zonal RAPID HNS bucket (e.g. `npi-smoke-zonal-*`).

Never plan or execute only one storage tier unless the user explicitly requests a single tier.
