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

GCSFUSE_VERSION ?= v3.4.0
GO_VERSION=1.24.11
UBUNTU_VERSION=24.04
REGISTRY=us-docker.pkg.dev
PROJECT=gcs-fuse-test


.DEFAULT_GOAL := build

.PHONY: build

build:
	gcloud builds submit --project $(PROJECT) --config cloudbuild.yaml . --substitutions=_GCSFUSE_VERSION=$(GCSFUSE_VERSION),_GO_VERSION=$(GO_VERSION),_UBUNTU_VERSION=$(UBUNTU_VERSION),_REGISTRY=$(REGISTRY),_PROJECT=$(PROJECT)
