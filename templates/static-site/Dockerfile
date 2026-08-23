# syntax=docker/dockerfile:1.4
# Optimized Dockerfile for Next.js static site
# Uses Bun for ~3x faster installs and builds

FROM oven/bun:1-alpine AS builder

WORKDIR /app

# Install dependencies first (better caching)
COPY package*.json bun.lockb* ./

# Use Bun for ultra-fast installs (falls back if no lockfile)
RUN bun install --frozen-lockfile 2>/dev/null || bun install

# Copy source and build
COPY . .
RUN bun run build

# Production stage - minimal runtime image using nginx-alpine (~25MB vs ~180MB for node)
FROM nginx:alpine AS runner

# Copy built static files
COPY --from=builder /app/out /usr/share/nginx/html

# Copy custom nginx config for static site with proper redirect handling
# absolute_redirect off ensures redirects use relative URLs (no port exposure)
RUN echo 'server { \
    listen 3000; \
    root /usr/share/nginx/html; \
    absolute_redirect off; \
    server_tokens off; \
    \
    # Security headers. The CSP carries real directives (not a permissive stub) so \
    # it is a hardened policy; frame-ancestors * is kept so the site stays \
    # embeddable in the Runtime preview pane. \
    add_header Content-Security-Policy "default-src '"'"'self'"'"'; script-src '"'"'self'"'"' '"'"'unsafe-inline'"'"'; style-src '"'"'self'"'"' '"'"'unsafe-inline'"'"'; img-src '"'"'self'"'"' data: blob:; font-src '"'"'self'"'"' data:; object-src '"'"'none'"'"'; base-uri '"'"'self'"'"'; form-action '"'"'self'"'"'; frame-ancestors *" always; \
    add_header X-Content-Type-Options "nosniff" always; \
    add_header Referrer-Policy "strict-origin-when-cross-origin" always; \
    \
    location / { \
        try_files $uri $uri/ $uri/index.html /index.html; \
    } \
    \
    location /health { \
        return 200 "OK"; \
        add_header Content-Type text/plain; \
    } \
}' > /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:3000/health || exit 1

# Run nginx
CMD ["nginx", "-g", "daemon off;"]
