# Agent Instructions

This project is a fullstack Runtm web app with Next.js frontend and FastAPI backend. Instructions auto-apply in these AI IDEs:

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
- Ports must match `runtm.yaml` (frontend: 3000, backend: 8080)
- `health_path` must return 200
- Lockfiles must be in sync (both frontend and backend)

## What is a Web App?

Interactive UI + backend for real user workflows. Combines a Next.js frontend with a FastAPI backend.

**Use cases:**
- Internal dashboard/admin tool
- Customer portal
- AI app demo/MVP (uploads + history + auth)

## Critical Rules

1. **DO NOT change ports** - Frontend: 3000, Backend: 8080
2. **DO NOT remove health endpoints** - `/health` must exist
3. **DO NOT remove runtm.yaml** - Required for deployment
4. **KEEP types in sync** - Pydantic models ↔ TypeScript types
5. **Machine tier** - `starter` is valid by default; use `performance` or `pro` for heavier workloads
6. **ADD dependencies to PRODUCTION deps** - See "Adding Python Dependencies" below

## Project Structure

### Backend (`backend/`)

```
backend/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── api/v1/              # API endpoints (thin layer)
│   │   ├── health.py        # Health check
│   │   └── items.py         # CRUD endpoints
│   ├── services/            # Business logic
│   │   └── items.py         # Items service
│   ├── models/              # Pydantic models
│   │   ├── common.py        # Shared models
│   │   └── items.py         # Item models
│   └── core/
│       ├── config.py        # Settings
│       └── logging.py       # Logging
└── tests/
```

### Frontend (`frontend/`)

```
frontend/
├── app/                     # Next.js App Router
│   ├── layout.tsx           # Root layout
│   ├── page.tsx             # Homepage
│   └── globals.css          # Global styles
├── components/
│   ├── layout/              # Header, Footer
│   ├── pages/               # Page-specific components & logic
│   │   └── home/            # Home page folder
│   │       ├── index.ts     # Exports components & actions
│   │       ├── actions.ts   # Server Actions for data fetching
│   │       ├── HomeHero.tsx
│   │       ├── HomeFeatures.tsx
│   │       └── HomeDemo.tsx
│   └── ui/                  # Reusable UI components
├── lib/
│   ├── api.ts               # Client-side API helpers
│   └── store.ts             # Zustand store
└── types/
    └── index.ts             # TypeScript types
```

## Architecture

```
Frontend (Next.js)
    ├── components/pages/    → Page-specific components & logic
    ├── components/ui/       → Reusable UI components
    ├── lib/store.ts         → Zustand state management
    └── lib/api.ts           → API client
           ↓
Backend (FastAPI)
    ├── api/v1/              → Thin API layer
    ├── services/            → Business logic
    └── models/              → Pydantic models (source of truth)
```

## Adding New Features

### 1. Add Backend Service

Create business logic in `backend/app/services/`:

```python
# backend/app/services/my_feature.py
class MyFeatureService:
    def process(self, data: str) -> dict:
        # Business logic here
        return {"result": data}

my_feature_service = MyFeatureService()
```

### 2. Add Pydantic Models

Define models in `backend/app/models/`:

```python
# backend/app/models/my_feature.py
from pydantic import BaseModel

class MyRequest(BaseModel):
    input: str

class MyResponse(BaseModel):
    result: str
```

### 3. Add API Endpoint

Create thin endpoint in `backend/app/api/v1/`:

```python
# backend/app/api/v1/my_feature.py
from fastapi import APIRouter
from app.models.my_feature import MyRequest, MyResponse
from app.services.my_feature import my_feature_service

router = APIRouter()

@router.post("/my-feature", response_model=MyResponse)
async def my_feature(request: MyRequest) -> MyResponse:
    result = my_feature_service.process(request.input)
    return MyResponse(**result)
```

Register in `backend/app/main.py`.

## Adding Python Dependencies

⚠️ **CRITICAL**: When you import a new Python package, you MUST add it to the **production dependencies** in `backend/pyproject.toml`.

### Where to Add Dependencies

