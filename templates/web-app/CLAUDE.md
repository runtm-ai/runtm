# Web App - Claude Instructions

This is a fullstack Runtm web app with Next.js frontend and FastAPI backend.

## Contract Rules

| File | Editable? | How to Change |
|------|-----------|---------------|
| `runtm.yaml` | NO | Use `runtm apply` or CLI commands |
| `Dockerfile` | YES | Edit freely, but must expose declared port |
| Health endpoint | YES | Must return 200 at manifest's `health_path` |
| Lockfiles | AUTO | Managed by `runtm run`/`runtm deploy` |

### Invariants (enforced at deploy)
- Ports must match `runtm.yaml` (frontend: 3000, backend: 8080)
- `health_path` must return 200
- Lockfiles must be in sync (both frontend and backend)

## What is a Web App?

Interactive UI + backend for real user workflows.

**Use cases:**
- Internal dashboard/admin tool
- Customer portal
- AI app demo/MVP (uploads + history + auth)

## Critical Constraints

1. **Ports are fixed**: Frontend=3000, Backend=8080
2. **Health endpoint required**: `/health` must return 200
3. **Keep types in sync**: Pydantic models ↔ TypeScript types
4. **No database**: In-memory storage only for V0
5. **Machine tier**: `starter` is valid by default; use `performance` or `pro` for heavier workloads
6. **Dependencies in `[project] dependencies`**: NOT in `dev` (see below)

## Project Structure

### Backend
```
backend/app/
├── api/v1/          # Thin API layer (routing, validation)
├── services/        # Business logic (core functionality)
├── models/          # Pydantic models (source of truth)
└── core/            # Config, utilities
```

### Frontend
```
frontend/
├── app/                 # Next.js pages
├── components/
│   ├── layout/          # Header, Footer
│   ├── pages/           # Page-specific components & logic
│   │   └── home/
│   │       ├── actions.ts   # Server Actions (data fetching)
│   │       ├── HomeHero.tsx
│   │       └── HomeDemo.tsx
│   └── ui/              # Reusable UI components
├── lib/
│   ├── store.ts         # Zustand state
│   └── api.ts           # Client-side API helpers
└── types/               # TypeScript types (mirror Pydantic)
```

## Architecture Patterns

### Backend: Thin API + Services
```python
# API layer - thin, just routing
@router.post("/items")
async def create_item(data: ItemCreate) -> Item:
    return items_service.create_item(data)

# Service layer - business logic
class ItemsService:
    def create_item(self, data: ItemCreate) -> Item:
        # All business logic here
        ...
```

### Frontend: Pages + UI Components
```
components/
├── pages/home/          # Page-specific (HomeHero, HomeDemo)
└── ui/                  # Reusable (Button, Card, ItemCard)
```

## Quick Reference

### Adding a Feature

1. **Backend service**: `backend/app/services/my_feature.py`
2. **Pydantic models**: `backend/app/models/my_feature.py`
3. **API endpoint**: `backend/app/api/v1/my_feature.py`
4. **TypeScript types**: `frontend/types/index.ts`
5. **Page folder**: `frontend/components/pages/my-page/`
   - `actions.ts` - Server Actions for data fetching
   - `MyPageComponent.tsx` - Page components

### Running Locally

```bash
# Recommended: starts both frontend and backend
# Uses Bun if available (3x faster), falls back to npm
runtm run

# Or manually in two terminals:
# Terminal 1: cd backend && pip install -e . && uvicorn app.main:app --reload --port 8080
# Terminal 2: cd frontend && bun install && bun run dev  # or npm if no bun
```

### Before Deploy (REQUIRED)

**You MUST edit `runtm.discovery.yaml` before deploying:**

1. Replace ALL `# TODO:` placeholders with real content
2. Fill in: description, summary, capabilities, use_cases, tags
3. Include `api.openapi_path: /openapi.json` and list main endpoints

**DO NOT deploy with `# TODO:` placeholders!**

### Deployment

```bash
# 1. Edit runtm.discovery.yaml first!
# 2. Then:
runtm status    # Check auth
runtm login     # If not authenticated
runtm validate  # Validate project
runtm deploy    # Deploy
```

## Adding Python Dependencies

⚠️ **When you import a package, add it to PRODUCTION deps, NOT dev deps!**

```toml
# backend/pyproject.toml

[project]
dependencies = [
    "httpx>=0.24,<1.0",  # ✅ CORRECT - installed in production
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0,<9.0",  # Only testing tools here
    # "httpx>=0.24,<1.0",  # ❌ WRONG - not installed in production!
]
```

Run `runtm validate` to catch missing dependencies before deployment.

## Environment Variables & Secrets

### Declaring Env Vars

If your app needs env vars, declare them in `runtm.yaml`:

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
    - name: AUTH_SECRET
      secret: true
      reason: "Needed for auth"
```

Then run `runtm approve` to merge into manifest.

## Key Rules

- ✅ Put business logic in `services/`
- ✅ Keep API endpoints thin
- ✅ Page-specific components go in `components/pages/`
- ✅ Each page folder has its own `actions.ts` for data fetching
- ✅ Reusable UI goes in `components/ui/`
- ✅ Sync Pydantic and TypeScript types
- ✅ Use a valid tier in runtm.yaml: `starter`, `performance`, or `pro`
- ✅ Add new imports to `[project] dependencies` in pyproject.toml
- ❌ Don't put logic in API endpoints
- ❌ Don't mix page-specific and reusable components
- ❌ Don't use old tier names such as `standard` in runtm.yaml
- ❌ Don't add production deps to `[project.optional-dependencies] dev`
- ❌ Don't scale SQLite to multiple machines (use Postgres instead)

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

Use SQLAlchemy in backend:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)

@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()
```

⚠️ **SQLite limits**: Single writer only. For horizontal scaling, use external Postgres via `DATABASE_URL`.

## Authentication (Optional)

### Requesting Auth (Agent Workflow)

Create `runtm.requests.yaml` to propose auth:

```yaml
# runtm.requests.yaml
requested:
  features:
    database: true
    auth: true
    reason: "App needs user accounts"
  env_vars:
    - name: AUTH_SECRET
      type: string
      required: true
      secret: true
      reason: "Required for session signing"
```

Human runs:
1. `runtm approve`
2. `runtm secrets set AUTH_SECRET=$(openssl rand -base64 32)`

### Manual Enable (Fallback)

Or add directly to `runtm.yaml`:

```yaml
features:
  database: true
  auth: true

env_schema:
  - name: AUTH_SECRET
    type: string
    required: true
    secret: true
```

Generate secret: `openssl rand -base64 32`

### Using Auth

```tsx
// Protect routes
import { AuthGuard } from "@/components/auth";

<AuthGuard>
  <ProtectedContent />
</AuthGuard>

// Get session in server components
import { getSession } from "@/lib/auth";
const session = await getSession({ headers: headers() });

// Client-side auth
import { useSession, signIn, signOut } from "@/lib/auth-client";
```

Auth stores data in separate `/data/auth.db` - don't merge with app database.
