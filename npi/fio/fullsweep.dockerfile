ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG GCSFUSE_VERSION=master
ARG REGISTRY=us-docker.pkg.dev
ARG PROJECT=gcs-fuse-test

FROM ${REGISTRY}/${PROJECT}/gcsfuse-benchmarks/gcsfuse-${GCSFUSE_VERSION}-perf-base:latest
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
COPY run_fio_matrix.py /run_fio_matrix.py
COPY fullsweep_matrix.csv /fullsweep_matrix.csv
COPY fullsweep.fio /fullsweep.fio
ENTRYPOINT ["/run_fio_matrix.py", "--matrix-config", "/fullsweep_matrix.csv", "--fio-template", "/fullsweep.fio"]