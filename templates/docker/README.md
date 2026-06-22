# My Docker App - Docker Template

A Docker-based deployment template for [Runtm](https://runtm.com). Use this template when you want to deploy any containerized application - Go, Rust, Elixir, or any language with Docker support.

## What is the Docker Template?

The docker template is a "bring your own Dockerfile" approach. Unlike other templates that scaffold a complete application, this template provides only the deployment contract files:

- `runtm.yaml` - Deployment manifest
- `Dockerfile` - Your container build (replace the placeholder)
- `runtm.discovery.yaml` - App metadata for discoverability

**Use cases:**
- Go microservices
- Rust APIs
- Elixir/Phoenix applications
- Any language with Docker support
- Existing containerized applications

## Requirements

Your Dockerfile must:

1. **Expose the correct port** - Match the `port` in `runtm.yaml` (default: 8080)
2. **Implement health endpoint** - `GET /health` must return HTTP 200
3. **Start quickly** - Application must be ready within 10 seconds

## Project Structure

```
my-docker-app/
├── runtm.yaml               # Deployment manifest
├── runtm.discovery.yaml     # App metadata (fill in before deploy!)
├── Dockerfile               # Your container build
├── .runtmignore             # Artifact exclusions
└── [your application code]
```

## Development

### 1. Replace the Dockerfile

The template includes a placeholder Dockerfile. Replace it with your own:

```dockerfile
# Example: Go application
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o main .

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/main .
EXPOSE 8080
CMD ["./main"]
```

### 2. Implement Health Endpoint

Your application must respond to `GET /health` with HTTP 200:

**Go:**
```go
http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
    w.Write([]byte("OK"))
})
```

**Rust (Axum):**
```rust
async fn health() -> &'static str {
    "OK"
}
```

**Elixir (Phoenix):**
```elixir
get "/health", PageController, :health
```

### 3. Build and Test Locally

```bash
# Build
docker build -t my-docker-app .

# Run
docker run -p 8080:8080 my-docker-app

# Test health
curl http://localhost:8080/health
```

## Deployment

### 1. Fill in Discovery Metadata

Edit `runtm.discovery.yaml` and replace ALL `# TODO:` placeholders:

```yaml
description: |
  A high-performance Go API for processing webhook events.
  Handles authentication, rate limiting, and async job processing.

summary: "Go webhook processor with job queue"

capabilities:
  - "Process webhook events"
  - "Async job processing"

use_cases:
  - "Event-driven architectures"
  - "Webhook integrations"

tags:
  - go
  - api
  - webhooks
```

### 2. Deploy

```bash
# Login (first time only)
runtm login

# Validate
runtm validate

# Deploy
runtm deploy
```

## Configuration

### Environment Variables

Declare environment variables in `runtm.yaml`:

```yaml
env_schema:
  - name: DATABASE_URL
    type: string
    required: true
    secret: true
    description: "Database connection string"
  - name: LOG_LEVEL
    type: string
    required: false
    default: "info"
```

Set secrets:

```bash
runtm secrets set DATABASE_URL=postgres://...
runtm secrets list
```

### Machine Tiers

| Tier | Memory | CPUs | Best for |
|------|--------|------|----------|
| `starter` | 2 GB | 2 shared | Simple APIs |
| `performance` | 4 GB | 4 shared | Most workloads |
| `pro` | 8 GB | 4 shared | Memory-intensive apps |

Set in `runtm.yaml`:

```yaml
tier: performance
```

## Constraints

- Port must match `runtm.yaml` (default: 8080)
- `/health` must exist and return 200
- Startup must complete within 10 seconds
- Artifact size must be under 20MB (use `.runtmignore`)

## Examples

### Go

```dockerfile
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o main .

FROM alpine:latest
COPY --from=builder /app/main /main
EXPOSE 8080
CMD ["/main"]
```

### Rust

```dockerfile
FROM rust:1.75-slim AS builder
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /app/target/release/myapp /myapp
EXPOSE 8080
CMD ["/myapp"]
```

### Elixir

```dockerfile
FROM elixir:1.15-alpine AS builder
ENV MIX_ENV=prod
WORKDIR /app
COPY . .
RUN mix deps.get && mix release

FROM alpine:latest
COPY --from=builder /app/_build/prod/rel/myapp ./
EXPOSE 8080
CMD ["bin/myapp", "start"]
```

## License

MIT
