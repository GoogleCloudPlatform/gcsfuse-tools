ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG ARCH=amd64
ARG GCSFUSE_VERSION=master

FROM gcr.io/gcs-fuse-test/gcsfuse-${GCSFUSE_VERSION}-perf-base-${ARCH}:latest
COPY run_fio_benchmark.py /run_fio_benchmark.py
COPY fio_benchmark_runner.py /fio_benchmark_runner.py
COPY run_fio_matrix.py /run_fio_matrix.py
COPY write_matrix.csv /write_matrix.csv
COPY write.fio /write.fio
ENTRYPOINT ["/run_fio_matrix.py", "--matrix-config", "/write_matrix.csv", "--fio-template", "/write.fio"]