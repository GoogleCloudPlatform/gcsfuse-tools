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

GCSFUSE_VERSION ?= v3.2.0
GO_VERSION=1.25.0
UBUNTU_VERSION=24.04
REGISTRY=us-docker.pkg.dev
PROJECT=gcs-fuse-test


.DEFAULT_GOAL := build

.PHONY: build base read-benchmark write-benchmark orbax-benchmark

base:
	docker buildx build --load -f gcsfuse-perf-base.dockerfile --platform linux/amd64,linux/arm64 --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) -t $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/gcsfuse-$(GCSFUSE_VERSION)-perf-base:latest . && docker push $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/gcsfuse-$(GCSFUSE_VERSION)-perf-base:latest

read-benchmark: base
	docker buildx build --load fio -f fio/read.dockerfile --platform linux/amd64,linux/arm64 --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-read-benchmark-$(GCSFUSE_VERSION):latest && docker push $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-read-benchmark-$(GCSFUSE_VERSION):latest

write-benchmark: base
	docker buildx build --load fio -f fio/write.dockerfile --platform linux/amd64,linux/arm64 --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-write-benchmark-$(GCSFUSE_VERSION):latest && docker push $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-write-benchmark-$(GCSFUSE_VERSION):latest

fullsweep-benchmark: base
	docker buildx build --load fio -f fio/fullsweep.dockerfile --platform linux/amd64,linux/arm64 --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-fullsweep-benchmark-$(GCSFUSE_VERSION):latest && docker push $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/fio-fullsweep-benchmark-$(GCSFUSE_VERSION):latest

orbax-benchmark: base
	docker buildx build --load fio -f fio/orbax_emulated.dockerfile --platform linux/amd64,linux/arm64 --build-arg GCSFUSE_VERSION=$(GCSFUSE_VERSION) --build-arg GO_VERSION=$(GO_VERSION) --build-arg UBUNTU_VERSION=$(UBUNTU_VERSION) --build-arg REGISTRY=$(REGISTRY) -t $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/orbax-emulated-benchmark-$(GCSFUSE_VERSION):latest && docker push $(REGISTRY)/$(PROJECT)/gcsfuse-benchmarks/orbax-emulated-benchmark-$(GCSFUSE_VERSION):latest

build: read-benchmark write-benchmark fullsweep-benchmark orbax-benchmark base

