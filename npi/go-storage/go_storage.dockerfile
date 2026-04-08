ARG GO_VERSION=1.24.5

FROM golang:${GO_VERSION} AS builder
WORKDIR /app
COPY go-storage/custom_go_client_benchmark /app/custom_go_client_benchmark
WORKDIR /app/custom_go_client_benchmark

RUN go build -o benchmark_tool .

FROM python:3.13-slim
RUN pip install --no-cache-dir google-cloud-storage google-cloud-bigquery
COPY --from=builder /app/custom_go_client_benchmark/benchmark_tool /usr/local/bin/benchmark_tool

COPY go-storage/go_storage_benchmark.py /app/go_storage_benchmark.py
WORKDIR /app
ENTRYPOINT ["python3", "go_storage_benchmark.py"]
