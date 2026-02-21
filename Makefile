APP=media-artwork-uploader
REGISTRY=jabrown
TAG?=$(shell git describe --tags --always --dirty)
PLATFORMS=linux/amd64,linux/arm64
CURRENT_VERSION=$(shell python3 -c "import re, sys; path = 'src/core/__version__.py'; try: data = open(path).read(); except FileNotFoundError: sys.stderr.write(f'Error: {path} not found\n'); sys.exit(1); m = re.search(r'__version__ = [\"\\x27]([^\"\\x27]+)', data); if not m: sys.stderr.write(f'Error: __version__ not found in {path}\n'); sys.exit(1); print(m.group(1))")

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
	@echo "Current version: $(CURRENT_VERSION)"
	$(eval NEW_VERSION := $(shell python3 -c "\
		v = '$(CURRENT_VERSION)'.split('.'); \
		bump = '$(BUMP)'; \
		parts = list(map(int, v[:3])); \
		idx = {'major': 0, 'minor': 1, 'patch': 2}.get(bump); \
		assert idx is not None, f'Invalid bump type: {bump}'; \
		parts[idx] += 1; \
		parts[idx+1:] = [0] * (2 - idx); \
		print('.'.join(map(str, parts)))"))
	@echo "New version:     $(NEW_VERSION)"
	@echo ""
	@printf "Create and push tag v$(NEW_VERSION)? [y/N] " ; read confirm && [ "$$confirm" = "y" ]
	git tag -a "v$(NEW_VERSION)" -m "Release v$(NEW_VERSION)"
	git push origin "v$(NEW_VERSION)"
	@echo ""
	@echo "Pushed v$(NEW_VERSION) — GitHub Actions will handle the release."

release-patch: ## Release a patch version bump
	$(MAKE) release BUMP=patch

release-minor: ## Release a minor version bump
	$(MAKE) release BUMP=minor

release-major: ## Release a major version bump
	$(MAKE) release BUMP=major
