"""Local secrets provider -- stores secrets in memory for container env injection."""

from __future__ import annotations

from .secrets_base import SecretListResult, SecretSetResult, SecretsProvider


class LocalSecretsProvider(SecretsProvider):
    """In-memory secrets provider for local deployments.

    Secrets are held in a dict and passed as container environment variables
    when `LocalProvider.deploy()` is called.  They are never written to
    disk or to any external service.

    Selected via `DEPLOY_PROVIDER=local` (the OSS default).
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    @property
    def name(self) -> str:
        return "local"

    def set_secrets(
        self,
        app_name: str,
        secrets: dict[str, str],
        stage: bool = False,  # noqa: ARG002
    ) -> SecretSetResult:
        existing = self._store.get(app_name, {})
        existing.update(secrets)
        self._store[app_name] = existing
        return SecretSetResult(success=True, secrets_set=len(secrets))

    def get_secret_names(self, app_name: str) -> SecretListResult:
        names = list(self._store.get(app_name, {}).keys())
        return SecretListResult(success=True, names=names)

    def delete_secrets(
        self,
        app_name: str,
        names: list[str],
    ) -> SecretSetResult:
        existing = self._store.get(app_name, {})
        removed = 0
        for n in names:
            if n in existing:
                del existing[n]
                removed += 1
        return SecretSetResult(success=True, secrets_set=removed)

    def get_secrets(self, app_name: str) -> dict[str, str]:
        """Return the full secret dict for an app (used by the local deploy path)."""
        return dict(self._store.get(app_name, {}))
