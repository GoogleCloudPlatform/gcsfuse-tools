# Defaults
PROJECT_ID ?= $(shell gcloud config get-value project)
REGISTRY ?= us-docker.pkg.dev
REPO_NAME ?= orbax-repo
IMAGE_NAME ?= orbax-loading
TAG ?= latest

.PHONY: build-cloud build-local

# Submit build to Google Cloud Build
build-cloud:
	gcloud builds submit --config cloudbuild.yaml \
		--substitutions=_PROJECT=$(PROJECT_ID),_REGISTRY=$(REGISTRY),_REPO_NAME=$(REPO_NAME),_IMAGE_NAME=$(IMAGE_NAME),_TAG=$(TAG) \
		.

# Build locally using Docker
build-local:
	docker build -t $(REGISTRY)/$(PROJECT_ID)/$(REPO_NAME)/$(IMAGE_NAME):$(TAG) .