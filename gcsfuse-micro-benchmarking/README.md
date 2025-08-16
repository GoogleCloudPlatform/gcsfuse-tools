## Steps to run the benchmark

### 1. Setup the tool
```
git clone https://github.com/GoogleCloudPlatform/gcsfuse-tools.git
cd gcsfuse-tools
git checkout gcsfuse-micro-benchmarking
cd gcsfuse-micro-benchmarking
```

### 2. Setup the environment
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Start the SSH agent and load your GCE private key to enable passwordless SSH access to your VMs.
```
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/google_compute_engine
```

### 4. Setup the configurations as per your requirement
For custom benchmark runs, according to your usecase, modify either of
* fio_job_cases.csv 
    - For executing mixed testcases such as the published GCSFuse benchmarks.
* jobfile.fio
    - For executing fio jobs with different global configurations 

For more details on setting the configurations as per requirement, follow the guidelines [here](https://docs.google.com/document/d/1yI0ApvDC8SDnpzAmz95kbf75h1G-me41Xa1XH7zecF0/edit?usp=sharing)

### 5. (Optional) Start the tmux session
```
tmux new -s benchmarking-session
```
Running the script can be blocking and any failure (for e.g. SSH issues of the local machine from which the script is triggered, etc.) can cause the entire script to retriggered , thus it is advised to run the benchmark in a tmux session.

### 5. Run the benchmark 
```
python3  main.py --benchmark_id={benchmark_id} --config_filepath={path/to/benchmark_config_file} --bench_type={bench_type}
```

### 6. Cleanup
Whenever necessary, a GCE VM of name `{benchmark_id}-vm` and a GCS bucket of name `{benchmark_id}-bkt}` is created at runtime.

Cleanup is handled as part of the script itself if the resources are created in runtime and explicitly stated via the config to delete after use. In case of tool failure, the resources are persisted.


### 7. Benchmark Results
The results from the benchmark run is available at the location `results/{benchmark_id}_result.txt}` locally, at the end of benchmarking and remotely, in the artifacts bucket at `gs://{ARTIFACTS_BUCKET}/{benchmark_id}/result.json`

The raw results are also persisted in the artifacts bucket at  `gs://{ARTIFACTS_BUCKET}/{benchmark_id}/raw-results/`

### 8. Compare Benchmark Runs
With identical benchmark runs for baseline/topline/feature , the results can be compared using the following steps:
```
cd compare_runs
python3 main.py --benchmark_ids=id1,id2,... --output_dir=output_dir
```

Visual plots are generated and stored under `output_dir/`

#### Note: 
* The benchmark_id passed as argument to the script, is used for creating the test bucket and VM instance if required, hence ensure the benchmark_id is complaint with the naming guidelines for such resources
* In case the GCE VM instance is pre-existing, please ensure that the VM scope is set to 
`https://www.googleapis.com/auth/cloud-platform` for full access to all Cloud APIs
* For future reference, the benchmark ids are also stored in the artifacts bucket at `gs://{ARTIFACTS_BUCKET}/${user}$/runs.json` . The runs can be labelled by setting the bench_type flag passed to the script`.
