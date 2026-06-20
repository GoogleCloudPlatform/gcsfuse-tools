---
name: remediation-advisor
description: Guides on diagnosing GCSFuse NPI performance regressions and conformance test failures, and compiling a structured remediation plan.
---

# GCSFuse NPI Remediation Advisor

This skill guides you through diagnosing regressions, test failures, or resource bottlenecks identified during benchmarking and conformance testing. It provides concrete debugging paths and directs the creation of a structured `npi_remediation_plan.md`.

## Diagnostic Trees

### 1. Performance Regressions (>5% vs Baseline)
- **Check CPU Pinning / NUMA alignment**: Confirm FIO docker runs were pinned to the correct NUMA node matching the network interface (e.g. `numa0`).
- **Check Direct Path (gRPC)**: Verify if Direct Path was active. Check logs for `Direct Path disabled` or `GOAWAY received`. Fallback to HTTP/1.1 or check VPC routing rules.
- **Check Machine Type Configuration**: Validate if the VM family (e.g. `c4`) is registered in `params.yaml` in the GCSFuse repo. If not, GCSFuse defaults to conservative connection limits.

### 2. Conformance Test Failures
- **HNS (Hierarchical Namespace) Mismatch**: Some directory renaming or file operations behave differently if HNS is disabled. Verify if the target GCS bucket has HNS enabled.
- **Permission Errors**: Verify if the VM's service account possesses necessary roles (`Storage Object Admin`, `Storage Legacy Bucket Owner`).
- **Transient Network Failures**: Check for socket timeouts, connections resets, or DNS resolution failures.

### 3. Resource Exhaustion / Hangs
- **Out of Disk (GCE)**: Ensure `/mnt/lssd` (RAID0) was mounted properly and size > 2TiB. If the boot disk filled up instead, increase boot disk size (>=200GB) and verify mounting script.
- **Out of Memory / OOM (GKE TPU)**: Since GKE TPU nodes use RAM disk buffers, ensure `read_file_cache` tests were skipped. Ensure memory limits match node capability.
- **Orchestrator Timeouts (600s)**: If GCSFuse hung, verify the mount options or Direct Path compatibility in that zone.

### 4. High-Bandwidth Throughput Bottlenecks (< 20GB/s for high-concurrency large files)
- **FUSE Concurrency Limits**: If the workload is highly concurrent (e.g., 128+ numjobs), FUSE default queues will bottle-neck. Verify if `--max-background` and `--congestion-threshold` are unset or set to low values.
- **gRPC Connection Congestion**: For massive parallel streams, a single gRPC channel hits stream concurrency limits or CPU limits. Verify if `--experimental-grpc-conn-pool-size` is unset or low.
- **NIC Packet Processing (Softirqs)**: Saturation on single CPU cores handling network interrupts will bottleneck throughput. Verify if Large Receive Offload (LRO) or Generic Receive Offload (GRO) is disabled.
- **Receive Flow Steering (RFS)**: Without RFS/RPS, network packets might be processed on different CPU cores than the FUSE reader threads, causing heavy CPU cache misses. Verify if RPS/RFS are unconfigured.
- **FUSE Read-Ahead Size**: Check if `--max-read-ahead-kb` is unset (defaults to 128KB). For sequential reads of large files, this must be much larger.
- **Network MTU**: Check if standard MTU 1500 is used instead of Jumbo Frames (MTU 8896), which decreases packet processing overhead.

---

## Step-by-Step Procedure

### Step 1: Analyze Validation Reports and Conformance Results
Review `npi_validation_report.md` and `conformance_results.json` to identify failed test suites or performance regressions.

### Step 2: Formulate Actions based on Diagnostics
Select from the diagnostic trees above to form concrete, actionable steps.

### Step 3: Verify Machine Type Optimization PR
If the machine family is missing from `params.yaml`, the concrete recommendation must include creating a PR in the main GCSFuse repository to whitelist the machine family.

### Step 4: Generate `npi_remediation_plan.md`
Generate a remediation plan with the following structure:

> [!IMPORTANT]
> **Advisory Only**: The remediation plan is strictly advisory. Do not execute or apply any remediation steps (such as modifying cluster configurations, editing cloud files, or applying local patches) automatically unless explicitly requested by the user.

