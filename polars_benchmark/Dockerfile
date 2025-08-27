ARG UBUNTU_VERSION=24.04
ARG GO_VERSION=1.24.5
ARG ARCH=amd64
ARG GCSFUSE_VERSION=master

FROM ubuntu:${UBUNTU_VERSION} AS builder

# Re-declare ARGs to make them available inside this build stage
ARG GO_VERSION
ARG ARCH
ARG GCSFUSE_VERSION

# Set DEBIAN_FRONTEND to noninteractive to avoid prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update -y && apt-get upgrade -y && \
    apt-get install -y git wget make gcc

# Download and install Go, then add it to the PATH
RUN wget "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -O go.tar.gz && \
    tar -C /usr/local -xzf go.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"

# Clone GCSFuse repository, build it, and clean up apt cache
RUN git clone -b ${GCSFUSE_VERSION} --depth 1 --single-branch https://github.com/GoogleCloudPlatform/gcsfuse.git && \
    cd gcsfuse && go build .

# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Install gcsfuse dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Add the gcloud-sdk repository

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .
COPY --from=builder /gcsfuse/gcsfuse /usr/local/bin/gcsfuse
ENV PATH="/usr/local/go/bin:${PATH}"

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the content of the local src directory to the working directory
COPY benchmark.py .

# Specify the command to run on container start
ENTRYPOINT ["python3", "benchmark.py"]
