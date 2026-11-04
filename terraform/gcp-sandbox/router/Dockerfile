# Runtm sandbox preview router for previews on your own domain (module input
# `preview`): Caddy with caddy-jwt, which verifies Runtm's signed preview token
# against Runtm's published keys. Published as ghcr.io/runtm-ai/runtm-sandbox-router
# by .github/workflows/sandbox-router-image.yml (:latest, plus :sha-<commit> to
# pin). The module mounts its rendered Caddyfile over the default copied below.
#
# Build context: this directory.

FROM caddy:2.11.7-builder-alpine AS builder

# grpc arrives indirectly through Caddy's OpenTelemetry exporters; the pin
# keeps the fixed release (CVE-2026-84445, CVE-2026-84304). Drop it once Caddy
# requires >= v1.83.2 itself. Same pins as Runtm's hosted preview proxy.
RUN xcaddy build v2.11.7 \
    --with github.com/ggicci/caddy-jwt@v1.4.0 \
    --with google.golang.org/grpc@v1.83.2

FROM caddy:2.11.7-alpine

# Alpine security fixes ahead of the next upstream caddy:2-alpine rebuild.
RUN apk upgrade --no-cache

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile.preview /etc/caddy/Caddyfile

# The module's env vars are read when the Caddyfile is parsed; check it parses.
RUN RUNTM_PREVIEW_DOMAIN=example.com RUNTM_PREVIEW_DOMAIN_RE='example\.com' \
    RUNTM_PREVIEW_JWKS_URL=https://example.com/jwks.json \
    RUNTM_APP_ORIGIN=https://example.com RUNTM_PROXY_CIDRS=10.0.0.0/8 \
    caddy adapt --adapter caddyfile --config /etc/caddy/Caddyfile > /dev/null

EXPOSE 8080
