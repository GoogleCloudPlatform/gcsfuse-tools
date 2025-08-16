import subprocess
import json
import shlex
import datetime
from urllib.parse import urlencode

def get_vm_cpu_utilization(instance_name: str, project: str, zone: str, start_time, end_time):
    """
    Fetches the AVERAGE VM CPU utilization metric from Google Cloud Monitoring
    over the specified interval using curl and gcloud for auth.

    Args:
        instance_name: The name of the GCE VM instance.
        project: The Google Cloud project ID.
        zone: The zone where the instance is located.
        start_time: The start time for the metrics range (RFC3339 format, e.g., YYYY-MM-DDTHH:MM:SSZ).
        end_time: The end time for the metrics range (RFC3339 format, e.g., YYYY-MM-DDTHH:MM:SSZ).

    Returns:
        A float representing the average CPU utilization (e.g., 0.15 for 15%),
        or None if no data is returned.

    Raises:
        subprocess.CalledProcessError: If any command fails.
        ValueError: If instance ID, auth token, or duration cannot be retrieved/calculated.
        json.JSONDecodeError: If the output from curl is not valid JSON.
    """
    try:
        # 1. Get the Instance ID using gcloud
        get_id_command = [
            "gcloud", "compute", "instances", "describe", instance_name,
            "--project", project,
            "--zone", zone,
            "--format=value(id)"
        ]
        print(f"Running command: {' '.join(shlex.quote(arg) for arg in get_id_command)}")
        id_result = subprocess.run(get_id_command, capture_output=True, text=True, check=True)
        instance_id = id_result.stdout.strip()
        if not instance_id:
            raise ValueError(f"Could not retrieve instance ID for {instance_name}")
        print(f"Instance ID: {instance_id}")

        # 2. Get Auth Token using gcloud
        get_token_command = ["gcloud", "auth", "print-access-token"]
        print(f"Running command: {' '.join(shlex.quote(arg) for arg in get_token_command)}")
        token_result = subprocess.run(get_token_command, capture_output=True, text=True, check=True)
        auth_token = token_result.stdout.strip()
        if not auth_token:
            raise ValueError("Could not retrieve auth token")

        # 3. Calculate alignmentPeriod for the entire interval
        try:
            if start_time >= end_time:
                raise ValueError("start_time must be before end_time")
            duration_seconds = (end_time - start_time).total_seconds()
            alignment_period = f"{int(duration_seconds)}s"
        except ValueError as e:
            raise ValueError(f"Error parsing time strings: {e}")

        print(f"Calculated alignmentPeriod: {alignment_period}")

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
            "interval.startTime": str(start_time),
            "interval.endTime": str(end_time),
            "aggregation.alignmentPeriod": alignment_period,
            "aggregation.perSeriesAligner": "ALIGN_MEAN",
            "aggregation.crossSeriesReducer": "REDUCE_MEAN",
             "view": "FULL"
        }

        curl_command = ["curl", "-H", f"Authorization: Bearer {auth_token}", "-G", api_url]
        for key, value in params.items():
            curl_command.extend(["--data-urlencode", f"{key}={value}"])

        print(f"Running command: {' '.join(shlex.quote(arg) for arg in curl_command)}")
        metrics_result = subprocess.run(curl_command, capture_output=True, text=True, check=True)
        metrics_data = json.loads(metrics_result.stdout)

        # Extract the single average value
        if "timeSeries" in metrics_data and metrics_data["timeSeries"]:
            points = metrics_data["timeSeries"][0]["points"]
            if points:
                avg_value = points[0]["value"]["doubleValue"]
                return avg_value
        return None

    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        print(f"Stderr: {e.stderr}")
        print(f"Stdout: {e.stdout}")
        raise
    except json.JSONDecodeError as e:
        print(f"Failed to decode JSON output: {e}")
        raise
    except ValueError as e:
        print(e)
        raise
# Example Usage:
if __name__ == '__main__':
    try:
        # Replace with your actual VM details and time range
        vm_metrics = get_vm_cpu_utilization(
            instance_name="non-existent-vm",
            project="non-existent-project",
            zone="us-central1-a",
            start_time=datetime.datetime(2025, 8, 16, 9, 30, 0),
            end_time=datetime.datetime(2025, 8, 16, 10, 30, 0)
        )
        print("\nMetrics Output:")
        print(json.dumps(vm_metrics, indent=2))

    except Exception as e:
        print(f"An error occurred: {e}")
