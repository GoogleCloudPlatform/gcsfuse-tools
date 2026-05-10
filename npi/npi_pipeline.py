import kfp
from kfp import dsl
from kfp.dsl import Dataset, Input, Output, Artifact

@dsl.component(base_image="google/cloud-sdk:latest")
def run_benchmark_op(
    execution_mode: str,
    bucket_name: str,
    project_id: str,
    bq_dataset_id: str,
    cluster_name: str = "",
    location: str = "",
    vm_name: str = "",
    zone: str = "",
    buffer_mount_path: str = "/tmp/buffer",
    iterations: int = 5,
    image_version: str = "latest",
    is_rapid_bucket: bool = False,
    run_file_cache_test: bool = False,
    file_cache_size_mb: int = 2097152,
    branch_name: str = "main",
):
    import subprocess
    import sys
    import os
    
    if execution_mode == "GKE":
        print("Running in GKE mode...")
        # Clone the repo to get the script
        print("Cloning gcsfuse-tools repo...")
        res = subprocess.run(["git", "clone", "https://github.com/GoogleCloudPlatform/gcsfuse-tools.git"], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to clone repo:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
            
        os.chdir("gcsfuse-tools")
        print(f"Checking out branch: {branch_name}")
        res = subprocess.run(["git", "checkout", branch_name], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to checkout branch {branch_name}:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
        os.chdir("npi")
        
        # Fetch credentials for GKE cluster
        print(f"Fetching credentials for GKE cluster: {cluster_name} in {location}")
        res = subprocess.run([
            "gcloud", "container", "clusters", "get-credentials", cluster_name,
            "--location", location, "--project", project_id
        ], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to fetch cluster credentials:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
            
        print("Running benchmark script...")
        cmd = [
            "python3", "npi_gke.py",
            "--bucket-name", bucket_name,
            "--project-id", project_id,
            "--bq-dataset-id", bq_dataset_id,
            "--iterations", str(iterations),
            "--image-version", image_version,
            "--cluster-name", cluster_name,
            "--location", location
        ]
        
        if is_rapid_bucket:
            cmd.append("--is-rapid-bucket")
        if run_file_cache_test:
            cmd.append("--run-file-cache-test")
            cmd.extend(["--file-cache-size-mb", str(file_cache_size_mb)])
            
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(res.stdout)
        if res.returncode != 0:
            print(f"Benchmark failed:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
            
    elif execution_mode == "GCE":
        print(f"Running in GCE mode on VM: {vm_name} in {zone}...")
        
        # Command to run on the VM
        remote_cmd = (
            f"if [ ! -d 'gcsfuse-tools' ]; then git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git; fi && "
            f"cd gcsfuse-tools && git checkout {branch_name} && cd npi && "
            f"python3 npi.py --bucket-name {bucket_name} --project-id {project_id} "
            f"--bq-dataset-id {bq_dataset_id} --buffer-mount-path {buffer_mount_path} "
            f"--iterations {iterations} --image-version {image_version}"
        )
        
        if is_rapid_bucket:
            remote_cmd += " --is-rapid-bucket"
        if run_file_cache_test:
            remote_cmd += f" --file-cache-size-mb {file_cache_size_mb}"
            
        cmd = [
            "gcloud", "compute", "ssh", vm_name,
            "--zone", zone,
            "--project", project_id,
            "--command", remote_cmd
        ]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(res.stdout)
        if res.returncode != 0:
            print(f"GCE Benchmark failed:\n{res.stderr}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown execution mode: {execution_mode}", file=sys.stderr)
        sys.exit(1)

@dsl.component(base_image="python:3.9")
def analyze_results_op(
    project_id: str,
    bq_dataset_id: str,
    report: Output[Artifact]
):
    from google.cloud import bigquery
    import json
    
    client = bigquery.Client(project=project_id)
    
    # List tables in the dataset
    tables_query = f"SELECT table_id FROM `{project_id}.{bq_dataset_id}.__TABLES__` WHERE table_id LIKE 'fio_%'"
    tables_job = client.query(tables_query)
    tables = [row.table_id for row in tables_job.result()]
    
    report_content = "# Benchmark Analysis Report\n\n"
    
    if not tables:
        report_content += "No benchmark result tables found (starting with 'fio_').\n"
    else:
        for table_id in tables:
            report_content += f"## Table: {table_id}\n\n"
            
            # Query to extract key metrics from JSON
            # Assuming standard FIO JSON structure where jobs[0] contains the main stats.
            query = f"""
                SELECT
                  run_timestamp,
                  iteration,
                  JSON_VALUE(fio_json_output, '$.jobs[0].jobname') as job_name,
                  JSON_VALUE(fio_json_output, '$.jobs[0].read.bw') as read_bw_kib,
                  JSON_VALUE(fio_json_output, '$.jobs[0].write.bw') as write_bw_kib,
                  JSON_VALUE(fio_json_output, '$.jobs[0].read.iops') as read_iops,
                  JSON_VALUE(fio_json_output, '$.jobs[0].write.iops') as write_iops
                FROM
                  `{project_id}.{bq_dataset_id}.{table_id}`
                ORDER BY
                  run_timestamp DESC, iteration ASC
                LIMIT 5
            """
            
            try:
                query_job = client.query(query)
                results = query_job.result()
                
                report_content += "| Timestamp | Iter | Job Name | Read BW (MiB/s) | Write BW (MiB/s) | Read IOPS | Write IOPS |\n"
                report_content += "|---|---|---|---|---|---|---|\n"
                
                for row in results:
                    read_bw_mib = float(row.read_bw_kib) / 1024.0 if row.read_bw_kib else 0.0
                    write_bw_mib = float(row.write_bw_kib) / 1024.0 if row.write_bw_kib else 0.0
                    
                    report_content += (
                        f"| {row.run_timestamp} | {row.iteration} | {row.job_name} | "
                        f"{read_bw_mib:.2f} | {write_bw_mib:.2f} | "
                        f"{row.read_iops or 0} | {row.write_iops or 0} |\n"
                    )
                report_content += "\n"
            except Exception as e:
                report_content += f"Failed to query table {table_id}: {e}\n\n"
        
    with open(report.path, "w") as f:
        f.write(report_content)

@dsl.pipeline(
    name="gcsfuse-npi-pipeline",
    description="Pipeline for running GCSFuse NPI benchmarks and analysis"
)
def gcsfuse_npi_pipeline(
    bucket_name: str,
    project_id: str,
    bq_dataset_id: str,
    execution_mode: str = "GKE",
    cluster_name: str = "",
    location: str = "",
    vm_name: str = "",
    zone: str = "",
    buffer_mount_path: str = "/tmp/buffer",
    iterations: int = 5,
    image_version: str = "latest",
    is_rapid_bucket: bool = False,
    run_file_cache_test: bool = False,
    file_cache_size_mb: int = 2097152,
    branch_name: str = "main"
):
    run_bench = run_benchmark_op(
        execution_mode=execution_mode,
        bucket_name=bucket_name,
        project_id=project_id,
        bq_dataset_id=bq_dataset_id,
        cluster_name=cluster_name,
        location=location,
        vm_name=vm_name,
        zone=zone,
        buffer_mount_path=buffer_mount_path,
        iterations=iterations,
        image_version=image_version,
        is_rapid_bucket=is_rapid_bucket,
        run_file_cache_test=run_file_cache_test,
        file_cache_size_mb=file_cache_size_mb,
        branch_name=branch_name
    )
    
    analyze = analyze_results_op(
        project_id=project_id,
        bq_dataset_id=bq_dataset_id
    )
    analyze.after(run_bench)

if __name__ == "__main__":
    kfp.compiler.Compiler().compile(
        pipeline_func=gcsfuse_npi_pipeline,
        package_path="gcsfuse_npi_pipeline.yaml"
    )
