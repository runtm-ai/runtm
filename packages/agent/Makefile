# Runtm agent CLI build targets.
#
# Customers install via `go install github.com/runtm-ai/runtm/packages/agent/cmd/runtm@latest`
# (also wrapped by install.sh). These targets exist for local development +
# the optional `make release` cross-compile if we ever ship pre-built binaries.
#
# Common workflows:
#   make build    # local binary at ./bin/runtm
#   make dev      # build + run --help, pointing at local backend
#   make install  # `go install` to $GOBIN -- same path customers use
#   make release  # cross-compile for darwin/linux x amd64/arm64 to ./dist (optional)
#   make test     # go test ./...
#   make lint     # go vet ./...
#   make fmt      # go fmt ./...

BINARY      := runtm
PKG         := ./cmd/runtm
VERSION     ?= dev
LDFLAGS     := -s -w -X github.com/runtm-ai/runtm/packages/agent/internal/cmd.Version=$(VERSION)
LOCAL_API   ?= http://localhost:8081/api

.PHONY: build
build:
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
	@echo "  build    Build local binary to ./bin/runtm"
	@echo "  dev      Build and print --help against local backend"
	@echo "  run      Build and run with ARGS=\"...\" against local backend"
	@echo "  install  go install into GOPATH"
	@echo "  test     go test ./..."
	@echo "  lint     go vet ./..."
	@echo "  fmt      go fmt ./..."
	@echo "  release  Cross-compile to ./dist"
	@echo "  clean    Remove bin/ and dist/"
