"""Unit tests for LocalProvider.

These tests mock the Docker client so they run without a Docker daemon.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from runtm_shared.types import CustomDomainInfo, MachineConfig, ProviderResource
from runtm_worker.providers.local import LocalProvider


@pytest.fixture()
def mock_docker():
    """Patch docker.from_env and return the mock client."""
    with patch("runtm_worker.providers.local.docker") as docker_mod:
        client = MagicMock()
        docker_mod.from_env.return_value = client
        # Simulate network already exists
        client.networks.get.return_value = MagicMock()
        yield client


@pytest.fixture()
def provider(mock_docker):
    return LocalProvider()


def _resource(app="dep-abc123", cid="c123") -> ProviderResource:
    return ProviderResource(
        app_name=app,
        machine_id=cid,
        region="local",
        image_ref="test-image:latest",
        url="http://localhost:9000",
    )


def _config() -> MachineConfig:
    return MachineConfig(
        image="test-image:latest",
        memory_mb=256,
        cpus=1,
    )


# ── name ─────────────────────────────────────────────────────────────


class TestProviderName:
    def test_name_is_local(self, provider):
        assert provider.name == "local"


# ── deploy ───────────────────────────────────────────────────────────


class TestDeploy:
    def test_deploy_success(self, provider, mock_docker):
        container = MagicMock()
        container.short_id = "abc123"
        container.id = "full-container-id"
        mock_docker.containers.run.return_value = container
        mock_docker.containers.list.return_value = []

        with patch.object(LocalProvider, "_probe_health", return_value=True):
            result = provider.deploy("dep-1", _config())

        assert result.success is True
        assert result.resource is not None
        assert result.resource.app_name == "dep-1"
        assert result.resource.region == "local"
        assert "localhost" in result.resource.url

    def test_deploy_pulls_missing_image(self, provider, mock_docker):
        from docker.errors import ImageNotFound

        mock_docker.images.get.side_effect = ImageNotFound("nope")
        container = MagicMock(short_id="x", id="xx")
        mock_docker.containers.run.return_value = container
        mock_docker.containers.list.return_value = []

        with patch.object(LocalProvider, "_probe_health", return_value=True):
            result = provider.deploy("dep-2", _config())

        mock_docker.images.pull.assert_called_once_with("test-image:latest")
        assert result.success is True

    def test_deploy_returns_failure_on_error(self, provider, mock_docker):
        mock_docker.containers.list.return_value = []
        mock_docker.containers.run.side_effect = RuntimeError("boom")

        result = provider.deploy("dep-3", _config())

        assert result.success is False
        assert "boom" in (result.error or "")


# ── get_status ───────────────────────────────────────────────────────


class TestGetStatus:
    def test_running(self, provider, mock_docker):
        container = MagicMock()
        container.status = "running"
        mock_docker.containers.get.return_value = container

        status = provider.get_status(_resource())

        assert status.state == "running"
        assert status.healthy is True

    def test_not_found(self, provider, mock_docker):
        from docker.errors import NotFound

        mock_docker.containers.get.side_effect = NotFound("gone")

        status = provider.get_status(_resource())

        assert status.state == "stopped"
        assert status.healthy is False


# ── destroy ──────────────────────────────────────────────────────────


class TestDestroy:
    def test_destroy_success(self, provider, mock_docker):
        container = MagicMock()
        mock_docker.containers.get.return_value = container

        assert provider.destroy(_resource()) is True
        container.stop.assert_called_once()
        container.remove.assert_called_once_with(force=True)

    def test_destroy_already_gone(self, provider, mock_docker):
        from docker.errors import NotFound

        mock_docker.containers.get.side_effect = NotFound("gone")

        assert provider.destroy(_resource()) is True


# ── get_logs ─────────────────────────────────────────────────────────


class TestGetLogs:
    def test_returns_logs(self, provider, mock_docker):
        container = MagicMock()
        container.logs.return_value = b"line1\nline2\n"
        mock_docker.containers.get.return_value = container

        output = provider.get_logs(_resource(), lines=50)

        assert "line1" in output
        container.logs.assert_called_once_with(tail=50)


# ── custom domains (stubs) ───────────────────────────────────────────


class TestCustomDomainStubs:
    def test_add_returns_not_supported(self, provider):
        info = provider.add_custom_domain(_resource(), "example.com")
        assert isinstance(info, CustomDomainInfo)
        assert info.certificate_status == "not_supported"

    def test_status_returns_not_supported(self, provider):
        info = provider.get_custom_domain_status(_resource(), "example.com")
        assert info.certificate_status == "not_supported"

    def test_remove_returns_true(self, provider):
        assert provider.remove_custom_domain(_resource(), "example.com") is True


# ── factory ──────────────────────────────────────────────────────────


class TestFactory:
    def test_local_default(self, mock_docker):
        from runtm_worker.providers.factory import get_provider

        with patch.dict("os.environ", {"DEPLOY_PROVIDER": "local"}):
            p = get_provider()
        assert p.name == "local"

    def test_fly_raises_without_token(self):
        from runtm_worker.providers.factory import get_provider

        with (
            patch.dict("os.environ", {"DEPLOY_PROVIDER": "fly"}, clear=False),
            pytest.raises(Exception),
        ):
            get_provider()

    def test_unknown_raises(self):
        from runtm_worker.providers.factory import get_provider

        with pytest.raises(ValueError, match="Unknown"):
            get_provider(provider_name="nope")