```toml
# backend/pyproject.toml

[project]
dependencies = [
    "fastapi>=0.100.0,<1.0",
    "uvicorn[standard]>=0.20.0,<1.0",
    "pydantic>=2.0.0,<3.0",
    "httpx>=0.24,<1.0",        # ← ADD NEW DEPS HERE (production)
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0,<9.0",        # ← Only testing/dev tools go here
    "ruff>=0.1.0",
]
```

### Common Mistake

❌ **WRONG**: Adding to `[project.optional-dependencies] dev`
```toml
[project.optional-dependencies]
dev = [
    "httpx>=0.24,<1.0",  # ← WRONG! Not installed in production!
]
```

✅ **CORRECT**: Adding to `[project] dependencies`
```toml
[project]
dependencies = [
    "httpx>=0.24,<1.0",  # ← CORRECT! Installed in production
]
```

### Validate Before Deploying

Always run `runtm validate` - it will catch missing dependencies:
```bash
runtm validate
# ✗ Missing dependency: 'httpx' is imported but not in pyproject.toml dependencies.
```

## Configuration & Secrets

Use environment variables via `backend/app/core/config.py`:

```python
from app.core.config import settings
api_key = settings.my_api_key
```

### Declaring Environment Variables

If your app needs environment variables, declare them in `runtm.yaml`:

```yaml
env_schema:
  - name: API_KEY
    type: string
    required: true
    secret: true  # Redacted from logs, injected securely
    description: "API key for external service"
  - name: DATABASE_URL
    type: url
    required: false
    secret: true
    description: "Database connection string"
```

### Setting Secrets

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

### Proposing New Env Vars

If you need new env vars, create `runtm.requests.yaml`:

```yaml
requested:
  env_vars:
    - name: AUTH_SECRET
      type: string
      secret: true
      reason: "Needed for authentication"
notes:
  - "Required if enabling user auth"
```

Then run `runtm approve` to merge into the manifest.

## Running Locally

```bash
# Recommended: starts both frontend and backend
# Uses Bun if available (3x faster), falls back to npm
runtm run

# Backend: http://localhost:8080
# Frontend: http://localhost:3000
```

Or manually run each in separate terminals:
```bash
# Terminal 1: Backend
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8080

# Terminal 2: Frontend (Bun preferred for speed)
cd frontend && bun install && bun run dev
# Or with npm:
cd frontend && npm install && npm run dev
```

### 4. Add TypeScript Types

Mirror Pydantic models in `frontend/types/index.ts`:

```typescript
export interface MyRequest {
  input: string;
}

export interface MyResponse {
  result: string;
}
```

### 5. Add API Client Method

Add to `frontend/lib/api.ts`:

```typescript
myFeature: async (data: MyRequest): Promise<MyResponse> => {
  const response = await fetch(`${API_BASE}/my-feature`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<MyResponse>(response);
},
```

### 6. Add Zustand Store (if needed)

Add to `frontend/lib/store.ts`.

### 7. Add Page Components & Actions

Create page-specific components and server actions in `frontend/components/pages/`:

```
components/pages/my-page/
├── index.ts          # Exports components & actions
├── actions.ts        # Server Actions for data fetching
├── MyPageHero.tsx    # Page-specific component
└── MyPageContent.tsx # Page-specific component
```

Example `actions.ts`:

```typescript
'use server';

import type { MyData } from '@/types';

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8080';

export async function getMyPageData(): Promise<MyData> {
  const response = await fetch(`${BACKEND_URL}/api/v1/my-endpoint`, {
    cache: 'no-store',
  });
  return response.json();
}
```

## Before Deploying (MANDATORY STEPS)

⚠️ **STOP: Complete ALL steps below before running `runtm deploy`.**

### Step 1: Update Discovery Metadata (REQUIRED)

**You MUST edit `runtm.discovery.yaml` and replace ALL `# TODO:` placeholders with real content.**

Example of a properly filled discovery file:

