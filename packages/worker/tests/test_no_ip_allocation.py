"""Runtm must never allocate Fly IP addresses.

Deployed apps get their public IPs from `flyctl deploy`, which allocates
whatever the generated fly.toml's [http_service] needs. Runtm itself issues no
`allocateIpAddress` mutation on any path.

These assertions are made at the HTTP boundary rather than by mocking an
internal helper: the removed code lived in four different places, two of which
inlined the mutation instead of calling a shared function, so a test that
patched one helper would not notice the mutation coming back somewhere else.
Anything reaching Fly's GraphQL endpoint is recorded and inspected here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtm_shared.types import MachineConfig, MachineTier, ProviderResource
from runtm_worker.builder.docker import DockerBuilder
from runtm_worker.jobs.deploy import DeployJob
from runtm_worker.providers.fly import FlyProvider

APP_NAME = "runtm-dep-abc123de"
HOSTNAME = "app.example.com"
IPV6 = "2a09:8280:1::1:2345"


class RecordingGraphQL:
    """Stands in for `httpx.post`, recording every GraphQL body sent."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json") or {}
        self.bodies.append(body)
        query = body.get("query", "")

        # Dispatch on the query so the provider sees a plausible response and
        # runs to completion instead of bailing early down an error path.
        if "ipAddresses" in query:
            # IPv6-only app: the exact case the removed code used to "fix" by
            # allocating a free shared IPv4 on the fly.
            data = {
                "app": {
                    "ipAddresses": {"nodes": [{"address": IPV6, "type": "v6"}]},
                    "sharedIpAddress": None,
                }
            }
        elif "certificate" in query:
            data = {
                "app": {
                    "certificate": {
                        "id": "cert_1",
                        "hostname": HOSTNAME,
                        "configured": True,
                        "clientStatus": "Ready",
                        "issued": {"nodes": [{"type": "rsa", "expiresAt": "2027-01-01"}]},
                        "dnsValidationHostname": None,
                        "dnsValidationTarget": None,
                    }
                }
            }
        else:
            data = {}

        return MagicMock(status_code=200, json=lambda: {"data": data}, text="")

    @property
    def queries(self) -> list[str]:
        return [b.get("query", "") for b in self.bodies]

    def assert_no_allocation(self) -> None:
        offenders = [q for q in self.queries if "allocateipaddress" in q.lower()]
        assert not offenders, f"allocateIpAddress mutation was sent: {offenders}"


@pytest.fixture
def provider() -> FlyProvider:
    return FlyProvider(api_token="test-token", org="test-org")


@pytest.fixture
def graphql() -> RecordingGraphQL:
    recorder = RecordingGraphQL()
    with patch("runtm_worker.providers.fly.httpx.post", recorder):
        yield recorder


class TestAllocationHelperIsGone:
    def test_provider_has_no_allocate_helper(self) -> None:
        """The helper itself must not come back under its old name."""
        assert not hasattr(FlyProvider, "_allocate_ip_addresses")

    def test_deploy_job_helper_is_renamed(self) -> None:
        """`_ensure_fly_app_with_ips` promised IPs it no longer provisions."""
        assert not hasattr(DeployJob, "_ensure_fly_app_with_ips")
        assert hasattr(DeployJob, "_ensure_fly_app")


class TestEnsureFlyApp:
    def _job(self) -> DeployJob:
        return DeployJob(db=MagicMock(), storage=MagicMock(), fly_api_token="test-token")

    def test_creates_app_without_allocating(self) -> None:
        job = self._job()
        fake_provider = MagicMock(spec=FlyProvider)
        fake_provider._get_app.return_value = None

        created = job._ensure_fly_app(fake_provider, APP_NAME, MagicMock())

        assert created is True
        fake_provider._create_app.assert_called_once_with(APP_NAME)
        # spec=FlyProvider means touching a removed attribute raises, so this
        # fails loudly if the call is ever restored.
        assert not hasattr(fake_provider, "_allocate_ip_addresses")

    def test_existing_app_is_left_alone(self) -> None:
        job = self._job()
        fake_provider = MagicMock(spec=FlyProvider)
        fake_provider._get_app.return_value = {"name": APP_NAME}

        created = job._ensure_fly_app(fake_provider, APP_NAME, MagicMock())

        assert created is False
        fake_provider._create_app.assert_not_called()


class TestDeployPath:
    def test_deploy_issues_no_allocation(
        self, provider: FlyProvider, graphql: RecordingGraphQL
    ) -> None:
        """The direct Machines-API path creates the app and nothing else."""
        config = MachineConfig.from_tier(tier=MachineTier.STARTER, image="registry.fly.io/x:v1")

        with (
            patch.object(provider, "_get_app", return_value=None),
            patch.object(provider, "_create_app") as create_app,
            patch.object(provider, "_create_machine", return_value={"id": "m1", "region": "iad"}),
            patch.object(provider, "_wait_for_machine", return_value=True),
        ):
            result = provider.deploy("dep_abc123de", config)

        assert result.success
        create_app.assert_called_once()
        graphql.assert_no_allocation()


class TestCustomDomain:
    def _resource(self) -> ProviderResource:
        return ProviderResource(
            app_name=APP_NAME,
            machine_id="m1",
            region="iad",
            image_ref="registry.fly.io/x:v1",
            url=f"https://{APP_NAME}.fly.dev",
        )

    def test_add_custom_domain_on_ipv6_only_app_issues_no_allocation(
        self, provider: FlyProvider, graphql: RecordingGraphQL
    ) -> None:
        info = provider.add_custom_domain(self._resource(), HOSTNAME)

        graphql.assert_no_allocation()
        # An IPv6-only app now yields an AAAA record and no A record, instead
        # of an IPv4 being provisioned on demand to produce one.
        types = [r.record_type for r in info.dns_records]
        assert "AAAA" in types
        assert "A" not in types

    def test_domain_status_on_ipv6_only_app_issues_no_allocation(
        self, provider: FlyProvider, graphql: RecordingGraphQL
    ) -> None:
        info = provider.get_custom_domain_status(self._resource(), HOSTNAME)

        graphql.assert_no_allocation()
        assert "A" not in [r.record_type for r in info.dns_records]


class TestFlyTomlKeepsHttpService:
    """The invariant the whole removal rests on.

    `flyctl deploy` allocates IPs for an app whose config declares an
    [http_service]. Drop that section and deployed apps would silently stop
    being reachable, with nothing else in the codebase to catch it.
    """

    def test_generated_fly_toml_declares_http_service(self, tmp_path: Path) -> None:
        context = tmp_path / "context"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM alpine")

        builder = DockerBuilder(use_remote_builder=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Deployed", stderr="")
            builder.build_remote(
                context_path=context,
                app_name=APP_NAME,
                deployment_id="dep_abc123de",
                fly_api_token="test-token",
                internal_port=8080,
            )

        fly_toml = (context / "fly.toml").read_text()
        assert "[http_service]" in fly_toml
        assert "internal_port = 8080" in fly_toml
