import argparse
import sys
from helpers import helper, upload,rationalize, validate, bucket, environment, record_bench_id, parse_results, generate_report
import os
import shutil


ARTIFACTS_BUCKET="gcsfuse-perf-benchmark-artifacts"


def parse_args():
    parser = argparse.ArgumentParser(description="Run GCSFuse benchmarks.")

    parser.add_argument('--benchmark_id_prefix', type=str, help='Prefix for unique id for identifying runs...')
    parser.add_argument('--config_filepath', type=str, help='Path to the config file for the benchmarking tool', default='resources/default_bench_config.yml')
    parser.add_argument('--bench_type', type=str, help='(only for manual identification)Type of benchmark to run: c++ , baseline, etc.', default='feature')
    parser.add_argument('--instance-group', type=str, help='Name of managed instance group for distributed benchmarking')
    
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
    does_vm_exist, does_bkt_exist=validate.validate_existing_resources_if_any(cfg.get("zonal_benchmarking"),cfg.get("bench_env"))
    
    # Generating fio files, etc.
    helper.generate_benchmarking_resources(artifacts_dir, cfg)
    # Copy the benchmark specific resources to artifacts bucket. Avaiable at gs://{ARTIFACTS_BUCKET}/{BENCHMARK_ID}
    helper.copy_directory_to_bucket(artifacts_dir, ARTIFACTS_BUCKET)

    # Create the test bucket
    if does_bkt_exist:
        print("Using existing GCS bucket for benchmarking...")
    else:
        cfg.get("bench_env").get("gcs_bucket")["bucket_name"]= benchmark_id+"-bkt"
        location=validate.extract_region_from_zone(cfg.get("bench_env").get("zone"))
        if not bucket.create_gcs_bucket(location,cfg.get('bench_env').get('project'), cfg.get("bench_env").get("gcs_bucket")):
            print("Error: Failed to create test bucket. Aborting.")
            sys.exit(1)

    if does_vm_exist:
        print("Using existing GCE VM for benchmarking...")
    else:
        if cfg.get("bench_env").get("gce_env").get("vm_name") == "":
            cfg.get("bench_env").get("gce_env")["vm_name"]= benchmark_id+"-vm"
    
    # Check if distributed mode is enabled
    if args.instance_group:
        print(f"\n========== DISTRIBUTED MODE ENABLED ==========")
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
        print(f"==============================================\n")
        
        # Update metadata and run script on each VM
        for vm, test_range in zip(vms, test_ranges):
            metadata_config = {
                'bucket': cfg.get("bench_env").get("gcs_bucket").get('bucket_name'),
                'artifacts_bucket': ARTIFACTS_BUCKET,
                'benchmark_id': benchmark_id,
                'iterations': cfg.get('iterations'),
                'reuse_same_mount': cfg.get('reuse_same_mount'),
                'test_id': test_range
            }
            
            print(f"Starting benchmark on VM {vm} with test-id {test_range}...")
            
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
        
    else:
        # Single VM mode (existing logic)
        metadata_config={
            'bucket':cfg.get("bench_env").get("gcs_bucket").get('bucket_name'),
            'artifacts_bucket': ARTIFACTS_BUCKET,
            'benchmark_id': benchmark_id,
            'iterations': cfg.get('iterations'),
            'reuse_same_mount': cfg.get('reuse_same_mount')
        }
        # Startup the GCE VM
        if not environment.startup_benchmark_vm(cfg.get('bench_env').get('gce_env'), cfg.get('bench_env').get('zone'), cfg.get('bench_env').get('project'), metadata_config):
            print("Error: Failed to startup benchmark VM. Aborting.")
            sys.exit(1)
        
        # At the end of benchmark run, we write a failure.txt or success.txt
        success_filepath=helper.construct_gcloud_path(ARTIFACTS_BUCKET,benchmark_id)
        success=helper.wait_for_benchmark_to_complete(ARTIFACTS_BUCKET,success_filepath)
    
    if success:
        print(f"Benchmark run successful for id: {benchmark_id}")

        # The benchmark details are available for future reference in the ARTIFACTS bucket under user/
        record_bench_id.record_benchmark_id_for_user(benchmark_id, args.bench_type, ARTIFACTS_BUCKET)

        # Parse the benchmark results and generate the result file.
        raw_data_dir, metrics= parse_results.parse_benchmark_results(benchmark_id, ARTIFACTS_BUCKET, cfg)
        
        upload.store_metrics_in_artifacts_bucket(metrics, benchmark_id, ARTIFACTS_BUCKET, cfg.get('bench_env').get('project'))
        
        output_dir = "./results/"
        os.makedirs(output_dir, exist_ok=True)
        output_filename=os.path.join(output_dir, args.bench_type+"_"+benchmark_id+"_result.txt")
        generate_report.pretty_print_metrics_table(metrics,output_filename)
        
        # Upon successful benchmarking, cleanup the created resources
        print("Starting cleanup...")
       
        print(f"\nCleaning up temporary directory: {os.path.dirname(raw_data_dir)}")
        shutil.rmtree(os.path.dirname(raw_data_dir))

        print(f"\nCleaning up temporary directory: {artifacts_dir}")
        shutil.rmtree(artifacts_dir)

        if cfg.get('bench_env').get('delete_after_use') and (not does_bkt_exist):
            bucket_name= cfg.get("bench_env").get("gcs_bucket").get("bucket_name")
            print("Deleting the GCS bucket {} created for benchmarking...".format(bucket_name))
            bucket.delete_gcs_bucket(bucket_name, cfg.get("bench_env").get("project"))
        if cfg.get('bench_env').get('delete_after_use') and not does_vm_exist:
            vm_name= cfg.get("bench_env").get("gce_env").get("vm_name")
            print("Deleting the GCE instance {} created for benchmarking...".format(vm_name))
            environment.delete_gce_vm(vm_name, cfg.get("bench_env").get("zone"), cfg.get("bench_env").get("project"))

        print("Cleanup complete.")
    else:
        print("Benchmarking failed. Preserving resources for debugging")
