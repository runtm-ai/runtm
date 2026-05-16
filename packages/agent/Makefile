# Runtm agent CLI build targets.
#
# Production installs go through the hosted one-liner:
#   curl -fsSL https://runtm.com/install | bash
# which downloads a pre-built binary published by .github/workflows/release-agent.yml
# (driven by .goreleaser.yml). Go developers can also `go install` from source.
#
# These targets are for local development -- `make release` is the same
# cross-compile that goreleaser does in CI, useful when iterating offline.
#
# Common workflows:
#   make build    # local binary at ./bin/runtm-api
#   make dev      # build + run --help, pointing at local backend
#   make install  # `go install` to $GOBIN
#   make release  # cross-compile for darwin/linux x amd64/arm64 to ./dist
#   make test     # go test ./...
#   make lint     # go vet ./...
#   make fmt      # go fmt ./...

BINARY      := runtm-api
PKG         := ./cmd/runtm-api
VERSION     ?= dev
LDFLAGS     := -s -w -X github.com/runtm-ai/runtm/packages/agent/internal/cmd.Version=$(VERSION)
LOCAL_API   ?= http://localhost:8081/api

.PHONY: sync-skills
sync-skills:
	@# Mirror skills/ -> internal/skills/files/ so the //go:embed copy in the
	@# binary matches what we ship in the release tarball under skills/.
	@mkdir -p internal/skills/files
	@cp skills/*.md internal/skills/files/

.PHONY: build
build: sync-skills
	mkdir -p bin
	go build -trimpath -ldflags '$(LDFLAGS)' -o bin/$(BINARY) $(PKG)

.PHONY: dev
dev: build
	@echo "Built ./bin/$(BINARY) (RUNTM_API_URL=$(LOCAL_API))"
	@RUNTM_API_URL=$(LOCAL_API) ./bin/$(BINARY) --help

.PHONY: run
run: build
	@RUNTM_API_URL=$(LOCAL_API) ./bin/$(BINARY) $(ARGS)

.PHONY: install
install:
	go install -trimpath -ldflags '$(LDFLAGS)' $(PKG)

.PHONY: test
test:
	go test -race ./...

.PHONY: lint
lint:
	go vet ./...

.PHONY: fmt
fmt:
	go fmt ./...

.PHONY: tidy
tidy:
	go mod tidy

.PHONY: clean
clean:
	rm -rf bin dist

.PHONY: release
release: clean
	mkdir -p dist
	@for os in darwin linux; do \
	  for arch in amd64 arm64; do \
	    echo "==> building $$os/$$arch"; \
	    GOOS=$$os GOARCH=$$arch CGO_ENABLED=0 \
	      go build -trimpath -ldflags '$(LDFLAGS)' \
	        -o dist/$(BINARY)-$$os-$$arch $(PKG); \
	  done; \
	done
	@ls -la dist/

.PHONY: help
help:
	@echo "Targets:"
	@echo "  build         Build local binary to ./bin/runtm-api (auto-syncs skills)"
	@echo "  dev           Build and print --help against local backend"
	@echo "  run           Build and run with ARGS=\"...\" against local backend"
	@echo "  install       go install into GOPATH"
	@echo "  test          go test ./..."
	@echo "  lint          go vet ./..."
	@echo "  fmt           go fmt ./..."
	@echo "  release       Cross-compile to ./dist"
	@echo "  sync-skills   Copy skills/*.md to internal/skills/files/ (for //go:embed)"
	@echo "  clean         Remove bin/ and dist/"
