ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG ARCH=amd64
ARG GCSFUSE_VERSION=master
ARG REGISTRY=us-docker.pkg.dev

FROM ${REGISTRY}/gcs-fuse-test/gcsfuse-benchmarks/gcsfuse-${GCSFUSE_VERSION}-perf-base-${ARCH}:latest
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
COPY run_fio_matrix.py /run_fio_matrix.py
COPY filecache_matrix.csv /filecache_matrix.csv
COPY filecache.fio /filecache.fio
ENTRYPOINT ["/run_fio_matrix.py", "--matrix-config", "/filecache_matrix.csv", "--fio-template", "/filecache.fio"]