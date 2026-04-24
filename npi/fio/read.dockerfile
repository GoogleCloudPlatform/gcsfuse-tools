ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG GCSFUSE_VERSION=master
ARG REGISTRY=us-docker.pkg.dev
ARG PROJECT=gcs-fuse-test
ARG IMAGE_VERSION=latest

FROM ${REGISTRY}/${PROJECT}/gcsfuse-benchmarks/gcsfuse-perf-base:${IMAGE_VERSION}
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
COPY run_fio_matrix.py /run_fio_matrix.py
COPY read_matrix.csv /read_matrix.csv
COPY read.fio /read.fio
ENTRYPOINT ["/run_fio_matrix.py", "--matrix-config", "/read_matrix.csv", "--fio-template", "/read.fio"]