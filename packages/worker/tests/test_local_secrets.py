"""Tests for LocalSecretsProvider and get_secrets_provider factory."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from runtm_worker.providers.local_secrets import LocalSecretsProvider
from runtm_worker.providers.secrets_factory import get_secrets_provider


class TestLocalSecretsProvider:
    def test_name_is_local(self):
        provider = LocalSecretsProvider()
        assert provider.name == "local"

    def test_set_secrets_returns_success(self):
        provider = LocalSecretsProvider()
        result = provider.set_secrets("my-app", {"DB_URL": "postgres://...", "KEY": "val"})
        assert result.success is True
        assert result.secrets_set == 2

    def test_get_secret_names_returns_names_only(self):
        provider = LocalSecretsProvider()
        provider.set_secrets("app-1", {"SECRET_A": "val-a", "SECRET_B": "val-b"})
        result = provider.get_secret_names("app-1")
        assert result.success is True
        assert sorted(result.names) == ["SECRET_A", "SECRET_B"]

    def test_get_secret_names_empty_for_unknown_app(self):
        provider = LocalSecretsProvider()
        result = provider.get_secret_names("unknown")
        assert result.success is True
        assert result.names == []

    def test_delete_secrets_removes_keys(self):
        provider = LocalSecretsProvider()
        provider.set_secrets("app", {"A": "1", "B": "2", "C": "3"})
        result = provider.delete_secrets("app", ["A", "C"])
        assert result.success is True
        assert result.secrets_set == 2
        remaining = provider.get_secret_names("app")
        assert remaining.names == ["B"]

    def test_delete_nonexistent_key_is_noop(self):
        provider = LocalSecretsProvider()
        provider.set_secrets("app", {"X": "1"})
        result = provider.delete_secrets("app", ["MISSING"])
        assert result.success is True
        assert result.secrets_set == 0

    def test_get_secrets_returns_full_dict(self):
        provider = LocalSecretsProvider()
        provider.set_secrets("app", {"K": "V"})
        assert provider.get_secrets("app") == {"K": "V"}

    def test_get_secrets_returns_copy(self):
        provider = LocalSecretsProvider()
        provider.set_secrets("app", {"K": "V"})
        d = provider.get_secrets("app")
        d["K"] = "CHANGED"
        assert provider.get_secrets("app") == {"K": "V"}

    def test_stage_flag_is_accepted(self):
        provider = LocalSecretsProvider()
        result = provider.set_secrets("app", {"S": "1"}, stage=True)
        assert result.success is True


class TestSecretsFactory:
    def test_returns_local_by_default(self):
        with patch.dict("os.environ", {"DEPLOY_PROVIDER": "local"}):
            p = get_secrets_provider()
        assert p.name == "local"
        assert isinstance(p, LocalSecretsProvider)

    def test_returns_fly_when_configured(self):
        with (
            patch.dict("os.environ", {"DEPLOY_PROVIDER": "fly", "FLY_API_TOKEN": "test-token"}),
        ):
            p = get_secrets_provider()
        assert p.name == "fly"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            get_secrets_provider(provider_name="nope")
