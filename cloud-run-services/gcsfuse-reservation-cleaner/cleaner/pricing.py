# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pricing catalog and cost estimation model for Google Compute Engine reservations."""

import re
from typing import Any, Dict, List, Optional

# Standard hours in a billing month (365 * 24 / 12 = 730)
HOURS_PER_MONTH = 730.0

# Base hourly on-demand machine type pricing catalog (USD)
BASE_PRICING: Dict[str, Dict[str, float]] = {
    # N1 series
    "n1-standard-1": {"default": 0.0475, "europe-west4": 0.0523, "asia-northeast1": 0.0570},
    "n1-standard-2": {"default": 0.0950, "europe-west4": 0.1045, "asia-northeast1": 0.1140},
    "n1-standard-4": {"default": 0.1900, "europe-west4": 0.2090, "asia-northeast1": 0.2280},
    "n1-standard-8": {"default": 0.3800, "europe-west4": 0.4180, "asia-northeast1": 0.4560},
    "n1-standard-16": {"default": 0.7600, "europe-west4": 0.8360, "asia-northeast1": 0.9120},
    "n1-standard-32": {"default": 1.5200, "europe-west4": 1.6720, "asia-northeast1": 1.8240},
    "n1-standard-64": {"default": 3.0400, "europe-west4": 3.3440, "asia-northeast1": 3.6480},
    "n1-standard-96": {"default": 4.5600, "europe-west4": 5.0160, "asia-northeast1": 5.4720},
    "n1-highmem-2": {"default": 0.1184, "europe-west4": 0.1302, "asia-northeast1": 0.1421},
    "n1-highmem-4": {"default": 0.2368, "europe-west4": 0.2605, "asia-northeast1": 0.2842},
    "n1-highmem-8": {"default": 0.4736, "europe-west4": 0.5210, "asia-northeast1": 0.5683},
    "n1-highmem-16": {"default": 0.9472, "europe-west4": 1.0419, "asia-northeast1": 1.1366},
    "n1-highmem-32": {"default": 1.8944, "europe-west4": 2.0838, "asia-northeast1": 2.2733},
    "n1-highmem-64": {"default": 3.7888, "europe-west4": 4.1677, "asia-northeast1": 4.5466},
    "n1-highmem-96": {"default": 5.6832, "europe-west4": 6.2515, "asia-northeast1": 6.8198},
    "n1-highcpu-2": {"default": 0.0709, "europe-west4": 0.0780, "asia-northeast1": 0.0851},
    "n1-highcpu-4": {"default": 0.1418, "europe-west4": 0.1560, "asia-northeast1": 0.1702},
    "n1-highcpu-8": {"default": 0.2836, "europe-west4": 0.3120, "asia-northeast1": 0.3403},
    "n1-highcpu-16": {"default": 0.5672, "europe-west4": 0.6239, "asia-northeast1": 0.6806},
    "n1-highcpu-32": {"default": 1.1344, "europe-west4": 1.2478, "asia-northeast1": 1.3613},
    "n1-highcpu-64": {"default": 2.2688, "europe-west4": 2.4957, "asia-northeast1": 2.7226},
    "n1-highcpu-96": {"default": 3.4032, "europe-west4": 3.7435, "asia-northeast1": 4.0838},
    # N2 series
    "n2-standard-2": {"default": 0.0971, "europe-west4": 0.1068, "asia-northeast1": 0.1165},
    "n2-standard-4": {"default": 0.1942, "europe-west4": 0.2136, "asia-northeast1": 0.2330},
    "n2-standard-8": {"default": 0.3885, "europe-west4": 0.4273, "asia-northeast1": 0.4661},
    "n2-standard-16": {"default": 0.7769, "europe-west4": 0.8546, "asia-northeast1": 0.9323},
    "n2-standard-32": {"default": 1.5539, "europe-west4": 1.7093, "asia-northeast1": 1.8646},
    "n2-standard-48": {"default": 2.3308, "europe-west4": 2.5639, "asia-northeast1": 2.7970},
    "n2-standard-64": {"default": 3.1077, "europe-west4": 3.4185, "asia-northeast1": 3.7293},
    "n2-standard-80": {"default": 3.8847, "europe-west4": 4.2731, "asia-northeast1": 4.6616},
    "n2-standard-96": {"default": 4.6616, "europe-west4": 5.1278, "asia-northeast1": 5.5939},
    "n2-standard-128": {"default": 6.2155, "europe-west4": 6.8370, "asia-northeast1": 7.4586},
    "n2-highmem-2": {"default": 0.1311, "europe-west4": 0.1442, "asia-northeast1": 0.1573},
    "n2-highmem-4": {"default": 0.2622, "europe-west4": 0.2884, "asia-northeast1": 0.3146},
    "n2-highmem-8": {"default": 0.5244, "europe-west4": 0.5768, "asia-northeast1": 0.6293},
    "n2-highmem-16": {"default": 1.0488, "europe-west4": 1.1537, "asia-northeast1": 1.2586},
    "n2-highmem-32": {"default": 2.0976, "europe-west4": 2.3074, "asia-northeast1": 2.5171},
    "n2-highmem-48": {"default": 3.1464, "europe-west4": 3.4610, "asia-northeast1": 3.7757},
    "n2-highmem-64": {"default": 4.1952, "europe-west4": 4.6147, "asia-northeast1": 5.0342},
    "n2-highmem-80": {"default": 5.2440, "europe-west4": 5.7684, "asia-northeast1": 6.2928},
    "n2-highmem-96": {"default": 6.2928, "europe-west4": 6.9221, "asia-northeast1": 7.5514},
    "n2-highmem-128": {"default": 8.3904, "europe-west4": 9.2294, "asia-northeast1": 10.0685},
    # N2D series
    "n2d-standard-2": {"default": 0.0845, "europe-west4": 0.0930, "asia-northeast1": 0.1014},
    "n2d-standard-4": {"default": 0.1690, "europe-west4": 0.1859, "asia-northeast1": 0.2028},
    "n2d-standard-8": {"default": 0.3380, "europe-west4": 0.3718, "asia-northeast1": 0.4056},
    "n2d-standard-16": {"default": 0.6760, "europe-west4": 0.7436, "asia-northeast1": 0.8112},
    "n2d-standard-32": {"default": 1.3520, "europe-west4": 1.4872, "asia-northeast1": 1.6224},
    "n2d-standard-48": {"default": 2.0280, "europe-west4": 2.2308, "asia-northeast1": 2.4336},
    "n2d-standard-64": {"default": 2.7040, "europe-west4": 2.9744, "asia-northeast1": 3.2448},
    "n2d-standard-80": {"default": 3.3800, "europe-west4": 3.7180, "asia-northeast1": 4.0560},
    "n2d-standard-96": {"default": 4.0560, "europe-west4": 4.4616, "asia-northeast1": 4.8672},
    "n2d-standard-128": {"default": 5.4080, "europe-west4": 5.9488, "asia-northeast1": 6.4896},
    "n2d-standard-224": {"default": 9.4640, "europe-west4": 10.4104, "asia-northeast1": 11.3568},
    # E2 series
    "e2-micro": {"default": 0.0084, "europe-west4": 0.0092, "asia-northeast1": 0.0101},
    "e2-small": {"default": 0.0168, "europe-west4": 0.0185, "asia-northeast1": 0.0202},
    "e2-medium": {"default": 0.0336, "europe-west4": 0.0370, "asia-northeast1": 0.0403},
    "e2-standard-2": {"default": 0.0671, "europe-west4": 0.0738, "asia-northeast1": 0.0805},
    "e2-standard-4": {"default": 0.1342, "europe-west4": 0.1477, "asia-northeast1": 0.1611},
    "e2-standard-8": {"default": 0.2685, "europe-west4": 0.2953, "asia-northeast1": 0.3222},
    "e2-standard-16": {"default": 0.5370, "europe-west4": 0.5907, "asia-northeast1": 0.6444},
    "e2-standard-32": {"default": 1.0740, "europe-west4": 1.1814, "asia-northeast1": 1.2888},
    # C2 / C2D / C3 series
    "c2-standard-4": {"default": 0.2088, "europe-west4": 0.2297, "asia-northeast1": 0.2506},
    "c2-standard-8": {"default": 0.4176, "europe-west4": 0.4594, "asia-northeast1": 0.5011},
    "c2-standard-16": {"default": 0.8352, "europe-west4": 0.9187, "asia-northeast1": 1.0022},
    "c2-standard-30": {"default": 1.5660, "europe-west4": 1.7226, "asia-northeast1": 1.8792},
    "c2-standard-60": {"default": 3.1320, "europe-west4": 3.4452, "asia-northeast1": 3.7584},
    "c3-standard-4": {"default": 0.2084, "europe-west4": 0.2292, "asia-northeast1": 0.2501},
    "c3-standard-8": {"default": 0.4168, "europe-west4": 0.4585, "asia-northeast1": 0.5002},
    "c3-standard-22": {"default": 1.1462, "europe-west4": 1.2608, "asia-northeast1": 1.3754},
    "c3-standard-44": {"default": 2.2924, "europe-west4": 2.5216, "asia-northeast1": 2.7509},
    "c3-standard-88": {"default": 4.5848, "europe-west4": 5.0433, "asia-northeast1": 5.5018},
    "c3-standard-176": {"default": 9.1696, "europe-west4": 10.0866, "asia-northeast1": 11.0035},
    # A2 series (GPU instances)
    "a2-highgpu-1g": {"default": 3.6732, "europe-west4": 4.0405, "asia-northeast1": 4.4078},
    "a2-highgpu-2g": {"default": 7.3464, "europe-west4": 8.0810, "asia-northeast1": 8.8157},
    "a2-highgpu-4g": {"default": 14.6928, "europe-west4": 16.1621, "asia-northeast1": 17.6314},
    "a2-highgpu-8g": {"default": 29.3856, "europe-west4": 32.3242, "asia-northeast1": 35.2627},
    "a2-megagpu-16g": {"default": 55.7256, "europe-west4": 61.2982, "asia-northeast1": 66.8707},
    # A3 series
    "a3-highgpu-8g": {"default": 31.0000, "europe-west4": 34.1000, "asia-northeast1": 37.2000},
    "a3-megagpu-8g": {"default": 33.0000, "europe-west4": 36.3000, "asia-northeast1": 39.6000},
    # G2 series
    "g2-standard-4": {"default": 0.7020, "europe-west4": 0.7722, "asia-northeast1": 0.8424},
    "g2-standard-8": {"default": 1.1040, "europe-west4": 1.2144, "asia-northeast1": 1.3248},
    "g2-standard-12": {"default": 1.5060, "europe-west4": 1.6566, "asia-northeast1": 1.8072},
    "g2-standard-16": {"default": 1.9080, "europe-west4": 2.0988, "asia-northeast1": 2.2896},
    "g2-standard-24": {"default": 2.8140, "europe-west4": 3.0954, "asia-northeast1": 3.3768},
    "g2-standard-32": {"default": 3.6180, "europe-west4": 3.9798, "asia-northeast1": 4.3416},
    "g2-standard-48": {"default": 5.4270, "europe-west4": 5.9697, "asia-northeast1": 6.5124},
    "g2-standard-96": {"default": 10.8540, "europe-west4": 11.9394, "asia-northeast1": 13.0248},
    # T2D series
    "t2d-standard-1": {"default": 0.0422, "europe-west4": 0.0465, "asia-northeast1": 0.0507},
    "t2d-standard-2": {"default": 0.0845, "europe-west4": 0.0929, "asia-northeast1": 0.1014},
    "t2d-standard-4": {"default": 0.1690, "europe-west4": 0.1859, "asia-northeast1": 0.2028},
    "t2d-standard-8": {"default": 0.3380, "europe-west4": 0.3718, "asia-northeast1": 0.4056},
    "t2d-standard-16": {"default": 0.6760, "europe-west4": 0.7436, "asia-northeast1": 0.8112},
    "t2d-standard-32": {"default": 1.3520, "europe-west4": 1.4872, "asia-northeast1": 1.6224},
    "t2d-standard-48": {"default": 2.0280, "europe-west4": 2.2308, "asia-northeast1": 2.4336},
    "t2d-standard-60": {"default": 2.5350, "europe-west4": 2.7885, "asia-northeast1": 3.0420},
}

