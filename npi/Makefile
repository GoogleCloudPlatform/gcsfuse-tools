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

GCSFUSE_VERSION ?=
GO_VERSION ?=
UBUNTU_VERSION ?= 24.04
REGISTRY ?= us-docker.pkg.dev
PROJECT ?= gcs-fuse-test
IMAGE_VERSION ?=


ARM_WORKER_POOL ?=


.DEFAULT_GOAL := build

.PHONY: build

build:
	python3 -u build_images.py \
		$(if $(GCSFUSE_VERSION),--gcsfuse-version $(GCSFUSE_VERSION)) \
		$(if $(GO_VERSION),--go-version $(GO_VERSION)) \
		--ubuntu-version $(UBUNTU_VERSION) \
		--registry $(REGISTRY) \
		--project $(PROJECT) \
		$(if $(IMAGE_VERSION),--image-version $(IMAGE_VERSION)) \
		$(if $(ARM_WORKER_POOL),--arm-worker-pool $(ARM_WORKER_POOL))
