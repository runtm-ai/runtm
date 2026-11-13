# Runtm Tool Instructions

This is an Runtm-deployed FastAPI tool. Follow these rules:

## Critical Rules

1. **Port 8080** - App must listen on port 8080
2. **Health Endpoint** - `GET /health` must return 200 quickly
3. **Stateless** - No databases, no local file storage
4. **Fast Startup** - Ready in <10 seconds

## Structure

- `app/api/v1/` - Add new endpoints here
- `app/api/v1/health.py` - DO NOT MODIFY
- `app/core/config.py` - Environment variables
- `runtm.yaml` - DO NOT DELETE

## Adding Endpoints

```python
# app/api/v1/my_endpoint.py
from fastapi import APIRouter

router = APIRouter()


@router.post("/my-endpoint")
async def my_endpoint(data: dict):
    return {"result": "success"}
```

Register in `app/main.py`:
```python
from app.api.v1.my_endpoint import router as my_endpoint_router

app.include_router(my_endpoint_router, prefix="/api/v1")
```

## Deployment

```bash
runtm validate
runtm up
```

## Don'ts

- Don't change port from 8080
- Don't remove /health
- Don't add databases
- Don't store files locally
- Don't add heavy dependencies

