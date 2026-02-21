APP=media-artwork-uploader
REGISTRY=jabrown
TAG?=$(shell git describe --tags --always --dirty)
PLATFORMS=linux/amd64,linux/arm64


.PHONY: docker-build docker-release release release-patch release-minor release-major help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

docker-build: ## Build Docker image locally
	docker buildx build \
	  --platform=$(PLATFORMS) \
	  -t $(REGISTRY)/$(APP):$(TAG) \
	  -t $(REGISTRY)/$(APP):dev \
	  .

docker-release: ## Build and push Docker image
	docker buildx build \
	  --platform=$(PLATFORMS) \
	  -t $(REGISTRY)/$(APP):$(TAG) \
	  -t $(REGISTRY)/$(APP):dev \
	  --push \
	  .

release: ## Create and push a release tag (usage: make release BUMP=patch|minor|major)
ifndef BUMP
	$(error BUMP is required. Usage: make release BUMP=patch|minor|major)
endif
	@NEW_VERSION=$$(python3 src/bump_version.py $(BUMP)) || exit 1; \
		echo ""; \
		printf "Create and push tag v$$NEW_VERSION? [y/N] "; \
		read confirm; \
		[ "$$confirm" = "y" ] || exit 1; \
		git add src/core/__version__.py && \
		git commit -m "Bump version to $$NEW_VERSION" && \
		git tag -a "v$$NEW_VERSION" -m "Release v$$NEW_VERSION" && \
		git push origin HEAD "v$$NEW_VERSION" && \
		echo "" && \
		echo "Pushed v$$NEW_VERSION — GitHub Actions will handle the release."

release-patch: ## Release a patch version bump
	$(MAKE) release BUMP=patch

release-minor: ## Release a minor version bump
	$(MAKE) release BUMP=minor

release-major: ## Release a major version bump
	$(MAKE) release BUMP=major
