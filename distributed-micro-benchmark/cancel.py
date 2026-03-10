# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cancel a running benchmark by creating a GCS cancel flag"""

import sys
import subprocess


def create_cancel_flag(benchmark_id, artifacts_bucket):
    """Create cancel flag in GCS"""
    cancel_path = f"gs://{artifacts_bucket}/{benchmark_id}/cancel"
    
    print(f"Creating cancellation flag: {cancel_path}")
    
    # Create empty file as cancel flag
    cmd = ['gsutil', 'cp', '-', cancel_path]
    process = subprocess.run(cmd, input=b'', capture_output=True)
    
    if process.returncode == 0:
        print(f"✓ Cancellation flag created successfully")
        print(f"\nWorkers will detect this flag and:")
        print(f"  1. Stop executing new tests")
        print(f"  2. Unmount GCSFuse")
        print(f"  3. Update manifest status to 'cancelled'")
        print(f"  4. Exit gracefully")
        print(f"\nNote: Active tests will complete before cancellation takes effect.")
        return True
    else:
        print(f"✗ Failed to create cancellation flag")
        print(f"Error: {process.stderr.decode()}")
        return False


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 cancel.py <benchmark_id> <artifacts_bucket>")
        print("\nExample:")
        print("  python3 cancel.py benchmark-1767457751 princer-working-dirs")
        sys.exit(1)
    
    benchmark_id = sys.argv[1]
    artifacts_bucket = sys.argv[2]
    
    print(f"========================================")
    print(f"Cancelling Benchmark")
    print(f"========================================")
    print(f"Benchmark ID: {benchmark_id}")
    print(f"Artifacts Bucket: {artifacts_bucket}")
    print(f"========================================\n")
    
    success = create_cancel_flag(benchmark_id, artifacts_bucket)
    
    if not success:
        sys.exit(1)
    
    print(f"\n========================================")
    print(f"Cancellation initiated successfully!")
    print(f"========================================")


if __name__ == "__main__":
    main()
