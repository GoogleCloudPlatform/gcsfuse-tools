#!/usr/bin/env python3
"""Test script to run result aggregation independently"""

import sys
from helpers import result_aggregator, report_generator

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 test_aggregation.py <benchmark_id> <artifacts_bucket> <vm1> [vm2] [vm3] ...")
        print("\nExample:")
        print("  python3 test_aggregation.py benchmark-1767457751 princer-working-dirs princer-test-m38x princer-test-x1r8")
        sys.exit(1)
    
    benchmark_id = sys.argv[1]
    artifacts_bucket = sys.argv[2]
    vms = sys.argv[3:]
    
    print(f"Testing aggregation for:")
    print(f"  Benchmark ID: {benchmark_id}")
    print(f"  Artifacts Bucket: {artifacts_bucket}")
    print(f"  VMs: {', '.join(vms)}")
    print()
    
    # Run aggregation
    print("Aggregating results...")
    metrics = result_aggregator.aggregate_results(benchmark_id, artifacts_bucket, vms)
    
    if not metrics:
        print("No metrics collected!")
        sys.exit(1)
    
    print(f"\nCollected metrics for {len(metrics)} tests:")
    for test_id, test_metrics in metrics.items():
        print(f"  Test {test_id}: {test_metrics}")
    
    # Generate report
    print("\nGenerating report...")
    report_file = f"results/{benchmark_id}_report.txt"
    report_generator.generate_report(metrics, report_file)
    
    print(f"\nReport saved to: {report_file}")

if __name__ == "__main__":
    main()
