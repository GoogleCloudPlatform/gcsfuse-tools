---
name: run-gcsfuse-npi
description: High-level entrypoint and index for GCSFuse NPI validation and benchmarking skills.
---

# GCSFuse NPI Validation and Benchmarking Skill Index

This index references the modularized, agent-specific skills for running GCSFuse Network Performance Improvement (NPI) verification, conformance testing, performance benchmarking, analysis, and remediation planning.

## Modular Skills Index

1.  **[Conformance Testing](../conformance-testing/SKILL.md)**: Guides on cloning the official GCSFuse repository, executing integration and conformance tests on target GCE VMs, and generating a structured `conformance_results_<TARGET_NAME>.json`.
2.  **[SSH Connection Management](../ssh-connection-management/SKILL.md)**: Focuses on persistent SSH socket multiplexing, checking and cleaning up stale socket files, establishing the master SSH connection, verifying status, and handling target-specific socket paths.
3.  **[Benchmark Build & Setup](../benchmark-build-setup/SKILL.md)**: Focuses on checking out the GCSFuse repository, building Docker/GKE benchmark images locally or on the target, configuring registry access, pushing built images to Artifact Registry, and verifying image availability.
4.  **[Benchmark Suite Execution](../benchmark-suite-execution/SKILL.md)**: Focuses on executing benchmarking workflows (GCE VM FIO tests, GKE container tests) using `npi_orchestrator.py`, parameterizing VM/cluster names dynamically from configured inputs like `targets.json`, handling parameters, verifying BQ table exports, and monitoring job states.
5.  **[Analysis & Report Generation](../analysis-report-generation/SKILL.md)**: Focuses on querying BigQuery tables for throughput/latency metrics, comparing results against baseline runs, and generating a standardized `npi_validation_report.md`.
6.  **[Remediation Advisor](../remediation-advisor/SKILL.md)**: Focuses on debugging regressions, errors (e.g. Direct Path fallbacks, TLS handshake errors), or resource constraints (e.g. GKE TPU OOMs), and outlining a structured `npi_remediation_plan.md`.
