"""Trusted proxy middleware and utilities.

SECURITY: This module provides centralized logic for trusting forwarded headers.
Only requests from trusted proxy IPs will have X-Forwarded-* headers honored.

This prevents:
- IP spoofing via X-Forwarded-For from direct connections
- TLS spoofing via X-Forwarded-Proto from untrusted sources
- Rate limit bypass via header manipulation

Usage:
    # Get client IP (only trusts headers from proxies)
    ip = get_client_ip(request, settings)

    # Check TLS status (only trusts X-Forwarded-Proto from proxies)
    scheme = get_request_scheme(request, settings)

    # Middleware for TLS enforcement
    app.add_middleware(TLSEnforcementMiddleware, settings=settings)
"""

from __future__ import annotations

import ipaddress
from typing import Union

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from runtm_api.core.config import Settings

# Type alias for network types
NetworkType = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def parse_trusted_proxies(trusted_proxies: str) -> list[NetworkType]:
    """Parse trusted proxy config into network objects.

    Supports both single IPs and CIDR notation:
    - "127.0.0.1" -> single IP
    - "10.0.0.0/8" -> network range

    Args:
        trusted_proxies: Comma-separated list of IPs/CIDRs

    Returns:
        List of network objects for membership testing
    """
    networks: list[NetworkType] = []
    for entry in trusted_proxies.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            # Try as network (CIDR notation)
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            try:
                # Try as single IP
                ip = ipaddress.ip_address(entry)
                # Convert to /32 or /128 network
                prefix = 32 if ip.version == 4 else 128
                networks.append(ipaddress.ip_network(f"{ip}/{prefix}"))
            except ValueError:
                pass  # Skip invalid entries
    return networks


def is_trusted_proxy(client_ip: str, trusted_networks: list[NetworkType]) -> bool:
    """Check if client IP is a trusted proxy.

    Args:
        client_ip: IP address to check
        trusted_networks: List of trusted network objects

    Returns:
        True if the IP is in a trusted network
    """
    try:
        ip = ipaddress.ip_address(client_ip)
        return any(ip in network for network in trusted_networks)
    except ValueError:
        return False


def get_client_ip(request: Request, settings: Settings) -> str:
    """Extract real client IP, only trusting headers from known proxies.

    SECURITY: Forwarded headers (X-Forwarded-For, X-Real-IP, Fly-Client-IP)
    are only trusted if the direct connection IP is in the trusted_proxies list.

    This prevents attackers from spoofing their IP by setting X-Forwarded-For
    when connecting directly to the API.

    Args:
        request: FastAPI request
        settings: Application settings with trusted_proxies config

    Returns:
        Client IP address (real IP if from trusted proxy, direct IP otherwise)
    """
    direct_ip = request.client.host if request.client else "unknown"

    trusted_networks = parse_trusted_proxies(settings.trusted_proxies)

    if not is_trusted_proxy(direct_ip, trusted_networks):
        # Direct connection from untrusted source - don't trust any headers
        return direct_ip

    # Request came from trusted proxy - check forwarded headers
    # Priority: Fly-Client-IP (Fly.io specific) > X-Forwarded-For > X-Real-IP
    fly_ip = request.headers.get("Fly-Client-IP")
    if fly_ip:
        return fly_ip.strip()

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        # First IP in chain is the original client
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    return direct_ip


def get_request_scheme(request: Request, settings: Settings) -> str:
    """Get request scheme, only trusting headers from known proxies.

    SECURITY: X-Forwarded-Proto is only trusted if the request came from
    a trusted proxy. This prevents attackers from bypassing TLS requirements
    by spoofing the header when connecting directly.

    Args:
        request: FastAPI request
        settings: Application settings with trusted_proxies config

    Returns:
        "http" or "https" based on actual connection or trusted proxy header
    """
    direct_ip = request.client.host if request.client else "unknown"

    trusted_networks = parse_trusted_proxies(settings.trusted_proxies)

    if not is_trusted_proxy(direct_ip, trusted_networks):
        # Untrusted source - use actual scheme, don't trust headers
        return str(request.url.scheme)

    # Trusted proxy - check forwarded proto
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    if proto in ("http", "https"):
        return proto

    return str(request.url.scheme)


class TLSEnforcementMiddleware(BaseHTTPMiddleware):
    """Reject non-HTTPS requests in production.

    SECURITY: Only trusts X-Forwarded-Proto from trusted proxies.
    This prevents attackers from bypassing TLS requirements by
    connecting directly and setting X-Forwarded-Proto: https.

    Usage:
        settings = get_settings()
        if settings.require_tls and not settings.debug:
            app.add_middleware(TLSEnforcementMiddleware, settings=settings)
    """

    def __init__(self, app, settings: Settings):
        """Initialize TLS enforcement middleware.

        Args:
            app: Starlette/FastAPI application
            settings: Application settings
        """
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        """Check TLS and reject non-HTTPS requests.

        Args:
            request: Incoming request
            call_next: Next middleware/route handler

        Returns:
            Response (403 if not HTTPS, otherwise route response)
        """
        # Skip health checks (needed for load balancer probes and monitoring)
        if request.url.path in ("/health", "/health/detailed", "/healthz", "/ready"):
            return await call_next(request)

        scheme = get_request_scheme(request, self.settings)

        if scheme != "https":
            return JSONResponse(
                status_code=403,
                content={
                    "error": "HTTPS required",
                    "hint": "This API requires TLS. Use https:// or check proxy configuration.",
                },
            )

        return await call_next(request)
