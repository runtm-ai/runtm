# Backend Service - Claude Instructions

This is a Runtm backend service that will be deployed as a containerized Python API.

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

Python compute for API-first services. Use cases: scraper/crawler, agent backend + tool calls, webhook + data processing/integrations.

## Critical Rules

1. **Port 8080** - The app MUST listen on port 8080. Never change this.
2. **Health Endpoint** - `GET /health` MUST exist, return 200, and respond in <100ms
3. **Stateless** - No local file storage, databases, or persistent state
4. **Fast Startup** - App must be ready in <10 seconds

## Project Structure

```
app/
├── main.py              # FastAPI entry point
├── api/v1/              # API endpoints (thin layer)
│   ├── health.py        # Health check (DO NOT MODIFY)
│   └── run.py           # Main tool endpoint
├── services/            # Business logic
│   └── processor.py     # Core processing service
└── core/
    ├── config.py        # Environment config
    └── logging.py       # Logging setup
tests/                   # Pytest tests
runtm.yaml               # Deployment manifest (DO NOT DELETE)
```

## Architecture Pattern

- **API Layer** (`api/v1/`): Thin controllers - routing, validation
- **Services** (`services/`): Business logic - processing, transformations
- **Core** (`core/`): Configuration, utilities

## Adding Features

### 1. Add Service (business logic)

```python
# app/services/my_service.py
class MyService:
    def process(self, data: str) -> dict:
        # Business logic here
        return {"result": data}


my_service = MyService()
```

### 2. Add Endpoint (thin layer)

```python
# app/api/v1/my_endpoint.py
from fastapi import APIRouter
from app.services.my_service import my_service

router = APIRouter()


@router.post("/my-endpoint")
async def my_endpoint(data: dict):
    return my_service.process(data["input"])
```

### 3. Register in main.py

```python
from app.api.v1.my_endpoint import router as my_endpoint_router

app.include_router(my_endpoint_router, prefix="/api/v1")
```

## Dependencies

⚠️ Add to `[project] dependencies`, NOT `dev`:

```toml
# pyproject.toml
[project]
dependencies = [
    "your-package>=1.0,<2.0",  # ✅ Installed in production
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",             # Only test tools here
    # "your-package",          # ❌ NOT installed in production!
]
```

Run `runtm validate` to catch missing dependencies.

## Environment Variables & Secrets

### Declaring Env Vars

If your service needs env vars, declare them in `runtm.yaml`:

```yaml
env_schema:
  - name: API_KEY
    type: string
    required: true
    secret: true       # Redacted from logs
    description: "External API key"
```

### Setting Secrets

```bash
runtm secrets set API_KEY=sk-xxx   # Stores in .env.local
runtm secrets list                  # Shows status
```

**Security:** `.env.local` is gitignored and cursorignored (AI agents can't see it).

### Proposing New Env Vars

Create `runtm.requests.yaml` to propose changes:

```yaml
requested:
  env_vars:
    - name: NEW_KEY
      secret: true
      reason: "Needed for integration"
```

Then run `runtm approve` to merge into manifest.

## Running Locally

```bash
runtm run          # Auto-detects runtime, starts on port 8080
```

## Testing

Add tests in `tests/` and run with `pytest -q`.

## Before Deploy (REQUIRED)

**You MUST edit `runtm.discovery.yaml` before deploying:**

1. Replace ALL `# TODO:` placeholders with real content
2. Fill in: description, summary, capabilities, use_cases, tags
3. Include `api.openapi_path: /openapi.json` and list main endpoints

**DO NOT deploy with `# TODO:` placeholders!**

## Deployment

```bash
# 1. Edit runtm.discovery.yaml first!
# 2. Then:
runtm status    # Check auth
runtm login     # If not authenticated
runtm validate  # Validate project
runtm deploy    # Deploy
```

## Constraints

- ❌ Don't change port from 8080
- ❌ Don't remove/break /health
- ❌ Don't store files locally (use `/data` with `features.database`)
- ❌ Don't add heavy dependencies
- ❌ Don't add production deps to `[project.optional-dependencies] dev`
- ❌ Don't scale SQLite to multiple machines (use Postgres instead)
- ✅ Keep API endpoints thin
- ✅ Put business logic in services/
- ✅ Add imports to `[project] dependencies`

## Database (Optional)

### Requesting Database (Agent Workflow)

Create `runtm.requests.yaml` to propose features:

```yaml
# runtm.requests.yaml
requested:
  features:
    database: true
    reason: "App needs persistent data storage"
```

Then human runs: `runtm approve`

### Manual Enable (Fallback)

Or add directly to `runtm.yaml`:

```yaml
features:
  database: true
```

Then use SQLAlchemy:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String)


@router.get("/items")
def get_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

⚠️ **SQLite limits**: Single writer only. For horizontal scaling, use external Postgres via `DATABASE_URL`.
