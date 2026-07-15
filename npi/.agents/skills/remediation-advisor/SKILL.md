---
name: remediation-advisor
description: Guides on diagnosing GCSFuse NPI performance regressions, 20 GB/s SLA gate failures, and conformance errors, analyzing root causes across FUSE queue depths, gRPC connection pools, OS LRO/GRO offloads, RFS/RPS packet steering, and sysctl TCP buffers, and compiling the advisory deliverable npi_remediation_plan.md without executing unrequested system modifications.
---

# GCSFuse NPI Remediation Advisor

This skill guides you through diagnosing regressions, SLA gate failures, or resource bottlenecks identified during benchmarking and conformance testing. It provides concrete diagnostic trees and directs the creation of a structured `npi_remediation_plan.md`.

## Prerequisites & Trigger Conditions

### Prerequisites
1. **Validation Report or Conformance Deliverables**: Availability of `npi_validation_report.md` or `conformance_results_<TARGET_NAME>.json` showing performance regressions or test failures.
2. **Access to System & Benchmark Metrics**: Access to BigQuery benchmark tables (`host_info`, `fio_*`) or execution logs (`npi_commands.log`).
3. **Repository Context**: Access to GCSFuse source code to review `params.yaml` and default mount configuration options.

### Trigger Conditions
- Sequential read throughput fails to achieve the **20 GB/s SLA gate** for HTTP/1.1 or gRPC in standard, non-NUMA-pinned runs.
- Throughput or latency regresses by **>5%** compared to baseline dataset.
- Conformance or POSIX integration tests return failure status codes.
- Resource bottlenecks occur (e.g., GKE TPU host OOM, disk space >85%, FUSE daemon queue saturation).

## Input/Output Contract

### Inputs
- **`npi_validation_report.md`**: Validation report detailing benchmark metrics, intra-run comparison deltas, and SLA verdicts.
- **`conformance_results_<TARGET_NAME>.json`**: Detailed JSON report of POSIX conformance test failures.
- **Target Specifications**: Target machine family (`c4`), CPU cores, RAM, network interfaces (`eth0`), storage mount path.

### Outputs
- **`npi_remediation_plan.md`**: Advisory remediation document containing:
  - Identified Issues & Root Cause Analysis.
  - Recommended Remediation Steps across Phase 1 (High Priority: FUSE params, connection pools, LRO/GRO, RFS/RPS), Phase 2 (Medium Priority: MTU 8896, TCP buffers), and Phase 3 (Experimental / Niche research).
  - Explicit Verification Plan detailing re-test rules.

## Step-by-Step Procedure

### Diagnostic Trees

#### 1. Performance Regressions (>5% vs Baseline)
- **Check CPU Pinning / NUMA alignment**: Confirm FIO docker runs were pinned to the correct NUMA node matching the network interface (e.g. `numa0`).
- **Check Direct Path (gRPC)**: Verify if Direct Path was active. Check logs for `Direct Path disabled` or `GOAWAY received`. Fallback to HTTP/1.1 or check VPC routing rules.
- **Check Machine Type Configuration**: Validate if the VM family (e.g. `c4`) is registered in `params.yaml` in the GCSFuse repo. If missing, GCSFuse defaults to conservative connection limits.

#### 2. Conformance Test Failures
- **HNS (Hierarchical Namespace) Mismatch**: Some directory renaming or file operations behave differently if HNS is disabled. Verify if the target GCS bucket has HNS enabled.
- **Permission Errors**: Verify if the VM's service account possesses necessary roles (`Storage Object Admin`, `Storage Legacy Bucket Owner`).
- **Transient Network Failures**: Check for socket timeouts, connection resets, or DNS resolution failures.

#### 3. Resource Exhaustion / Hangs
- **Out of Disk (GCE)**: Ensure `/mnt/lssd` (RAID0) was mounted properly and size > 2TiB. If boot disk filled up, increase boot disk size (>=200GB) and verify mounting script.
- **Out of Memory / OOM (GKE TPU)**: Since GKE TPU nodes use RAM disk buffers, ensure `read_file_cache` tests were skipped. Ensure memory limits match node capability.
- **Orchestrator Timeouts (14400s)**: If GCSFuse hung, verify mount options or Direct Path compatibility in that zone.

#### 4. High-Bandwidth Throughput Bottlenecks (< 20 GB/s SLA Gate)
- **FUSE Concurrency Limits**: If workload is highly concurrent (128 numjobs), FUSE default queues bottleneck. Verify if `--max-background` and `--congestion-threshold` are unset or set low.
- **gRPC Connection Congestion**: Single gRPC channel hits stream concurrency limits. Verify if `--experimental-grpc-conn-pool-size` is unset or low.
- **NIC Packet Processing (Softirqs)**: Saturation on single CPU cores handling network interrupts bottlenecks throughput. Verify if Large Receive Offload (LRO) or Generic Receive Offload (GRO) is disabled.
- **Receive Flow Steering (RFS/RPS)**: Without RFS/RPS, network packets process on different CPU cores than FUSE reader threads, causing CPU cache misses.
- **FUSE Read-Ahead Size**: Check if `--max-read-ahead-kb` is unset (defaults to 128KB). For sequential reads of large files, set to 4096KB.

---

### Execution Steps

