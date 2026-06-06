ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.26.4
ARG REGISTRY=us-docker.pkg.dev
ARG PROJECT=gcs-fuse-test
ARG IMAGE_VERSION=latest

# Builder stage to compile the Go binary
FROM golang:${GO_VERSION} AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 go build -o go-grpc-verify main.go

# Runtime stage using the performance base image
FROM ${REGISTRY}/${PROJECT}/gcsfuse-benchmarks/gcsfuse-perf-base:${IMAGE_VERSION}
WORKDIR /benchmark
COPY --from=builder /app/go-grpc-verify /benchmark/go-grpc-verify

ENTRYPOINT ["/benchmark/go-grpc-verify"]
