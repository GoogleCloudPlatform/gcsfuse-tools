ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG ARCH=amd64
ARG GCSFUSE_VERSION=master
ARG REGISTRY=us-docker.pkg.dev

FROM ${REGISTRY}/gcs-fuse-test/gcsfuse-benchmarks/gcsfuse-${GCSFUSE_VERSION}-perf-base-${ARCH}:latest
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
COPY run_fio_matrix.py /run_fio_matrix.py
COPY file_cache_matrix.csv /file_cache_matrix.csv
COPY file_cache.fio /file_cache.fio
ENTRYPOINT ["/run_fio_matrix.py", "--matrix-config", "/file_cache_matrix.csv", "--fio-template", "/file_cache.fio",
"--gcsfuse-flags=implicit-dirs metadata-cache-ttl-secs=-1 type-cache-max-size-mb=-1 stat-cache-max-size-mb=-1 file-cache-max-size-mb=-1 file-cache-cache-file-for-range-read"
]
