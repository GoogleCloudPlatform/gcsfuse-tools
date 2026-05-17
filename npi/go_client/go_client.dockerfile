ARG GO_VERSION=1.25.0

FROM golang:${GO_VERSION} AS builder
WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 go build -o benchmark .

FROM python:3.13-slim
RUN pip install --no-cache-dir google-cloud-bigquery
COPY --from=builder /app/benchmark /benchmark
COPY run_go_client_benchmark.py /run_go_client_benchmark.py
ENTRYPOINT ["python3", "/run_go_client_benchmark.py"]