# Standalone accelerator hourly pricing (USD)
ACCELERATOR_PRICING: Dict[str, Dict[str, float]] = {
    "nvidia-tesla-t4": {"default": 0.3500, "europe-west4": 0.3850, "asia-northeast1": 0.4200},
    "nvidia-tesla-v100": {"default": 2.4800, "europe-west4": 2.7280, "asia-northeast1": 2.9760},
    "nvidia-tesla-p100": {"default": 1.4600, "europe-west4": 1.6060, "asia-northeast1": 1.7520},
    "nvidia-tesla-p4": {"default": 0.6000, "europe-west4": 0.6600, "asia-northeast1": 0.7200},
    "nvidia-tesla-k80": {"default": 0.4500, "europe-west4": 0.4950, "asia-northeast1": 0.5400},
    "nvidia-a100-80gb": {"default": 3.6700, "europe-west4": 4.0370, "asia-northeast1": 4.4040},
    "nvidia-tesla-a100": {"default": 2.9300, "europe-west4": 3.2230, "asia-northeast1": 3.5160},
    "nvidia-l4": {"default": 0.5600, "europe-west4": 0.6160, "asia-northeast1": 0.6720},
    "nvidia-h100-80gb": {"default": 8.0000, "europe-west4": 8.8000, "asia-northeast1": 9.6000},
}