1. **Step 1: Analyze Validation Reports & Conformance Deliverables**: Review `npi_validation_report.md` and `conformance_results_<TARGET_NAME>.json`.
2. **Step 2: Formulate Actions Based on Diagnostics**: Map observed failure symptoms to concrete parameters in Phase 1, Phase 2, or Phase 3.
3. **Step 3: Verify Machine Type PR Needs**: Check `params.yaml` in local GCSFuse repo checkout.
4. **Step 4: Generate `npi_remediation_plan.md`**:

> [!IMPORTANT]
> **Advisory Only**: The remediation plan is strictly advisory. Do NOT execute or apply remediation commands (such as running `sysctl`, `ethtool`, or editing cluster configs) automatically unless explicitly instructed by the user.

Example `npi_remediation_plan.md` template:
```markdown
# GCSFuse NPI Remediation Plan

## Identified Issues & Gap Analysis
### 1. [Issue Name, e.g., Non-Pinned Throughput SLA Failure (<20 GB/s)]
- **Symptom**: [Verbatim throughput metric, e.g., Non-pinned gRPC throughput achieved 14.2 GB/s vs 20 GB/s SLA gate]
- **Root Cause Category**: [e.g. Network Offloads / FUSE Queue Depth]
- **Diagnostic Details**: [Evidence from host_info or ethtool output]

## Recommended Remediation Steps

### Phase 1: High Priority (Blocking / High-Impact Fixes)
1. **GCSFuse FUSE Parameter Tuning**:
   - Set `--max-background=512` and `--congestion-threshold=512`.
   - Set `--max-read-ahead-kb=4096` (4MB).
2. **GCSFuse Connection Pool Expansion (gRPC & HTTP/1.1)**:
   - Set `--experimental-grpc-conn-pool-size=128`.
   - Set `--max-conns-per-host=256` and `--max-idle-conns-per-host=256`.
3. **Kernel/OS Large Receive Offload (LRO / GRO)**:
   ```bash
   DEFAULT_IFACE=$(ip route show default | awk '{print $5}')
   sudo ethtool -K $DEFAULT_IFACE gro on
   sudo ethtool -K $DEFAULT_IFACE lro on
   ```
4. **Receive Flow Steering (RFS) & Receive Packet Steering (RPS)**:
   ```bash
   DEFAULT_IFACE=$(ip route show default | awk '{print $5}')
   sudo sysctl -w net.core.rps_sock_flow_entries=32768
   for f in /sys/class/net/$DEFAULT_IFACE/queues/rx-*/rps_flow_cnt; do echo 2048 | sudo tee $f; done
   # Calculate dynamic hex core bitmask based on nproc core count
   RPS_MASK=$(python3 -c "print(hex((1<<$(nproc))-1)[2:])")
   for f in /sys/class/net/$DEFAULT_IFACE/queues/rx-*/rps_cpus; do echo "$RPS_MASK" | sudo tee $f; done
   ```

### Phase 2: Medium/Low Priority (System / Infrastructure Optimizations)
1. **Network MTU Jumbo Frames**: Set MTU to 8896 on supported VPCs (`DEFAULT_IFACE=$(ip route show default | awk '{print $5}') && sudo ip link set dev $DEFAULT_IFACE mtu 8896`).
2. **TCP Buffer Window Tuning**:
   ```bash
   sudo sysctl -w net.core.rmem_max=134217728
   sudo sysctl -w net.core.wmem_max=134217728
   sudo sysctl -w net.ipv4.tcp_rmem="4096 87380 67108864"
   sudo sysctl -w net.ipv4.tcp_wmem="4096 65536 67108864"
   sudo sysctl -w net.netfilter.nf_conntrack_max=1048576
   ```

### Phase 3: Open-Ended Performance Exploration & Niche Parameters (Experimental)
1. **NIC Ring Buffer Optimization**: `DEFAULT_IFACE=$(ip route show default | awk '{print $5}') && sudo ethtool -G $DEFAULT_IFACE rx 4096 tx 4096`
2. **CPU Governor Tuning**: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`
3. **PCIe Max Read Request Size (MRRS)**: Inspect and tune via `lspci -vvv`.

## Verification Plan
[Specify exact re-test criteria: Rerun benchmark suite with specified parameters and confirm non-pinned 1G file sequential read throughput >= 20 GB/s for both HTTP/1.1 and gRPC].
```

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Remediation / Recovery Action |
|---|---|---|
| **Unauthorized Auto-Execution Attempt** | Agent attempts to run `sysctl` or `ethtool` directly on remote host | Enforce Advisory-Only policy. Never execute system configuration commands automatically. Write commands to `npi_remediation_plan.md`. |
| **VPC Unsupported Jumbo Frames** | Interface MTU set to 8896 on VPC without Jumbo Frame support | Network interface drops packets. Revert MTU to 1500 (`sudo ip link set dev <interface> mtu 1500`) and focus on TCP window and LRO tuning. |
| **`ethtool -K` Unsupported Offload** | Virtual NIC driver does not support hardware LRO | Check driver via `ethtool -i <interface>`. Rely on GRO (software Generic Receive Offload) and RPS packet steering instead. |

## Verification Checks

1. **Verify Deliverable File**: Confirm `npi_remediation_plan.md` exists and is non-empty:
   ```bash
   test -s npi_remediation_plan.md && echo "Remediation plan generated successfully"
   ```
2. **Verify Required Plan Sections**: Confirm all key sections are present in the deliverable:
   ```bash
   grep -E "(Identified Issues|Phase 1|Phase 2|Phase 3|Verification Plan)" npi_remediation_plan.md
   ```
3. **Verify Compliance with Advisory Policy**: Confirm the plan contains explicit advisory notice preventing unrequested auto-execution.
