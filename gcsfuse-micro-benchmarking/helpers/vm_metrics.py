import subprocess
import json
import shlex
import datetime
from urllib.parse import urlencode

def get_vm_cpu_utilization_points(instance_name: str, project: str, zone: str,
                           start_time: datetime.datetime, end_time: datetime.datetime):
    """
    Fetches the AVERAGE VM CPU utilization metric from Google Cloud Monitoring
    over the specified interval using curl and gcloud for auth.

    Args:
        instance_name: The name of the GCE VM instance.
        project: The Google Cloud project ID.
        zone: The zone where the instance is located.
        start_time: A timezone-aware datetime object representing the start of the interval.
        end_time: A timezone-aware datetime object representing the end of the interval.

    Returns:
        A float representing the average CPU utilization (e.g., 0.15 for 15%),
        or None if no data is returned.

    Raises:
        subprocess.CalledProcessError: If any command fails.
        ValueError: If instance ID, auth token, or duration cannot be retrieved/calculated,
                    or if time inputs are invalid.
        json.JSONDecodeError: If the output from curl is not valid JSON.
    """
    try:
        # Input validation for times
        if not isinstance(start_time, datetime.datetime) or not isinstance(end_time, datetime.datetime):
            raise ValueError("start_time and end_time must be datetime objects")
        if start_time.tzinfo is None or start_time.tzinfo.utcoffset(start_time) is None:
            raise ValueError("start_time must be timezone-aware")
        if end_time.tzinfo is None or end_time.tzinfo.utcoffset(end_time) is None:
            raise ValueError("end_time must be timezone-aware")

        if start_time >= end_time:
            raise ValueError("start_time must be before end_time")

        # 1. Get the Instance ID using gcloud
        get_id_command = [
            "gcloud", "compute", "instances", "describe", instance_name,
            "--project", project,
            "--zone", zone,
            "--format=value(id)"
        ]
        # print(f"Running command: {' '.join(shlex.quote(arg) for arg in get_id_command)}")
        id_result = subprocess.run(get_id_command, capture_output=True, text=True, check=True, timeout=60)
        instance_id = id_result.stdout.strip()
        if not instance_id:
            raise ValueError(f"Could not retrieve instance ID for {instance_name}")
        # print(f"Instance ID: {instance_id}")

        # 2. Get Auth Token using gcloud
        get_token_command = ["gcloud", "auth", "print-access-token"]
        # print(f"Running command: {' '.join(shlex.quote(arg) for arg in get_token_command)}")
        token_result = subprocess.run(get_token_command, capture_output=True, text=True, check=True, timeout=60)
        auth_token = token_result.stdout.strip()
        if not auth_token:
            raise ValueError("Could not retrieve auth token")

        # 3. Calculate alignmentPeriod for the entire interval
        duration_seconds = (end_time - start_time).total_seconds()
        alignment_period = f"{int(duration_seconds)}s"
        # print(f"Calculated alignmentPeriod: {alignment_period}")

        # Format timestamps to RFC 3339 string for the API call
        start_time_str = start_time.isoformat()
        end_time_str = end_time.isoformat()

        # 4. Fetch AVERAGE CPU Utilization metrics using curl and the REST API
        metric_filter = (
            f'metric.type="compute.googleapis.com/instance/cpu/utilization" AND '
            f'resource.type="gce_instance" AND '
            f'resource.labels.instance_id="{instance_id}" AND '
            f'resource.labels.zone="{zone}"'
        )
        api_url = f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"

        params = {
            "filter": metric_filter,
            "interval.startTime": start_time_str,
            "interval.endTime": end_time_str,
             "view": "FULL"
        }

        curl_command = ["curl", "-H", f"Authorization: Bearer {auth_token}", "-G", api_url]
        for key, value in params.items():
            curl_command.extend(["--data-urlencode", f"{key}={value}"])

        # print(f"Running command: {' '.join(shlex.quote(arg) for arg in curl_command)}")
        metrics_result = subprocess.run(curl_command, capture_output=True, text=True, check=True, timeout=120)
        metrics_data = json.loads(metrics_result.stdout)

        cpu_values = []
        if "timeSeries" in metrics_data and metrics_data["timeSeries"]:
            points = metrics_data["timeSeries"][0]["points"]
            for point in points:
                cpu_values.append(point["value"]["doubleValue"])
        return cpu_values

    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        print(f"Stderr: {e.stderr}")
        print(f"Stdout: {e.stdout}")
        raise
    except subprocess.TimeoutExpired as e:
        print(f"Command timed out: {e}")
        raise
    except json.JSONDecodeError as e:
        print(f"Failed to decode JSON output: {e}")
        raise
    except ValueError as e:
        print(f"Value error: {e}")
        raise
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        raise

# Example Usage:
if __name__ == '__main__':
    try:
        # Replace with your actual VM details and time range
        vm_metrics = get_vm_cpu_utilization(
            instance_name="anu8q860ng2bh-vm",
            project="gcs-fuse-test",
            zone="us-west4-a",
            # Add tzinfo to make the datetime objects timezone-aware
            start_time=datetime.datetime(2025, 8, 20, 9, 30, 0, tzinfo=datetime.timezone.utc),
            end_time=datetime.datetime(2025, 8, 20, 10, 32, 0, tzinfo=datetime.timezone.utc)
        )
        print("\nMetrics Output:")
        print(json.dumps(vm_metrics, indent=2))

    except Exception as e:
        print(f"An error occurred: {e}")