# Fallback cost per vCPU hour when machine type is not found in table
FALLBACK_VCPU_HOURLY_RATE = 0.0485


def _extract_region_from_zone(zone: Optional[str]) -> str:
    """Extract GCP region name from a zone identifier (e.g., 'us-central1-a' -> 'us-central1')."""
    if not zone:
        return "default"
    clean_zone = zone.strip().lower()
    if "/" in clean_zone:
        clean_zone = clean_zone.split("/")[-1]
    # Remove zone suffix (e.g. '-a', '-b')
    parts = clean_zone.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
        return parts[0]
    return clean_zone


def _extract_vcpus_from_machine_type(machine_type: str) -> int:
    """Extract approximate vCPU count from machine type string."""
    if not machine_type:
        return 1
    # Check for custom machine pattern like 'custom-16-65536' or 'n2-custom-16-65536'
    custom_match = re.search(r"custom-(\d+)-\d+", machine_type)
    if custom_match:
        try:
            return int(custom_match.group(1))
        except ValueError:
            pass
    # Check for suffix like '-4', '-8', '-16'
    match = re.search(r"-(\d+)$", machine_type)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    if "micro" in machine_type:
        return 1
    if "small" in machine_type:
        return 1
    if "medium" in machine_type:
        return 1
    return 2



