ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5

FROM golang:${GO_VERSION} AS builder
WORKDIR /app
RUN git clone https://github.com/kislaykishore/custom-go-client-benchmark.git
WORKDIR /app/custom-go-client-benchmark
RUN go build -o benchmark_tool .

FROM python:3.13-slim
RUN pip install --no-cache-dir google-cloud-storage google-cloud-bigquery
COPY --from=builder /app/custom-go-client-benchmark/benchmark_tool /usr/local/bin/benchmark_tool
COPY go-storage/go_storage_benchmark.py /app/go_storage_benchmark.py
WORKDIR /app
ENTRYPOINT ["python3", "go_storage_benchmark.py"]
