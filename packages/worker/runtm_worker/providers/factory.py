"""Provider factory -- selects the deploy backend based on DEPLOY_PROVIDER."""

from __future__ import annotations

import os

from .base import DeployProvider


def get_provider(
    provider_name: str | None = None,
    **kwargs,
) -> DeployProvider:
    """Instantiate the appropriate deploy provider.

    Args:
        provider_name: ``"local"`` or ``"fly"``.  Defaults to the
            ``DEPLOY_PROVIDER`` env var, falling back to ``"local"``.
        **kwargs: Forwarded to the provider constructor
            (e.g. ``api_token`` for FlyProvider).

    Returns:
        A concrete ``DeployProvider`` instance.

    Raises:
        ValueError: Unknown provider name.
    """
    name = (provider_name or os.environ.get("DEPLOY_PROVIDER", "local")).lower()

    if name == "fly":
        from .fly import FlyProvider

        return FlyProvider(**kwargs)

    if name == "local":
        from .local import LocalProvider

        return LocalProvider(**kwargs)

    raise ValueError(
        f"Unknown DEPLOY_PROVIDER: {name!r}.  "
        "Supported values: 'local', 'fly'."
    )
