FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    mdadm \
    fio \
    xfsprogs \
    e2fsprogs \
    libaio1 \
    && rm -rf /var/lib/apt/lists/*

# Install BigQuery client
RUN pip install --no-cache-dir google-cloud-bigquery

WORKDIR /app

COPY fio/fio_benchmark_runner.py /app/fio_benchmark_runner.py

COPY lssd_throughput_tests/lssd_benchmark.py /app/lssd_benchmark.py

ENTRYPOINT ["python3", "lssd_benchmark.py"]