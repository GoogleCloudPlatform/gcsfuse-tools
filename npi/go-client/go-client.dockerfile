ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.26.4
ARG REGISTRY=us-docker.pkg.dev
ARG PROJECT=gcs-fuse-test
ARG IMAGE_VERSION=latest

# Builder stage to compile the Go benchmark binary
FROM golang:${GO_VERSION} AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -o go-benchmark-client main.go

# Runtime stage
FROM ${REGISTRY}/${PROJECT}/gcsfuse-benchmarks/gcsfuse-perf-base:${IMAGE_VERSION}
WORKDIR /benchmark
COPY --from=builder /app/go-benchmark-client /benchmark/go-benchmark-client
COPY run_go_matrix.py /benchmark/run_go_matrix.py
COPY go_read_matrix.csv /benchmark/go_read_matrix.csv

ENTRYPOINT ["/benchmark/run_go_matrix.py", "--matrix-config", "/benchmark/go_read_matrix.csv"]
