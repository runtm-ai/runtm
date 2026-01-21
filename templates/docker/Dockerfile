# Docker Template - Bring Your Own Dockerfile
# Replace this with your own Dockerfile
#
# Requirements:
# 1. EXPOSE the port specified in runtm.yaml (default: 8080)
# 2. Implement a health endpoint at health_path (default: /health) that returns HTTP 200
# 3. Start within 10 seconds

# Example: Go application
# FROM golang:1.21-alpine AS builder
# WORKDIR /app
# COPY go.mod go.sum ./
# RUN go mod download
# COPY . .
# RUN CGO_ENABLED=0 go build -o main .
#
# FROM alpine:latest
# WORKDIR /app
# COPY --from=builder /app/main .
# EXPOSE 8080
# CMD ["./main"]

# Example: Rust application
# FROM rust:1.75-slim AS builder
# WORKDIR /app
# COPY Cargo.toml Cargo.lock ./
# COPY src ./src
# RUN cargo build --release
#
# FROM debian:bookworm-slim
# COPY --from=builder /app/target/release/myapp /usr/local/bin/
# EXPOSE 8080
# CMD ["myapp"]

# Example: Elixir/Phoenix application
# FROM elixir:1.15-alpine AS builder
# ENV MIX_ENV=prod
# WORKDIR /app
# COPY mix.exs mix.lock ./
# RUN mix deps.get --only prod
# COPY . .
# RUN mix release
#
# FROM alpine:latest
# COPY --from=builder /app/_build/prod/rel/myapp ./
# EXPOSE 8080
# CMD ["bin/myapp", "start"]

# Placeholder - replace with your Dockerfile
FROM alpine:latest

WORKDIR /app

# Create a simple health endpoint for demonstration
# Replace this with your actual application
RUN echo '#!/bin/sh' > /app/server.sh && \
    echo 'while true; do' >> /app/server.sh && \
    echo '  echo -e "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK" | nc -l -p 8080 -q 1' >> /app/server.sh && \
    echo 'done' >> /app/server.sh && \
    chmod +x /app/server.sh

RUN apk add --no-cache netcat-openbsd

EXPOSE 8080

CMD ["/bin/sh", "/app/server.sh"]
