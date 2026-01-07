# Use a lightweight Python base, but we need OS utilities for disk management
FROM python:3.11-slim-bookworm

# Install system dependencies
# mdadm: for RAID management
# fio: for benchmarking
# xfsprogs/e2fsprogs: for filesystem formatting
# libaio1: required for fio libaio engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    mdadm \
    fio \
    xfsprogs \
    e2fsprogs \
    libaio1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the benchmark script
COPY lssd_benchmark.py /app/lssd_benchmark.py

# Entry point triggers the python script
ENTRYPOINT ["python3", "lssd_benchmark.py"]