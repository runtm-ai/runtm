# Agent Instructions

This project uses the Runtm Docker template for deploying custom containerized applications. Instructions auto-apply in these AI IDEs:

| IDE | Auto-apply File |
|-----|-----------------|
| Cursor | `.cursor/rules/runtm.mdc` |
| Claude Code | `CLAUDE.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |

If you're using a different AI tool, follow the rules below.

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

The docker template is a "bring your own Dockerfile" approach for deploying any containerized application.

**Use cases:**
- Go microservices and APIs
- Rust web services
- Elixir/Phoenix applications
- Any language with Docker support
- Existing containerized applications

## Critical Rules

1. **DO NOT change the port** - Must match `runtm.yaml` (default: 8080)
2. **DO NOT remove health endpoint** - Must return 200 at `/health`
3. **DO NOT create slow-starting containers** - Must be ready in <10 seconds

## Project Structure

```
my-docker-app/
├── runtm.yaml               # Deployment manifest (DO NOT DELETE)
├── runtm.discovery.yaml     # App metadata (FILL IN before deploy)
├── Dockerfile               # Your container build
├── .runtmignore             # Artifact exclusions
└── [your application code]
```

## Building Your Application

### 1. Replace the Dockerfile

The template includes a placeholder. Replace with your own:

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

Your application MUST respond to `GET /health` with HTTP 200:

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

let app = Router::new()
    .route("/health", get(health));
```

**Elixir (Phoenix):**
```elixir
# router.ex
get "/health", HealthController, :check

# health_controller.ex
def check(conn, _params) do
  send_resp(conn, 200, "OK")
end
```

### 3. Test Locally

```bash
docker build -t my-app .
docker run -p 8080:8080 my-app
curl http://localhost:8080/health
```

## Configuration & Secrets

### Declaring Environment Variables

If your service needs environment variables, declare them in `runtm.yaml`:

```yaml
env_schema:
  - name: DATABASE_URL
    type: string
    required: true
    secret: true  # Redacted from logs, injected securely
    description: "Database connection string"
  - name: LOG_LEVEL
    type: string
    required: false
    default: "info"
```

### Setting Secrets

Store secret values in `.env.local` (gitignored, cursorignored):

```bash
# Set a secret
runtm secrets set DATABASE_URL=postgres://...

# List env vars and their status
runtm secrets list
```

**Security:**
- `.env.local` is gitignored and cursorignored (AI agents can't see it)
- Secrets marked with `secret: true` are redacted from logs
- Secrets are injected to the deployment provider, never stored in Runtm DB

### Proposing New Env Vars

If you need new env vars, create `runtm.requests.yaml`:

```yaml
requested:
  env_vars:
    - name: API_KEY
      type: string
      secret: true
      reason: "Needed for external service integration"
notes:
  - "This API key enables the new payment feature"
```

Then run `runtm approve` to merge into the manifest.

## Before Deploying (MANDATORY STEPS)

⚠️ **STOP: Complete ALL steps below before running `runtm deploy`.**

### Step 1: Replace Dockerfile (REQUIRED)

Replace the placeholder Dockerfile with your own that:
- Builds your application
- Exposes port 8080
- Implements `/health` endpoint

### Step 2: Update Discovery Metadata (REQUIRED)

**You MUST edit `runtm.discovery.yaml` and replace ALL `# TODO:` placeholders with real content.**

Example of a properly filled discovery file:

```yaml
description: |
  A high-performance Go API for processing webhook events from payment
  providers. Handles authentication, rate limiting, and async job processing.

summary: "Go webhook processor with async job queue"

capabilities:
  - "Process webhook events from Stripe, PayPal"
  - "Async job processing with retry logic"
  - "Rate limiting and authentication"

use_cases:
  - "E-commerce platforms needing payment webhooks"
  - "Event-driven architectures"

tags:
  - go
  - api
  - webhooks
  - payments
```

**DO NOT deploy with `# TODO:` placeholders!** Apps with proper metadata are discoverable in the dashboard.

### Step 3: Authenticate (REQUIRED)

```bash
runtm status    # Check if logged in
runtm login     # If not authenticated, complete browser auth
```

### Step 4: Validate and Deploy

```bash
runtm validate  # Check project is valid
runtm deploy    # Deploy to production
```

### Deployment Checklist (follow in order)
1. ✅ Replace Dockerfile with your application build
2. ✅ Implement `/health` endpoint returning 200
3. ✅ Test locally with `docker build` and `docker run`
4. ✅ Edit `runtm.discovery.yaml` - replace ALL `# TODO:` with real content
5. ✅ Run `runtm status` to check auth
6. ✅ If not authenticated, run `runtm login` and complete browser auth
7. ✅ Run `runtm validate` to check project
8. ✅ Run `runtm deploy` to deploy

### Common Errors
- **"nodename nor servname provided"** → Not logged in. Run `runtm login` first.
- **"Authentication required"** → Run `runtm login` first.
- **"Health check failed"** → Your `/health` endpoint isn't returning 200.
- **Build timeout** → Your Dockerfile takes too long. Optimize build steps.

## What NOT To Do

- ❌ Change the port from what's in `runtm.yaml`
- ❌ Remove or break the `/health` endpoint
- ❌ Create containers that take >10 seconds to start
- ❌ Hardcode secrets (use environment variables)
- ❌ Deploy with `# TODO:` placeholders in discovery file

## Machine Tiers

| Tier | Memory | CPUs | Best for |
|------|--------|------|----------|
| `starter` | 2 GB | 2 shared | Simple APIs, low traffic |
| `performance` | 4 GB | 4 shared | Most workloads |
| `pro` | 8 GB | 4 shared | Memory-intensive apps |

Set in `runtm.yaml`:

```yaml
tier: performance
```
