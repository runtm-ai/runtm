# runtm-worker

Build and deploy worker for Runtm. See the [self-hosting docs](https://docs.runtm.com/self-hosting/overview) for deployment setup.

## Pipeline

### Remote Builder (Default, Recommended)

1. Unzip artifact from storage
2. Validate manifest against schema
3. Create Fly app and allocate IPs
4. Run `flyctl deploy` (builds AND deploys in one step)
   - Auto-generates `fly.toml` with optimized health checks
   - Builds on Fly's remote builders (fast, no local Docker)
   - Deploys to Fly Machine with health check verification
5. Save provider resource and verify health
6. Update deployment status + URL in DB
7. Persist logs

### Local Build (Fallback)

1. Unzip artifact from storage
2. Validate manifest against schema
3. Build Docker container locally with BuildKit
4. Push to Fly.io registry
5. Create/update Fly Machine via Machines API
6. Poll for health check
7. Update deployment status + URL in DB
8. Persist logs

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run worker
python -m runtm_worker.main

# Run tests
pytest
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `REDIS_URL` | Redis connection string | Required |
| `FLY_API_TOKEN` | Fly.io API token | Required |
| `ARTIFACT_STORAGE_PATH` | Local artifact storage path | `/artifacts` |
| `ARTIFACT_STORAGE_BACKEND` | Storage backend: `local` or `s3` | `local` |
| `BUCKET_NAME` | S3/Tigris bucket name (when backend=s3) | - |
| `AWS_ENDPOINT_URL_S3` | S3 endpoint URL (e.g. `https://fly.storage.tigris.dev`) | - |
| `USE_REMOTE_BUILDER` | Use Fly's remote builder (faster) | `true` |

## Providers

The worker uses a provider interface to support multiple deployment backends:

- `FlyProvider` - Fly.io Machines (default)
- Future: Cloud Run, etc.

Note: When using the remote builder (default), most deployment is handled by
`flyctl deploy`. The FlyProvider is still used for app creation, IP allocation,
machine listing, health verification, and custom domain management.