```yaml
description: |
  A fullstack dashboard for managing team workflows with real-time updates.
  Built with Next.js frontend and FastAPI backend.

summary: "Team workflow management dashboard"

capabilities:
  - "Real-time workflow updates"
  - "Team collaboration"
  - "Task management"
  - "Analytics dashboard"

use_cases:
  - "Teams managing project workflows"
  - "Managers tracking team progress"

tags:
  - dashboard
  - fullstack
  - workflow
  - team

api:
  openapi_path: /openapi.json
  endpoints:
    - GET /api/v1/workflows
    - POST /api/v1/workflows
    - GET /api/v1/tasks
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
- **"nodename nor servname provided"** → Not logged in. Run `runtm login`
- **"Authentication required"** → Run `runtm login`
- **Build failures** → Check both frontend and backend build locally first

## What NOT To Do

- ❌ Change frontend port from 3000
- ❌ Change backend port from 8080
- ❌ Remove `/health` endpoint
- ❌ Use old tier names such as `standard` in runtm.yaml
- ❌ **Add Python imports without adding to `[project] dependencies`** (NOT dev deps!)
- ❌ Store files locally at runtime (use `/data` volume with `features.database`)
- ❌ Use sync blocking calls in async endpoints
- ❌ Hardcode secrets (use environment variables)
- ❌ Forget to sync Pydantic ↔ TypeScript types
- ❌ Put business logic in API endpoints (use services/)
- ❌ Put page-specific logic in ui/ components (use pages/)
- ❌ Scale SQLite to multiple machines (use Postgres for horizontal scaling)

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
  - "Adding database for user data"
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

### Using the Database (Backend)

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db

# Define a model
from sqlalchemy import Column, Integer, String

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String)

# Use in endpoint
@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()
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

**What happens if you ignore this:**
- Multiple machines writing to SQLite = database corruption
- Silent data loss, hard-to-debug errors

## Authentication Feature (Optional)

### Requesting Auth (Recommended for Agents)

Agents should request auth via `runtm.requests.yaml`:

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
      reason: "Required for Better Auth session signing"
notes:
  - "Adding user authentication"
```

Then the human runs:
1. `runtm approve` - merges features and env_schema into manifest
2. `runtm secrets set AUTH_SECRET=$(openssl rand -base64 32)` - sets the secret

### Manual Enable (Fallback)

Alternatively, add to your `runtm.yaml`:

```yaml
features:
  database: true  # Required for auth
  auth: true

env_schema:
  - name: AUTH_SECRET
    type: string
    required: true
    secret: true
    description: "Secret for signing auth cookies/sessions"
```

Generate a secret:
```bash
openssl rand -base64 32 | runtm secrets set AUTH_SECRET=
```

### Auth Files

When auth is enabled, these files are available:

```
frontend/
├── lib/
│   ├── auth.ts          # Server-side auth (handler, getSession, requireUser)
│   └── auth-client.ts   # Client-side auth (useSession, signIn, signOut)
├── components/auth/
│   ├── LoginForm.tsx    # Email/password form
│   ├── MagicLinkForm.tsx # Passwordless form
│   ├── SocialButtons.tsx # OAuth buttons
│   └── AuthGuard.tsx    # Route protection
└── app/
    ├── api/auth/[...all]/route.ts  # Auth API handler
    └── (auth)/login/page.tsx       # Login page
```

### Protecting Routes

```tsx
import { AuthGuard } from "@/components/auth";

export default function ProtectedPage() {
  return (
    <AuthGuard>
      <h1>Only visible to logged-in users</h1>
    </AuthGuard>
  );
}
```

### Getting Session in Server Components

```tsx
import { getSession } from "@/lib/auth";
import { headers } from "next/headers";

export default async function ProfilePage() {
  const session = await getSession({ headers: headers() });
  if (!session?.user) redirect("/login");

  return <h1>Hello, {session.user.name}</h1>;
}
```

### Social OAuth (Optional)

Add OAuth providers by setting env vars:

```bash
runtm secrets set GOOGLE_CLIENT_ID=xxx
runtm secrets set GOOGLE_CLIENT_SECRET=xxx
runtm secrets set GITHUB_CLIENT_ID=xxx
runtm secrets set GITHUB_CLIENT_SECRET=xxx
```

### Auth Database Separation

Better Auth stores its data in `/data/auth.db`, separate from your app's `/data/app.db`. This separation is intentional:

1. **Cleaner ownership**: Auth schema is managed by Better Auth, app schema by you
2. **Safer migrations**: Auth migrations don't interfere with app migrations
3. **Security boundary**: Auth data doesn't pollute app queries

Do NOT merge these databases "for convenience" - the separation protects you.
