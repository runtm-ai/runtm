# Docker Template - Claude Instructions

This is a Runtm Docker template for deploying any containerized application.

## Contract Rules

| File | Editable? | How to Change |
|------|-----------|---------------|
| `runtm.yaml` | NO | Use `runtm apply` or CLI commands |
| `Dockerfile` | YES | Edit freely, but must expose declared port |
| Health endpoint | YES | Must return 200 at manifest's `health_path` |

### Invariants (enforced at deploy)
- Port must match `runtm.yaml` (default: 8080)
- `health_path` must return 200 (default: /health)
- Application must start within 10 seconds

## What is the Docker Template?

Bring your own Dockerfile for any language: Go, Rust, Elixir, or any containerized application.

## Critical Rules

1. **Port 8080** - The app MUST listen on port 8080 (or the port in `runtm.yaml`)
2. **Health Endpoint** - `GET /health` MUST return 200, respond in <100ms
3. **Fast Startup** - Container must be ready in <10 seconds

## Project Structure

```
my-docker-app/
├── runtm.yaml               # Deployment manifest (DO NOT DELETE)
├── runtm.discovery.yaml     # App metadata (FILL IN before deploy)
├── Dockerfile               # Your container build
├── .runtmignore             # Artifact exclusions
└── [your application code]
```

## Building Your App

### 1. Replace Dockerfile

```dockerfile
# Example: Go
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o main .

FROM alpine:latest
COPY --from=builder /app/main /main
EXPOSE 8080
CMD ["/main"]
```

### 2. Implement Health Endpoint

**Go:**
```go
http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
})
```

**Rust (Axum):**
```rust
async fn health() -> &'static str { "OK" }
```

### 3. Test Locally

```bash
docker build -t my-app .
docker run -p 8080:8080 my-app
curl http://localhost:8080/health
```

## Environment Variables & Secrets

### Declaring Env Vars

```yaml
# runtm.yaml
env_schema:
  - name: DATABASE_URL
    type: string
    required: true
    secret: true       # Redacted from logs
    description: "Database connection"
```

### Setting Secrets

```bash
runtm secrets set DATABASE_URL=postgres://...
runtm secrets list
```

**Security:** `.env.local` is gitignored and cursorignored (AI agents can't see it).

### Proposing New Env Vars

Create `runtm.requests.yaml`:

```yaml
requested:
  env_vars:
    - name: API_KEY
      secret: true
      reason: "Needed for integration"
```

Then run `runtm approve` to merge into manifest.

## Before Deploy (REQUIRED)

**You MUST edit `runtm.discovery.yaml` before deploying:**

1. Replace ALL `# TODO:` placeholders with real content
2. Fill in: description, summary, capabilities, use_cases, tags

**DO NOT deploy with `# TODO:` placeholders!**

## Deployment

```bash
# 1. Replace Dockerfile with your app
# 2. Implement /health endpoint
# 3. Test locally with docker build/run
# 4. Edit runtm.discovery.yaml
# 5. Then:
runtm status    # Check auth
runtm login     # If not authenticated
runtm validate  # Validate project
runtm deploy    # Deploy
```

## Constraints

- ❌ Don't change port from what's in `runtm.yaml`
- ❌ Don't remove/break `/health`
- ❌ Don't create slow-starting containers (>10s)
- ❌ Don't hardcode secrets
- ✅ Use multi-stage Docker builds
- ✅ Keep images small (Alpine base)
- ✅ Implement proper health checks

## Machine Tiers

| Tier | Memory | CPUs |
|------|--------|------|
| `starter` | 256 MB | 1 shared |
| `standard` | 512 MB | 1 shared |
| `performance` | 1 GB | 2 shared |

Set in `runtm.yaml`:

```yaml
tier: standard
```
