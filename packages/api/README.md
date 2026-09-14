# runtm-api

FastAPI control plane for Runtm. See the [API docs](https://docs.runtm.com/api/overview) for endpoint reference.

**Hosted API:** [app.runtm.com](https://app.runtm.com)

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v0/deployments` | Create deployment (multipart: manifest + artifact.zip) |
| GET | `/v0/deployments/:id` | Get deployment status, URL, timestamps, error |
| GET | `/v0/deployments/:id/logs` | Get build/deploy logs |

### Creating Deployments

**POST `/v0/deployments`**

Creates a new deployment or redeploys an existing one (based on manifest name).

**Query Parameters:**
- `new=true` - Force creation of new deployment instead of redeploying existing
- `tier=starter|performance|pro` - Override machine tier from manifest

**Request:**
- `manifest` (file) - `runtm.yaml` manifest file
- `artifact` (file) - `artifact.zip` containing project files

**Headers:**
- `Authorization: Bearer <token>` - API authentication token
- `Idempotency-Key: <key>` - Optional idempotency key for retries

**Example:**
```bash
curl -X POST "http://localhost:8000/v0/deployments?tier=performance" \
  -H "Authorization: Bearer $RUNTM_API_KEY" \
  -F "manifest=@runtm.yaml" \
  -F "artifact=@artifact.zip"
```

**Machine Tiers:**

All deployments use **auto-stop** for cost savings (machines stop when idle and start automatically on traffic).

| Tier | CPUs | Memory | Est. Cost |
|------|------|--------|-----------|
| `starter` (default) | 2 shared | 2GB | ~$15/month* |
| `performance` | 4 shared | 4GB | ~$30/month* |
| `pro` | 4 shared | 8GB | ~$55/month* |

*Costs are estimates for 24/7 operation. With auto-stop, costs are much lower for low-traffic services.

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run migrations
alembic upgrade head

# Start server
uvicorn runtm_api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
pytest
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `REDIS_URL` | Redis connection string | Required |
| `RUNTM_API_SECRET` | API authentication secret (what server validates against) | Required |
| `ARTIFACT_STORAGE_PATH` | Local artifact storage path | `/artifacts` |
| `ARTIFACT_STORAGE_BACKEND` | Storage backend: `local` or `s3` | `local` |
| `BUCKET_NAME` | S3/Tigris bucket name (when backend=s3) | - |
| `AWS_ENDPOINT_URL_S3` | S3 endpoint URL (e.g. `https://fly.storage.tigris.dev`) | - |
| `AUTH_MODE` | Auth mode: `single_tenant` or `multi_tenant` | `single_tenant` |
