# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

GCSFUSE_VERSION ?= master
ARCH ?= amd64
GO_VERSION=1.25.0
UBUNTU_VERSION=24.04
REGISTRY=us-docker.pkg.dev

.DEFAULT_GOAL := build

.PHONY: build base read-benchmark write-benchmark

base:
	docker build -f gcsfuse-perf-base.docker --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg ARCH=$(ARCH) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) -t $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/gcsfuse-$(GCSFUSE_VERSION)-perf-base-$(ARCH):latest . && docker push $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/gcsfuse-$(GCSFUSE_VERSION)-perf-base-$(ARCH):latest

read-benchmark: base
	docker build fio -f fio/read.dockerfile --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg ARCH=$(ARCH) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-read-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest && docker push $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-read-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest

write-benchmark: base
	docker build fio -f fio/write.dockerfile --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg ARCH=$(ARCH) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-write-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest && docker push $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-write-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest

fullsweep-benchmark: base
	docker build fio -f fio/fullsweep.dockerfile --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg ARCH=$(ARCH) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-fullsweep-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest && docker push $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/fio-fullsweep-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest

orbax-benchmark: base
	docker build fio -f fio/orbax_emulated.dockerfile --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg ARCH=$(ARCH) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/orbax-emulated-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest && docker push $(REGISTRY)/gcs-fuse-test/gcsfuse-benchmarks/orbax-emulated-benchmark-$(GCSFUSE_VERSION)-$(ARCH):latest

build: read-benchmark write-benchmark fullsweep-benchmark orbax-benchmark base


