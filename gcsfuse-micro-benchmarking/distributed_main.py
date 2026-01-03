import argparse
import sys
from helpers import helper, upload, rationalize, validate, bucket, environment, record_bench_id, parse_results, generate_report
import os
import shutil


ARTIFACTS_BUCKET="princer-working-dirs"


def parse_args():
    parser = argparse.ArgumentParser(description="Run GCSFuse benchmarks in distributed mode.")

    parser.add_argument('--benchmark_id_prefix', type=str, help='Prefix for unique id for identifying runs...')
    parser.add_argument('--config_filepath', type=str, help='Path to the config file for the benchmarking tool', default='resources/default_bench_config.yml')
    parser.add_argument('--bench_type', type=str, help='(only for manual identification)Type of benchmark to run: c++ , baseline, etc.', default='feature')
    parser.add_argument('--instance-group', type=str, required=True, help='Name of managed instance group for distributed benchmarking')
    
    return parser.parse_args()


if __name__ == '__main__':

    args = parse_args()
    # Figure out the benchmark unique id    
    benchmark_id=(args.benchmark_id_prefix or '') + helper.generate_random_string(10)

    # Make directory to store the artifacts
    artifacts_dir=helper.generate_artifacts_dir(benchmark_id)

    print(f"---------Unique ID for benchmark run : {benchmark_id}-----------")
    
    cfg = helper.parse_bench_config(args.config_filepath)
    cfg = rationalize.rationalize_config(cfg)
    
    print(f"Config iterations value: {cfg.get('iterations')} (type: {type(cfg.get('iterations'))})")
    
    # Generating fio files, etc.
    helper.generate_benchmarking_resources(artifacts_dir, cfg)
    # Copy the benchmark specific resources to artifacts bucket. Available at gs://{ARTIFACTS_BUCKET}/{BENCHMARK_ID}
    helper.copy_directory_to_bucket(artifacts_dir, ARTIFACTS_BUCKET)

    # Handle test bucket - use provided bucket name or create one
    bucket_name = cfg.get("bench_env").get("gcs_bucket").get("bucket_name")
    if bucket_name:
        # Check if bucket exists
        if bucket.check_bucket_exists(bucket_name, cfg.get('bench_env').get('project')):
            print(f"Using existing GCS bucket: {bucket_name}")
        else:
            print(f"Creating GCS bucket: {bucket_name}")
            location = validate.extract_region_from_zone(cfg.get("bench_env").get("zone"))
            if not bucket.create_gcs_bucket(location, cfg.get('bench_env').get('project'), cfg.get("bench_env").get("gcs_bucket")):
                print("Error: Failed to create test bucket. Aborting.")
                sys.exit(1)
    else:
        # Create bucket with benchmark_id
        bucket_name = benchmark_id + "-bkt"
        cfg.get("bench_env").get("gcs_bucket")["bucket_name"] = bucket_name
        print(f"Creating GCS bucket: {bucket_name}")
        location = validate.extract_region_from_zone(cfg.get("bench_env").get("zone"))
        if not bucket.create_gcs_bucket(location, cfg.get('bench_env').get('project'), cfg.get("bench_env").get("gcs_bucket")):
            print("Error: Failed to create test bucket. Aborting.")
            sys.exit(1)

    print(f"\n========== DISTRIBUTED MODE ==========")
    print(f"Instance Group: {args.instance_group}")
    
    # Get VMs from instance group
    vms = helper.get_vms_from_instance_group(
        args.instance_group, 
        cfg.get('bench_env').get('zone'), 
        cfg.get('bench_env').get('project')
    )
    
    if not vms:
        print("Error: No VMs found in instance group. Aborting.")
        sys.exit(1)
    
    # Count test cases
    total_tests = helper.count_test_cases(args.config_filepath)
    if total_tests == 0:
        print("Error: No test cases found. Aborting.")
        sys.exit(1)
    
    # Distribute test cases across VMs
    test_ranges = helper.distribute_test_cases(total_tests, len(vms))
    
    print(f"\nDistribution Plan:")
    print(f"Total Tests: {total_tests}")
    print(f"Number of VMs: {len(vms)}")
    for i, (vm, test_range) in enumerate(zip(vms, test_ranges)):
        print(f"  {vm}: Tests {test_range}")
    print(f"======================================\n")
    
    # Update metadata and run script on each VM
    for vm, test_range in zip(vms, test_ranges):
        metadata_config = {
            'bucket': bucket_name,
            'artifacts_bucket': ARTIFACTS_BUCKET,
            'benchmark_id': benchmark_id,
            'iterations': cfg.get('iterations'),
            'reuse_same_mount': cfg.get('reuse_same_mount'),
            'test_id': test_range
        }
        
        # Calculate number of tests for this VM
        if '-' in test_range:
            start, end = test_range.split('-')
            num_tests = int(end) - int(start) + 1
        else:
            num_tests = 1
        
        print(f"Starting benchmark on VM {vm}:")
        print(f"  Test-id range: {test_range}")
        print(f"  Number of test cases: {num_tests}")
        print(f"  Iterations per test: {cfg.get('iterations')}")
        print(f"  Total FIO runs: {num_tests * cfg.get('iterations')}")
        print(f"  Metadata being set: iterations={metadata_config['iterations']}")
        
        # Update metadata
        if not environment.update_vm_metadata_parameter(
            vm, 
            cfg.get('bench_env').get('zone'), 
            cfg.get('bench_env').get('project'), 
            metadata_config
        ):
            print(f"Error: Failed to update metadata for VM {vm}")
            continue
        
        # Run script remotely
        if not environment.run_script_remotely(
            vm, 
            cfg.get('bench_env').get('zone'), 
            cfg.get('bench_env').get('project'), 
            cfg.get('bench_env').get('gce_env').get('startup_script')
        ):
            print(f"Error: Failed to run script on VM {vm}")
            continue
        
        print(f"Successfully started benchmark on VM {vm}")
    
    print(f"\nAll VMs started. Waiting for completion...")
    
    # Wait for all VMs to complete in distributed mode
    success_filepath = helper.construct_gcloud_path(ARTIFACTS_BUCKET, benchmark_id)
    success = helper.wait_for_all_vms_to_complete(ARTIFACTS_BUCKET, success_filepath, vms)
    
    if success:
        print(f"Benchmark run successful for id: {benchmark_id}")

        # The benchmark details are available for future reference in the ARTIFACTS bucket under user/
        record_bench_id.record_benchmark_id_for_user(benchmark_id, args.bench_type, ARTIFACTS_BUCKET)

        # Parse the benchmark results and generate the result file (skip VM metrics)
        raw_data_dir, metrics= parse_results.parse_benchmark_results(benchmark_id, ARTIFACTS_BUCKET, cfg, fetch_vm_metrics=False)
        
        upload.store_metrics_in_artifacts_bucket(metrics, benchmark_id, ARTIFACTS_BUCKET, cfg.get('bench_env').get('project'))
        
        output_dir = "./results/"
        os.makedirs(output_dir, exist_ok=True)
        output_filename=os.path.join(output_dir, args.bench_type+"_"+benchmark_id+"_result.txt")
        generate_report.pretty_print_metrics_table_concise(metrics, output_filename)
        
        # Upon successful benchmarking, cleanup the created resources
        print("Starting cleanup...")
       
        print(f"\nCleaning up temporary directory: {os.path.dirname(raw_data_dir)}")
        shutil.rmtree(os.path.dirname(raw_data_dir))

        print(f"\nCleaning up temporary directory: {artifacts_dir}")
        shutil.rmtree(artifacts_dir)

        # Don't delete bucket or VMs in distributed mode - they are pre-existing
        print("Distributed mode: Preserving instance group and bucket.")

        print("Cleanup complete.")
    else:
        print("Benchmarking failed. Preserving resources for debugging")
