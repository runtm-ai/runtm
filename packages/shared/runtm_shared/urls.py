"""URL construction utilities for Runtm deployments.

This module handles constructing deployment URLs, supporting:
- Default Fly.io URLs: <app>.fly.dev
- Custom domain URLs: <app>.runtm.com (when RUNTM_BASE_DOMAIN is set)
- Proxied URLs: <label>.apps.runtm.com (when RUNTM_DEPLOYMENT_PROXY_DOMAIN is set)

RUNTM_DEPLOYMENT_PROXY_DOMAIN is the switch for *private* deployments. Setting
it means an authenticating reverse proxy fronts every deployment, so the app
itself must not be reachable any other way: the deploy path allocates no public
IPs, publishes no public DNS record, and reports the proxied URL as the
deployment's URL. Leaving it unset keeps the historical public behaviour, which
is what a self-hosted Runtm without such a proxy needs.
"""

from __future__ import annotations

import os

APP_NAME_PREFIX = "runtm-"


def get_base_domain() -> str:
    """Get the configured base domain for deployments.

    Returns:
        Base domain (e.g., "runtm.com") or empty string for default fly.dev
    """
    return os.environ.get("RUNTM_BASE_DOMAIN", "")


def get_deployment_proxy_domain() -> str:
    """Domain of the authenticating proxy that fronts deployments.

    Returns:
        Proxy domain (e.g., "apps.runtm.com"), or empty string when
        deployments are served publicly.
    """
    return os.environ.get("RUNTM_DEPLOYMENT_PROXY_DOMAIN", "")


def deployments_are_private() -> bool:
    """Whether deployments are reachable only through the proxy.

    True when a proxy domain is configured. Every "do not expose this app"
    decision in the deploy path keys off this one function so the three
    mechanisms that make an app public — a public IP, a public DNS record, and
    a public URL handed back to the user — can never drift apart.
    """
    return bool(get_deployment_proxy_domain())


def deployment_label(app_name: str) -> str:
    """Proxy host label for a Fly app name: ``runtm-dep-abc123`` → ``dep-abc123``.

    The proxy restores the prefix to rebuild the app name, so this is the exact
    inverse of what it does (see proxy/Caddyfile in runtm-cloud).
    """
    if app_name.startswith(APP_NAME_PREFIX):
        return app_name[len(APP_NAME_PREFIX) :]
    return app_name


def construct_deployment_url(app_name: str, base_domain: str | None = None) -> str:
    """Construct the public URL for a deployment.

    Args:
        app_name: Fly.io app name (e.g., "runtm-abc123")
        base_domain: Override base domain (uses env var if not provided)

    Returns:
        Full HTTPS URL for the deployment

    Examples:
        >>> construct_deployment_url("runtm-abc123")
        "https://runtm-abc123.fly.dev"  # default

        >>> construct_deployment_url("runtm-abc123", "runtm.com")
        "https://runtm-abc123.runtm.com"  # custom domain

    With RUNTM_DEPLOYMENT_PROXY_DOMAIN=apps.runtm.com the app has no public
    address at all, so the proxied host is the only URL that resolves:

        >>> construct_deployment_url("runtm-dep-abc123")
        "https://dep-abc123.apps.runtm.com"
    """
    proxy_domain = get_deployment_proxy_domain()
    if proxy_domain:
        return f"https://{deployment_label(app_name)}.{proxy_domain}"

    if base_domain is None:
        base_domain = get_base_domain()

    if base_domain:
        return f"https://{app_name}.{base_domain}"
    else:
        return f"https://{app_name}.fly.dev"


def get_subdomain_for_app(app_name: str, base_domain: str | None = None) -> str | None:
    """Get the subdomain hostname for automatic certificate provisioning.

    Only returns a value when a custom base domain is configured.
    This subdomain should be added as a certificate to the Fly app.

    Args:
        app_name: Fly.io app name
        base_domain: Override base domain

    Returns:
        Subdomain hostname (e.g., "runtm-abc123.runtm.com"), or None when no
        base domain is set or deployments are private — a private deployment
        gets no public hostname, so there is nothing to certificate.
    """
    if deployments_are_private():
        return None

    if base_domain is None:
        base_domain = get_base_domain()

    if base_domain:
        return f"{app_name}.{base_domain}"
    return None