```markdown
# GCSFuse NPI Remediation Plan

## Identified Issues & Gap Analysis
### 1. [Issue Name, e.g., gRPC Read Throughput Regression]
- **Symptom**: [Verbatim error or throughput delta, e.g., gRPC Read BW dropped by 20% compared to baseline]
- **Root Cause Category**: [e.g. Network / Configuration / Code Bug]
- **Diagnostic Details**: [Evidence from logs or BigQuery, e.g., `params.yaml` missing `c4` family]

### 2. [Issue Name, e.g., TestAppendWrite Failure]
- **Symptom**: [Verbatim test failure logs]
- **Diagnostic Details**: [e.g. Bucket lacks Hierarchical Namespace (HNS)]

## Recommended Remediation Steps

### Phase 1: High Priority (Blocking / High-Impact Fixes)
1.  **GCSFuse FUSE Parameter Tuning**:
    *   **Action**: Modify GCSFuse mount options to increase FUSE kernel queue depth.
    *   **Parameters**: Set `--max-background=512` and `--congestion-threshold=512` (typically 75-100% of max-background).
    *   **Read-Ahead**: Set `--max-read-ahead-kb=4096` (4MB) to maximize kernel sequential prefetching.
2.  **GCSFuse Connection Pool Expansion (gRPC)**:
    *   **Action**: Expand gRPC connection pool to multiplex requests across independent TCP connections.
    *   **Parameters**: Set `--experimental-grpc-conn-pool-size=128` (or match the concurrent worker count).
3.  **GCSFuse Connection Pool Expansion (HTTP/1.1)**:
    *   **Action**: Increase HTTP/1.1 max connections and idle connections to prevent connection starvation under highly concurrent workloads.
    *   **Parameters**: Set `--max-conns-per-host=256` and `--max-idle-conns-per-host=256` (defaults are 100).
4.  **Kernel/OS Large Receive Offload (LRO / GRO)**:
    *   **Action**: Enable LRO/GRO on the host's network interfaces to coalesce incoming packets, reducing CPU overhead from packet headers.
    *   **Commands**:
        ```bash
        sudo ethtool -K <interface> gro on
        sudo ethtool -K <interface> lro on
        ```
5.  **Receive Flow Steering (RFS) & Receive Packet Steering (RPS)**:
    *   **Action**: Configure RPS and RFS on the network queues to route packet processing to the CPU cores where the FUSE reader threads are running.
    *   **Commands**:
        ```bash
        # Set socket flow table size
        sudo sysctl -w net.core.rps_sock_flow_entries=32768
        # For each rx queue, set flow count
        for f in /sys/class/net/<interface>/queues/rx-*/rps_flow_cnt; do echo 2048 | sudo tee $f; done
        # Enable RPS by mapping to all CPUs or local NUMA CPUs
        for f in /sys/class/net/<interface>/queues/rx-*/rps_cpus; do echo "fff" | sudo tee $f; done
        ```

### Phase 2: Medium/Low Priority (System / Infrastructure Optimizations)
1.  **Network MTU Jumbo Frames**:
    *   **Action**: Set the network interface MTU to Jumbo Frames (8896 on GCP) if supported by the VPC.
    *   **Command**: `sudo ip link set dev <interface> mtu 8896`
2.  **TCP Buffer Window Tuning**:
    *   **Action**: Increase maximum TCP socket memory allocations via sysctl.
    *   **Commands**:
        ```bash
        sudo sysctl -w net.core.rmem_max=134217728
        sudo sysctl -w net.core.wmem_max=134217728
        sudo sysctl -w net.netfilter.nf_conntrack_max=1048576
        ```

### Phase 3: Open-Ended Performance Exploration & Niche Parameters (Experimental)
If the 20 GB/s target is still not met after applying standard high-priority tunings, you MUST actively explore, research, and recommend additional platform-specific or kernel-level parameter tweaks. A few experimental areas to investigate and propose are:
1.  **NIC Ring Buffer Optimization**:
    *   **Action**: Increase the RX/TX descriptor ring sizes on the network interface card to prevent packet drop/throttling under heavy packet rates.
    *   **Command**: `sudo ethtool -G <interface> rx 4096 tx 4096` (or set to maximum supported values shown by `ethtool -g <interface>`).
2.  **CPU Power & Frequency Governor Tuning**:
    *   **Action**: Force all CPU cores into high-performance mode to eliminate latency spikes caused by dynamic frequency scaling (DVFS) under load.
    *   **Command**: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`
3.  **PCIe Max Read Request Size (MRRS)**:
    *   **Action**: Check and increase the PCIe MRRS for the virtual network device (e.g., using `lspci -vvv`) to optimize PCIe bus utilization for high-speed transfers.
4.  **Virtual Memory (VM) Page Cache / Dirty Ratio Tuning**:
    *   **Action**: Tune dirty page writeback ratios in `/proc/sys/vm/` (e.g., `dirty_background_ratio` and `dirty_ratio`) to prevent sudden background page flushes from blocking FUSE daemon I/O threads.
5.  **Active Research Option**:
    *   **Action**: Perform web searches or consult GCP/Linux performance guides specifically targeting the VM machine family (e.g., C3, C4) or gVNIC drivers to identify other niche parameters or driver configurations to tweak.

## Verification Plan
[Specify how to verify each fix, e.g. "Rerun the performance-benchmarking suite with the tuned FUSE mount options and OS network/kernel tuning, and verify through the analysis report that the 1G file-size, 1M block size, 128 numjobs sequential read throughput successfully achieves or exceeds the 20GB/s target for BOTH HTTP/1.1 and gRPC protocols in the **non-NUMA-pinned (standard) configurations** without enabling GCSFuse caches."]
```
