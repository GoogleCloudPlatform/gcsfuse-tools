ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG GCSFUSE_VERSION=master
ARG REGISTRY=us-docker.pkg.dev

FROM ${REGISTRY}/gcs-fuse-test/gcsfuse-${GCSFUSE_VERSION}-perf-base:latest
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
ENTRYPOINT ["/run_fio_benchmark.py"]