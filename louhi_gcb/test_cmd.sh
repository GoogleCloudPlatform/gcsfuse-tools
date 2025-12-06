#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <commit-hash>"
  exit 1
fi

if [ "$1" == "git_clone" ]; then
    COMMIT_HASH=8b8142514ae1b617b4ac3c249a2a29ff30abbf3e
    cloud-build-local --config=git_clone.yaml --dryrun=false --substitutions=_COMMIT_HASH=${COMMIT_HASH} .
    exit 0
fi

if [ "$1" == "test_package_on_gce_vm" ]; then
    RELEASE_VERSION=0.0.7
    UPLOAD_BUCKET=test-release-flow
    PROJECT_ID=gcs-fuse-test
    IMAGE_FAMILY=ubuntu-2204-lts
    IMAGE_PROJECT=ubuntu-os-cloud
    ZONE=us-south1-a
    MACHINE_TYPE=n2-standard-16
    RUN_LIGHT_TEST=true
    ZONAL=true
    READ_CACHE=true
    DOCKER_SUFFIX=""
    TEST_FLOW="true"
    COMMIT_HASH=8b8142514ae1b617b4ac3c249a2a29ff30abbf3e
    cloud-build-local --config=test_package_on_gce_vm.yaml --dryrun=false --substitutions=_PROJECT_ID=${PROJECT_ID},_IMAGE_FAMILY=${IMAGE_FAMILY},_IMAGE_PROJECT=${IMAGE_PROJECT},_ZONE=${ZONE},_RELEASE_VERSION=${RELEASE_VERSION},_UPLOAD_BUCKET=${UPLOAD_BUCKET},_MACHINE_TYPE=${MACHINE_TYPE},_ZONAL=${ZONAL},_DOCKER_SUFFIX=${DOCKER_SUFFIX},_READ_CACHE=${READ_CACHE},_RUN_LIGHT_TEST=${RUN_LIGHT_TEST},_COMMIT_HASH=${COMMIT_HASH} .
    exit 0
fi
