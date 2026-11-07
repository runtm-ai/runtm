# Agent Instructions

This project is a Runtm backend service. Instructions auto-apply in these AI IDEs:

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
| Lockfiles | AUTO | Managed by `runtm run`/`runtm deploy` |

### Invariants (enforced at deploy)
- Port must match `runtm.yaml` (8080)
- `health_path` must return 200
- Lockfile must be in sync

## What is a Backend Service?

A backend service is Python compute for API-first services that can expose endpoints when needed.

**Use cases:**
- Scraper/crawler
- Agent backend + tool calls
- Webhook + data processing/integrations
- API services

## Critical Rules

1. **DO NOT change the port** - Must be 8080
2. **DO NOT remove or break /health endpoint** - Must return 200 quickly
3. **DO NOT add heavy dependencies** - Keep startup time under 10 seconds

## Project Structure

```
app/
├── main.py              # FastAPI entry point
├── api/v1/              # API endpoints (thin layer)
│   ├── health.py        # Health check (DO NOT MODIFY)
│   └── run.py           # Main tool endpoint
├── services/            # Business logic (core functionality)
│   └── processor.py     # Processing service
├── db/                  # Database module (optional, enable via features.database)
│   ├── __init__.py      # Exports: Base, get_db
│   ├── base.py          # SQLAlchemy Base class
│   ├── session.py       # Engine, session factory, SQLite pragmas
│   ├── migrations.py    # Auto-migration on startup
│   ├── alembic/         # Alembic migration scripts
│   └── models/          # Your SQLAlchemy models
└── core/
    ├── config.py        # Environment config
    └── logging.py       # Logging setup
tests/                   # Pytest tests
runtm.yaml               # Deployment manifest (DO NOT DELETE)
Dockerfile               # Container build
```

## Architecture

```
API Layer (thin)  →  Services (business logic)  →  External calls (if any)
```

- **API Layer** (`api/v1/`): Request/response handling, validation, routing
- **Services** (`services/`): Core business logic, processing, transformations
- **Core** (`core/`): Configuration, utilities, shared helpers

## Adding New Features

### 1. Add Business Logic to Services

Create or update services in `app/services/`:

```python
# app/services/my_feature.py
class MyFeatureService:
    def __init__(self):
        # Initialize resources
        pass

    def process(self, input_data: str) -> dict:
        # Your business logic here
        return {"result": "processed"}


my_feature_service = MyFeatureService()
```

### 2. Create API Endpoint

Add endpoint in `app/api/v1/`:

```python
# app/api/v1/my_endpoint.py
from fastapi import APIRouter
from app.services.my_feature import my_feature_service

router = APIRouter()


@router.post("/my-endpoint")
async def my_endpoint(data: dict):
    # Delegate to service - keep endpoint thin
    result = my_feature_service.process(data["input"])
    return result
```

### 3. Register Router

In `app/main.py`:

```python
from app.api.v1.my_endpoint import router as my_endpoint_router

app.include_router(my_endpoint_router, prefix="/api/v1")
```

### Adding Dependencies

⚠️ **CRITICAL**: Add to `[project] dependencies` (NOT `dev` dependencies):

```toml
# pyproject.toml

[project]
dependencies = [
    "fastapi>=0.100.0,<1.0",
    "uvicorn[standard]>=0.20.0,<1.0",
    "your-package>=1.0,<2.0",  # ✅ CORRECT - installed in production
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0,<9.0",        # Only testing/linting tools here
    # "your-package>=1.0",     # ❌ WRONG - not installed in production!
]
```

Run `runtm validate` to catch missing dependencies before deploying.

### Configuration & Secrets

Use environment variables via `app/core/config.py`:

```python
from app.core.config import settings

api_key = settings.my_api_key
```

#### Declaring Environment Variables

If your service needs environment variables, declare them in `runtm.yaml`:

```yaml
env_schema:
  - name: API_KEY
    type: string
    required: true
    secret: true  # Redacted from logs, injected securely
    description: "API key for external service"
  - name: LOG_LEVEL
    type: string
    required: false
    default: "info"
```

#### Setting Secrets

Store secret values in `.env.local` (gitignored, cursorignored):

```bash
# Set a secret
runtm secrets set API_KEY=sk-xxx

# List env vars and their status
runtm secrets list
```

