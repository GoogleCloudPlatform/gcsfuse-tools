#!/usr/bin/env python3
import argparse
import subprocess
import time
import sys
import os

def run_command(command, check=True, capture_output=True):
    try:
        result = subprocess.run(
            command,
            check=check,
            shell=True,
            text=True,
            capture_output=capture_output
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        if not check:
            return e
        raise

def get_pod_info(yaml_file):
    pod_name = None
    container_name = None
    
    # Simple parsing to avoid external dependencies
    with open(yaml_file, 'r') as f:
        lines = f.readlines()
    
    in_metadata = False
    in_containers = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped:
            continue
            
        if stripped == 'metadata:':
            in_metadata = True
            in_containers = False
            continue
        elif stripped == 'containers:':
            in_metadata = False
            in_containers = True
            continue
            
        if in_metadata:
            if stripped.startswith('name:'):
                pod_name = stripped.split(':', 1)[1].strip()
                in_metadata = False
        
        if in_containers:
            if stripped.startswith('- name:'):
                container_name = stripped.split(':', 1)[1].strip()
                in_containers = False
                
    return pod_name, container_name

def wait_for_pod_completion(pod_name, namespace, timeout=7200):
    print(f"Waiting for pod {pod_name} to complete...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        cmd = f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}'"
        result = run_command(cmd, check=False)
        if result.returncode != 0:
            # Pod might not be created yet
            time.sleep(5)
            continue
        
        phase = result.stdout.strip()
        if phase in ["Succeeded", "Failed"]:
            return phase
        
        time.sleep(10)
    raise TimeoutError(f"Pod {pod_name} did not complete within {timeout} seconds.")

def main():
    parser = argparse.ArgumentParser(description="Run pod benchmark iterations.")
    parser.add_argument("--iterations", type=int, required=True, help="Number of iterations.")
    parser.add_argument("--pod-spec", type=str, default="pod.yaml", help="Path to pod spec yaml.")
    parser.add_argument("--namespace", type=str, default="default", help="Kubernetes namespace.")
    parser.add_argument("--output-dir", type=str, default="logs", help="Directory to save logs.")
    
    args = parser.parse_args()

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    pod_name, container_name = get_pod_info(args.pod_spec)
    if not pod_name:
        pod_name = "orbax-script-pod"
    if not container_name:
        container_name = "orbax-container"
    
    print(f"Target Pod: {pod_name}, Container: {container_name}")

    for i in range(1, args.iterations + 1):
        print(f"\n--- Iteration {i}/{args.iterations} ---")
        
        # Ensure clean state
        run_command(f"kubectl delete pod {pod_name} -n {args.namespace} --ignore-not-found --wait=true", check=False)
        
        print(f"Applying {args.pod_spec}...")
        run_command(f"kubectl apply -f {args.pod_spec} -n {args.namespace}")
        
        try:
            phase = wait_for_pod_completion(pod_name, args.namespace)
            print(f"Pod finished with phase: {phase}")
        except Exception as e:
            print(f"Error waiting for pod: {e}")
        
        print("Fetching logs...")
        log_file = os.path.join(args.output_dir, f"iteration_{i}.log")
        
        # Fetch logs for the specific container
        cmd = f"kubectl logs {pod_name} -c {container_name} -n {args.namespace}"
        res = run_command(cmd, check=False)
        
        with open(log_file, "w") as f:
            if res.returncode != 0:
                print(f"Failed to get logs for container {container_name}. Error: {res.stderr}")
                f.write(f"Failed to get logs: {res.stderr}\n")
            else:
                f.write(res.stdout)
        
        print(f"Logs saved to {log_file}")

        print("Deleting pod...")
        run_command(f"kubectl delete -f {args.pod_spec} -n {args.namespace} --wait=true")

if __name__ == "__main__":
    main()