# My Web App - Fullstack Template

Fullstack template: Next.js frontend + FastAPI backend for [Runtm](https://runtm.com). See the [template docs](https://docs.runtm.com/templates/web-app) for more details.

## What is a Web App?

Interactive UI + backend for real user workflows. Combines a Next.js frontend with a FastAPI backend.

**Use cases:**
- Internal dashboard/admin tool
- Customer portal
- AI app demo/MVP (uploads + history + auth)

## Stack

### Frontend
- **Next.js 14** - React framework with App Router
- **TypeScript** - Full type safety
- **Zustand** - Lightweight state management
- **Tailwind CSS** - Utility-first styling
- **Server Actions** - Backend calls from components

### Backend
- **FastAPI** - High-performance Python API
- **Pydantic** - Data validation and serialization
- **Uvicorn** - ASGI server

## Project Structure

```
web-app/
├── frontend/                    # Next.js frontend
│   ├── app/                     # App Router pages
│   ├── components/
│   │   ├── layout/              # Header, Footer
│   │   ├── pages/               # Page-specific components & logic
│   │   │   └── home/            # Home page folder
│   │   │       ├── index.ts     # Exports
│   │   │       ├── actions.ts   # Server Actions (data fetching)
│   │   │       ├── HomeHero.tsx
│   │   │       ├── HomeFeatures.tsx
│   │   │       └── HomeDemo.tsx
│   │   └── ui/                  # Reusable UI components
│   ├── lib/
│   │   ├── api.ts               # Client-side API helpers
│   │   └── store.ts             # Zustand store
│   └── types/                   # TypeScript types
├── backend/                     # FastAPI backend
│   ├── app/
│   │   ├── api/v1/              # API endpoints (thin layer)
│   │   ├── services/            # Business logic
│   │   ├── models/              # Pydantic models
│   │   └── core/                # Config, logging
│   └── tests/
├── Dockerfile                   # Multi-stage build
└── runtm.yaml                   # Deployment manifest
```

## Architecture

### Backend: Thin API + Services

The backend follows a clean separation:

- **API Layer** (`api/v1/`): Thin controllers - routing, validation, HTTP concerns
- **Services** (`services/`): Business logic - processing, domain rules
- **Models** (`models/`): Data structures - Pydantic models (source of truth)

```
Request → API Endpoint → Service → Response
              ↓             ↓
         Validation    Business Logic
```

### Frontend: Pages + UI Components

The frontend organizes components by scope:

- **Pages** (`components/pages/`): Page-specific components and logic
- **UI** (`components/ui/`): Reusable, generic UI components
- **Layout** (`components/layout/`): App-wide layout components

```
app/page.tsx
    └── components/pages/home/    # Page-specific
            ├── HomeHero.tsx
            ├── HomeFeatures.tsx
            └── HomeDemo.tsx
                    └── components/ui/    # Reusable
                            ├── ItemCard.tsx
                            └── AddItemForm.tsx
```

## Development

### Prerequisites
- Node.js 20+
- Python 3.9+

### Running Locally

```bash
# Recommended: Use runtm run (starts both frontend and backend)
runtm run
```

This automatically starts:
- Backend on http://localhost:8080
- Frontend on http://localhost:3000

#### Manual Setup (if needed)

**Terminal 1 - Backend:**
```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8080
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm install
npm run dev
```

### Running Tests

**Backend:**
```bash
cd backend
pytest -q
```

**Frontend:**
```bash
cd frontend
npm run lint
```

## Type Safety

Types are shared between frontend and backend:

- **Backend**: Pydantic models in `backend/app/models/`
- **Frontend**: TypeScript types in `frontend/types/`

Keep these in sync when modifying the API contract.

## Adding Features

### 1. Add Backend Service

```python
# backend/app/services/my_feature.py
class MyFeatureService:
    def process(self, data: str) -> dict:
        return {"result": f"Processed: {data}"}

my_feature_service = MyFeatureService()
```

### 2. Add Pydantic Models

```python
# backend/app/models/my_feature.py
from pydantic import BaseModel

class MyRequest(BaseModel):
    input: str

class MyResponse(BaseModel):
    result: str
```

### 3. Add API Endpoint (thin)

```python
# backend/app/api/v1/my_feature.py
from app.services.my_feature import my_feature_service
from app.models.my_feature import MyRequest, MyResponse

@router.post("/my-feature", response_model=MyResponse)
async def my_feature(request: MyRequest) -> MyResponse:
    return my_feature_service.process(request.input)
```

### 4. Add TypeScript Types

```typescript
// frontend/types/index.ts
export interface MyRequest {
  input: string;
}

export interface MyResponse {
  result: string;
}
```

### 5. Add Page Components & Actions

Each page folder contains its own server actions for data fetching:

```
frontend/components/pages/my-page/
├── index.ts              # Exports components & actions
├── actions.ts            # Server Actions for data fetching
├── MyPageHero.tsx        # Page-specific hero
└── MyPageContent.tsx     # Page-specific content
```

Example `actions.ts`:

```typescript
'use server';

import type { MyData } from '@/types';

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8080';

export async function getMyPageData(): Promise<MyData> {
  const response = await fetch(`${BACKEND_URL}/api/v1/my-data`, {
    cache: 'no-store',
  });
  return response.json();
}
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/v1/items` | List all items |
| POST | `/api/v1/items` | Create item |
| GET | `/api/v1/items/:id` | Get item |
| PATCH | `/api/v1/items/:id` | Update item |
| DELETE | `/api/v1/items/:id` | Delete item |

## Deployment

Get your API key at [app.runtm.com](https://app.runtm.com) and deploy:

```bash
# Login (first time only)
runtm login

# Validate
runtm validate

# Deploy
runtm deploy
```

### Resource Requirements

The generated template uses the `starter` tier by default. Use `performance` or `pro` for fullstack apps that need more CPU or memory.

## Customization

### Styling

Edit CSS variables in `frontend/app/globals.css`:
- `--background` - Page background
- `--foreground` - Text color
- `--accent` - Brand color
- `--muted` - Secondary text
- `--border` - Border color

## Database (Optional)

Enable persistent SQLite storage by adding to `runtm.yaml`:

```yaml
features:
  database: true
```

This creates a persistent volume at `/data` and enables SQLAlchemy in the backend:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db
from sqlalchemy import Column, Integer, String

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)

@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(User).all()
```

**Key features:**
- SQLite with WAL mode for better concurrent reads
- Auto-migration on startup via Alembic
- Data persists across redeploys

Note: SQLite supports a single writer. For horizontal scaling, use external Postgres via `DATABASE_URL`.

## Authentication (Optional)

Enable user authentication with Better Auth:

```yaml
features:
  database: true  # Required for auth
  auth: true

env_schema:
  - name: AUTH_SECRET
    type: string
    required: true
    secret: true
```

Generate a secret: `openssl rand -base64 32`

### Frontend Components

Auth provides ready-to-use components:

```tsx
// Protect routes
import { AuthGuard } from "@/components/auth";

export default function DashboardPage() {
  return (
    <AuthGuard>
      <h1>Only visible to logged-in users</h1>
    </AuthGuard>
  );
}

// Client-side auth hooks
import { useSession, signIn, signOut } from "@/lib/auth-client";

function UserMenu() {
  const { data: session } = useSession();
  if (!session?.user) return <button onClick={() => signIn.email({...})}>Login</button>;
  return <button onClick={() => signOut()}>Logout</button>;
}
```

### Available Components

| Component | Description |
|-----------|-------------|
| `LoginForm` | Email/password login with optional signup |
| `MagicLinkForm` | Passwordless email login |
| `SocialButtons` | OAuth buttons (Google, GitHub if configured) |
| `AuthGuard` | Route protection wrapper |

### Server-side Auth

```tsx
import { getSession, requireUser } from "@/lib/auth";
import { headers } from "next/headers";

// In Server Components
export default async function ProfilePage() {
  const session = await getSession({ headers: headers() });
  if (!session?.user) redirect("/login");
  return <h1>Hello, {session.user.name}</h1>;
}

// In API routes
export async function GET(request: Request) {
  const user = await requireUser(request.headers); // Throws if not authenticated
  return Response.json({ user });
}
```

### Social OAuth (Optional)

Add OAuth providers via env vars:

```bash
runtm secrets set GOOGLE_CLIENT_ID=xxx
runtm secrets set GOOGLE_CLIENT_SECRET=xxx
runtm secrets set GITHUB_CLIENT_ID=xxx
runtm secrets set GITHUB_CLIENT_SECRET=xxx
```

## License

MIT