**Security:**
- `.env.local` is gitignored and cursorignored (AI agents can't see it)
- Secrets marked with `secret: true` are redacted from logs
- Secrets are injected to the deployment provider, never stored in Runtm DB

#### Proposing New Env Vars

If you need new env vars, create `runtm.requests.yaml`:

```yaml
requested:
  env_vars:
    - name: NEW_API_KEY
      type: string
      secret: true
      reason: "Needed for new integration"
notes:
  - "This API key is optional but improves reliability"
```

Then run `runtm approve` to merge into the manifest.

## Running Locally

```bash
# Recommended: auto-detects runtime and port from runtm.yaml
runtm run

# Or manually:
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8080
```

## Testing

Always add tests for new functionality:

```python
# tests/test_my_endpoint.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_my_endpoint():
    response = client.post("/api/v1/my-endpoint", json={"key": "value"})
    assert response.status_code == 200
```

Run tests with `pytest -q`.

## Before Deploying (MANDATORY STEPS)

⚠️ **STOP: Complete ALL steps below before running `runtm deploy`.**

### Step 1: Update Discovery Metadata (REQUIRED)

**You MUST edit `runtm.discovery.yaml` and replace ALL `# TODO:` placeholders with real content.**

Example of a properly filled discovery file:

```yaml
description: |
  A RESTful API for processing webhook events from Stripe and sending
  notifications to Slack. Handles payment confirmations and refunds.

summary: "Stripe webhook processor with Slack notifications"

capabilities:
  - "Process Stripe webhook events"
  - "Send Slack notifications"
  - "Payment status tracking"

use_cases:
  - "E-commerce apps needing payment notifications"
  - "Teams monitoring payment events"

tags:
  - api
  - webhook
  - stripe
  - slack

api:
  openapi_path: /openapi.json
  endpoints:
    - POST /api/v1/webhooks/stripe
    - GET /api/v1/payments/status
    - GET /health
```

**DO NOT deploy with `# TODO:` placeholders!** Apps with proper metadata are discoverable in the dashboard.

### Step 2: Authenticate (REQUIRED)

```bash
runtm status    # Check if logged in
runtm login     # If not authenticated, complete browser auth
```

### Step 3: Validate and Deploy

```bash
runtm validate  # Check project is valid
runtm deploy    # Deploy to production
```

### Deployment Checklist (follow in order)
1. ✅ Edit `runtm.discovery.yaml` - replace ALL `# TODO:` with real content
2. ✅ Run `runtm status` to check auth
3. ✅ If not authenticated, run `runtm login` and complete browser auth
4. ✅ Run `runtm validate` to check project
5. ✅ Run `runtm deploy` to deploy

### Common Errors
- **"nodename nor servname provided"** → Not logged in. Run `runtm login` first.
- **"Authentication required"** → Run `runtm login` first.
- **Network/DNS errors** → Run `runtm login` to configure the CLI.

## Database Feature (Optional)

### Requesting Database (Recommended for Agents)

Agents should request features via `runtm.requests.yaml` (which they CAN edit):

```yaml
# runtm.requests.yaml
requested:
  features:
    database: true
    reason: "App needs persistent data storage"
notes:
  - "Adding database for item storage"
```

Then the human runs: `runtm approve` to merge into manifest.

### Manual Enable (Fallback)

Alternatively, add to your `runtm.yaml`:

```yaml
features:
  database: true
```

This automatically:
- Creates a persistent Fly volume at `/data`
- Enables SQLite database at `/data/app.db`
- Runs migrations on startup

### Using the Database

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db

# Define a model
from sqlalchemy import Column, Integer, String


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


# Use in endpoint
@router.get("/items")
def get_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

### Creating Migrations

```bash
# Generate a migration
cd app/db && alembic revision --autogenerate -m "add items table"

# Migrations run automatically on startup
```

### External PostgreSQL

To use external PostgreSQL instead of SQLite:

```yaml
# runtm.yaml
env_schema:
  - name: DATABASE_URL
    type: string
    required: false
    secret: true
    description: "External PostgreSQL URL (defaults to SQLite)"
```

```bash
runtm secrets set DATABASE_URL=postgresql://user:pass@host:5432/db
```

## ⚠️ IMPORTANT: SQLite Single-Writer Constraint

This template uses SQLite by default, which supports only ONE writer at a time.

**This means:**
- Deploy with a single Fly machine (default)
- Do NOT scale to multiple machines with SQLite
- For horizontal scaling, switch to PostgreSQL

**To switch to Postgres:**
1. Set `DATABASE_URL` to your Postgres connection string
2. You can now scale to multiple machines

**What happens if you ignore this:**
- Multiple machines writing to SQLite = database corruption
- Silent data loss, hard-to-debug errors