def estimate_hourly_rate(
    machine_type: str,
    zone: Optional[str] = None,
    accelerators: Optional[List[Dict[str, Any]]] = None,
) -> float:
    """Estimate on-demand hourly rate in USD for a given GCE machine type and accelerators."""
    if not machine_type:
        return 0.0

    # Clean machine type (e.g. from full URL)
    clean_machine_type = machine_type.strip().lower()
    if "/" in clean_machine_type:
        clean_machine_type = clean_machine_type.split("/")[-1]

    region = _extract_region_from_zone(zone)

    # 1. Base machine type rate
    machine_pricing = BASE_PRICING.get(clean_machine_type)
    if machine_pricing:
        base_rate = machine_pricing.get(region, machine_pricing.get("default", 0.0))
    else:
        # Fallback based on vCPU count
        vcpus = _extract_vcpus_from_machine_type(clean_machine_type)
        base_rate = vcpus * FALLBACK_VCPU_HOURLY_RATE

    # 2. Add accelerator rates if specified
    accel_rate = 0.0
    if accelerators:
        for acc in accelerators:
            acc_type = acc.get("acceleratorType", "")
            if "/" in acc_type:
                acc_type = acc_type.split("/")[-1]
            acc_type = acc_type.lower()
            acc_count = int(acc.get("acceleratorCount", 1))

            pricing_dict = ACCELERATOR_PRICING.get(acc_type, {})
            rate_per_acc = pricing_dict.get(region, pricing_dict.get("default", 0.35))
            accel_rate += rate_per_acc * acc_count

    return round(base_rate + accel_rate, 4)


def calculate_monthly_cost(hourly_rate: float, count: int = 1) -> float:
    """Calculate monthly cost based on 730 hours/month and reservation capacity."""
    if hourly_rate < 0 or count < 0:
        return 0.0
    return round(hourly_rate * count * HOURS_PER_MONTH, 2)


def calculate_annual_cost(monthly_cost: float) -> float:
    """Calculate annual cost given a monthly cost."""
    if monthly_cost < 0:
        return 0.0
    return round(monthly_cost * 12.0, 2)


def format_currency(amount: float) -> str:
    """Format float into USD currency string."""
    return f"${amount:,.2f}"
