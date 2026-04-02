"""Secrets provider factory -- selects backend based on DEPLOY_PROVIDER."""

from __future__ import annotations

import os

from .secrets_base import SecretsProvider


def get_secrets_provider(
    provider_name: str | None = None,
    **kwargs,
) -> SecretsProvider:
    """Instantiate the appropriate secrets provider.

    Args:
        provider_name: `"local"` or `"fly"`.  Defaults to the
            `DEPLOY_PROVIDER` env var, falling back to `"local"`.
        **kwargs: Forwarded to the provider constructor.

    Returns:
        A concrete `SecretsProvider` instance.

    Raises:
        ValueError: Unknown provider name.
    """
    name = (provider_name or os.environ.get("DEPLOY_PROVIDER", "local")).lower()

    if name == "fly":
        from .fly_secrets import FlySecretsProvider

        return FlySecretsProvider(**kwargs)

    if name == "local":
        from .local_secrets import LocalSecretsProvider

        return LocalSecretsProvider(**kwargs)

    raise ValueError(f"Unknown secrets provider: {name!r}.  Supported values: 'local', 'fly'.")
