---
name: bucket-creation
description: Guides on provisioning Regional HNS (Hierarchical Namespace) and Zonal RAPID HNS Google Cloud Storage buckets for GCSFuse NPI benchmarking and conformance testing, enforcing uniform bucket-level access, verifying colocation, and inspecting bucket metadata.
---

# Google Cloud Storage Bucket Creation for GCSFuse NPI

This skill guides you through provisioning, configuring, and verifying Google Cloud Storage (GCS) buckets for GCSFuse New Product Introduction (NPI) conformance testing and performance benchmarking. It covers both standard Regional Hierarchical Namespace (HNS) buckets and Zonal RAPID HNS buckets.

---

## Prerequisites & Trigger Conditions

### Prerequisites
1. **Google Cloud SDK**: `gcloud` and `gcloud storage` CLI tools authenticated with permissions to create and manage storage buckets (`storage.buckets.create`, `storage.buckets.get`, `storage.buckets.update`).
2. **GCP Project Context**: Target GCP Project ID (e.g., `gcs-fuse-test`).
3. **Target Colocation Details**: Compute zone/region where the target GCE VMs or GKE clusters reside (e.g. `us-east5-b`, `us-central1`).

### Trigger Conditions
- Target buckets specified in `targets.json` do not exist in the GCP project.
- Preparing dedicated storage buckets for POSIX conformance testing or FIO benchmarking.
- Provisioning Zonal RAPID buckets or Regional HNS buckets colocated with compute resources.

---

## Input/Output Contract

### Inputs
- **`PROJECT_ID`**: Google Cloud Project ID.
- **`BUCKET_NAME`**: Unique name for the GCS bucket.
- **`LOCATION` / `REGION`**: GCP Region for regional buckets (e.g., `us-east5`, `us-central1`).
- **`ZONE`**: GCP Zone for zonal RAPID buckets (e.g., `us-east5-b`, `us-central1-a`).
- **`IS_RAPID`**: Boolean flag indicating whether the bucket is a Zonal RAPID bucket (`true`) or standard Regional bucket (`false`).

### Outputs
- **GCS Bucket**: Provisioned GCS bucket with HNS enabled (`hierarchical_namespace.enabled = true`) and uniform bucket-level access (`uniform_bucket_level_access = true`).
- **Verified Bucket Metadata**: Output of `gcloud storage buckets describe gs://<BUCKET_NAME>`.

---

## Step-by-Step Procedure

### Step 1: Verify Existing Bucket or Colocation

Check if the bucket already exists before attempting creation:
```bash
gcloud storage buckets describe gs://<BUCKET_NAME> --project=<PROJECT_ID> --format="json(name,location,default_storage_class,hierarchical_namespace)" 2>/dev/null && echo "BUCKET_EXISTS" || echo "BUCKET_NOT_FOUND"
```

If the bucket exists, verify that its location matches the compute target location:
- **Regional standard buckets**: Must reside in the same region as the target GCE VM or GKE cluster.
- **Zonal RAPID buckets**: Must reside in the exact same zone (`placement`) as the target GCE VM or GKE cluster.

### Step 2: Provision Regional HNS Bucket (Standard Storage)

For standard regional performance benchmarks and POSIX conformance testing, create a Regional bucket with Hierarchical Namespace (HNS) enabled:
```bash
gcloud storage buckets create gs://<BUCKET_NAME> \
    --project=<PROJECT_ID> \
    --location=<REGION> \
    --enable-hierarchical-namespace \
    --uniform-bucket-level-access
```

### Step 3: Provision Zonal RAPID HNS Bucket (Ultra-Low Latency Storage)

For high-throughput zonal RAPID workloads (e.g., TPU slices or low-latency SSD targets), create a Zonal RAPID bucket:
```bash
gcloud storage buckets create gs://<BUCKET_NAME> \
    --project=<PROJECT_ID> \
    --location=<REGION> \
    --placement=<ZONE> \
    --default-storage-class=RAPID \
    --enable-hierarchical-namespace \
    --uniform-bucket-level-access
```

> [!NOTE]
> Zonal RAPID buckets require both `--location=<REGION>` and `--placement=<ZONE>` along with `--default-storage-class=RAPID`.

### Step 4: Verify Bucket Properties

Verify that HNS and Uniform Bucket-Level Access are properly enabled on the bucket:
```bash
gcloud storage buckets describe gs://<BUCKET_NAME> --project=<PROJECT_ID> --format="json"
```

Expected JSON snippet:
```json
{
  "default_storage_class": "STANDARD",
  "hierarchical_namespace": {
    "enabled": true
  },
  "location": "US-EAST5",
  "location_type": "region",
  "name": "<BUCKET_NAME>",
  "uniform_bucket_level_access": true
}
```

---

## Failure Modes & Edge Cases

| Failure Scenario | Root Cause | Recovery / Remediation Action |
|---|---|---|
| **Bucket Name Already Taken** | Global GCS bucket name collision across all Google Cloud accounts | Append a unique project or timestamp suffix (e.g. `npi-smoke-<TARGET_NAME>-<PROJECT_ID>`). |
| **Zone/Region Mismatch** | Bucket created in a different region than compute VM/cluster | Cross-region traffic adds latency and fails colocation checks. Delete or recreate the bucket in the same region/zone. |
| **RAPID Capacity Unavailable** | Target zone lacks RAPID storage availability or quota | Skip Rapid tests on that target or fall back to Regional Standard HNS bucket. |
| **Permission Denied** | Service account or caller lacks `storage.buckets.create` permission | Grant `roles/storage.admin` or `roles/storage.objectAdmin` on the GCP project. |

---

## Verification Checks

1. **Bucket Existence and Metadata Check**:
   ```bash
   gcloud storage buckets describe gs://<BUCKET_NAME> --project=<PROJECT_ID> --format="value(name,location,default_storage_class)"
   ```
2. **Write / Read Capability Test**:
   ```bash
   echo "npi-test-payload" | gcloud storage cp - gs://<BUCKET_NAME>/npi-health-check.txt
   gcloud storage cat gs://<BUCKET_NAME>/npi-health-check.txt
   gcloud storage rm gs://<BUCKET_NAME>/npi-health-check.txt
   ```
