ARG REGISTRY=us-docker.pkg.dev
ARG PROJECT=gcs-fuse-test
ARG IMAGE_VERSION=latest

FROM ${REGISTRY}/${PROJECT}/gcsfuse-benchmarks/gcsfuse-perf-base:${IMAGE_VERSION}

COPY record_host_info.py /record_host_info.py

ENTRYPOINT ["python3", "/record_host_info.py"]
