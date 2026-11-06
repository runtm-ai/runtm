# syntax=docker/dockerfile:1.4
# Optimized multi-stage Dockerfile for fullstack app (Next.js + FastAPI)
# 
# Optimizations applied:
# - Bun instead of npm (~3x faster installs and builds)
# - Standalone Next.js output (no npm ci in final stage, ~10-15s savings)
# - Minimal runtime image (only what's needed to run)
# - BuildKit for parallel multi-stage builds

# ============================================
# Stage 1: Build Frontend with Bun (runs in parallel with backend deps)
# ============================================
FROM oven/bun:1-alpine AS frontend-builder

WORKDIR /app/frontend

# Install dependencies first (better caching)
COPY frontend/package*.json frontend/bun.lock* ./

# Use Bun for ultra-fast installs
RUN bun install --frozen-lockfile 2>/dev/null || bun install

# Copy source and build
COPY frontend/ ./

# Set backend URL for build-time (internal container communication)
ENV BACKEND_URL=http://localhost:8080

# Build with Bun (uses Next.js under the hood)
RUN bun run build

# ============================================
# Stage 2: Build Backend Dependencies
# ============================================
FROM python:3.12-alpine AS backend-builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache gcc musl-dev libffi-dev curl

# Copy entire backend directory (pyproject.toml + source code)
COPY backend/ ./backend/

# Use BuildKit cache mount for pip
# Install the backend package and its dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir ./backend

# ============================================
# Stage 3: Production Runtime (Minimal)
# ============================================
FROM python:3.12-alpine AS runner

WORKDIR /app

# Install only Node.js runtime (no npm/bun needed - standalone is self-contained)
# curl for health checks
RUN apk add --no-cache nodejs curl

# Create data directory for persistent storage (SQLite, uploads, etc.)
# Note: Do NOT use VOLUME directive - Fly handles volume mounts externally
RUN mkdir -p /data

# Copy Python packages from builder (includes uvicorn and all dependencies)
COPY --from=backend-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend-builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy backend code
COPY backend/ ./backend/

# Copy standalone Next.js output (includes minimal node_modules)
# Standalone mode bundles everything needed - no npm ci required!
COPY --from=frontend-builder /app/frontend/.next/standalone ./frontend/
COPY --from=frontend-builder /app/frontend/.next/static ./frontend/.next/static
COPY --from=frontend-builder /app/frontend/public ./frontend/public

# Set environment variables
ENV NODE_ENV=production
ENV BACKEND_URL=http://localhost:8080
ENV PYTHONUNBUFFERED=1

# Expose the frontend port (this is what Runtm expects)
EXPOSE 3000

# Health check against the frontend which proxies to backend
# Fullstack apps need longer start period for both services to initialize
HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=5 \
    CMD curl -f http://localhost:3000/health || exit 1

# Create startup script for better process management and logging
COPY <<'EOF' /app/start.sh
#!/bin/sh
# Startup script for fullstack app (Next.js + FastAPI)
# Optimized: Uses Next.js standalone server (no npm/bun needed at runtime)

# Handle shutdown signals
cleanup() {
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "=== Starting Runtm Fullstack App ==="
echo "Python: $(python --version)"
echo "Node: $(node --version)"

# Start backend
echo "Starting backend on port 8080..."
cd /app/backend
python -c "import app.main; print('Backend imports OK')" || { echo "ERROR: Backend import failed!"; exit 1; }
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
BACKEND_PID=$!
echo "Backend started with PID $BACKEND_PID"

# Wait for backend to be ready
echo "Waiting for backend to start..."
sleep 2
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "ERROR: Backend process died!"
    exit 1
fi
echo "Backend is running"

# Start frontend using standalone server (ultra-fast startup)
echo "Starting frontend on port 3000 (standalone mode)..."
cd /app/frontend
node server.js &
FRONTEND_PID=$!
echo "Frontend started with PID $FRONTEND_PID"

echo "=== All services started ==="

# Wait for any process to exit
wait
EOF
RUN chmod +x /app/start.sh

# Start both services using the startup script
CMD ["/bin/sh", "/app/start.sh"]
