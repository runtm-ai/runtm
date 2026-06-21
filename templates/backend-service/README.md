# My Service - Backend Service Template

A FastAPI-based backend service template for [Runtm](https://runtm.com). See the [template docs](https://docs.runtm.com/templates/backend-service) for more details.

## What is a Backend Service?

Python compute for API-first services that can expose endpoints when needed.

**Use cases:**
- Scraper/crawler
- Agent backend + tool calls
- Webhook + data processing/integrations
- API services

## Stack

- **FastAPI** - High-performance Python API framework
- **Pydantic** - Data validation and serialization
- **Uvicorn** - ASGI server
- **Pytest** - Testing framework

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
pyproject.toml           # Dependencies
Dockerfile               # Container build
runtm.yaml               # Deployment manifest
```

## Architecture

The template follows a clean separation of concerns:

- **API Layer** (`api/v1/`): Thin controllers handling HTTP requests/responses
- **Services** (`services/`): Business logic and core functionality
- **Core** (`core/`): Configuration, utilities, and shared helpers

```
Request → API Endpoint → Service → Response
              ↓             ↓
         Validation    Business Logic
```

## Development

### Prerequisites
- Python 3.10+

### Running Locally

```bash
# Recommended: Use runtm run (auto-detects runtime and port)
runtm run

# Or manually:
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080/docs to see the API documentation.

### Running Tests

```bash
pytest -q
```

### Linting

```bash
ruff check .
ruff format .
```

## Adding Features

### 1. Create a Service

Add business logic to `app/services/`:

```python
# app/services/my_feature.py
class MyFeatureService:
    def process(self, input_data: str) -> dict:
        # Your business logic here
        return {"result": f"Processed: {input_data}"}

my_feature_service = MyFeatureService()
```

### 2. Create an API Endpoint

Add a thin endpoint in `app/api/v1/`:

```python
# app/api/v1/my_feature.py
from fastapi import APIRouter
from pydantic import BaseModel
from app.services.my_feature import my_feature_service

router = APIRouter()

class MyRequest(BaseModel):
    input: str

class MyResponse(BaseModel):
    result: str

@router.post("/my-feature", response_model=MyResponse)
async def my_feature(request: MyRequest) -> MyResponse:
    result = my_feature_service.process(request.input)
    return MyResponse(**result)
```

### 3. Register the Router

In `app/main.py`:

```python
from app.api.v1.my_feature import router as my_feature_router
app.include_router(my_feature_router, prefix="/api/v1")
```

### 4. Add Tests

```python
# tests/test_my_feature.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_my_feature():
    response = client.post("/api/v1/my-feature", json={"input": "test"})
    assert response.status_code == 200
    assert "result" in response.json()
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/api/v1/run` | Main tool endpoint |

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

## Configuration

Environment variables can be added via `app/core/config.py`:

```python
@dataclass
class Settings:
    my_api_key: str = os.environ.get("MY_API_KEY", "")
```

## Constraints

- Port must be 8080
- `/health` must exist and return 200
- Keep startup time under 10 seconds

## Database (Optional)

Enable persistent SQLite storage by adding to `runtm.yaml`:

```yaml
features:
  database: true
```

This creates a persistent volume at `/data` and enables SQLAlchemy:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import Base, get_db
from sqlalchemy import Column, Integer, String

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

@router.get("/items")
def get_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

**Key features:**
- SQLite with WAL mode for better concurrent reads
- Auto-migration on startup via Alembic
- Foreign key constraints enabled
- Data persists across redeploys

**External PostgreSQL:** Set `DATABASE_URL` to use external Postgres:

```yaml
env_schema:
  - name: DATABASE_URL
    type: string
    required: false
    secret: true
```

Note: SQLite supports a single writer. For horizontal scaling, use Postgres.

## License

MIT
