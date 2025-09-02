ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG GCSFUSE_VERSION=master

FROM --platform=$BUILDPLATFORM golang:${GO_VERSION} AS builder
ARG GCSFUSE_VERSION
ARG TARGETOS
ARG TARGETARCH
WORKDIR /app
RUN git clone -b ${GCSFUSE_VERSION} --depth 1 --single-branch https://github.com/GoogleCloudPlatform/gcsfuse.git
RUN cd gcsfuse && GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build .

FROM python:3.13
# Install FUSE and related packages
RUN apt-get update && apt-get install -y fuse3 fio \
--no-install-recommends && \
rm -rf /var/lib/apt/lists/* && \
pip install --no-cache-dir google-cloud-bigquery
COPY --from=builder /app/gcsfuse/gcsfuse /gcsfuse/gcsfuse
ENTRYPOINT ["/bin/bash"]