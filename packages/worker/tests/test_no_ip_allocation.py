"""Runtm must never allocate a *public* Fly IP address.

On the public path, deployed apps get their IPs from `flyctl deploy`, which
allocates whatever the generated fly.toml's [http_service] needs. Runtm issues
no `allocateIpAddress` mutation of a public type on any path.

The one allocation Runtm does make is `private_v6` — the Flycast address that
makes an app with no public IP reachable from the proxy. It is asserted
separately below, and the guard here is typed rather than blanket so that
"allocate an address" cannot quietly come back as a public one.

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
FLYCAST = "fdaa:3b:fa0:0:1::295"
PROXY_DOMAIN = "apps.example.com"

# Every public address type Fly can hand out. A private_v6 allocation is
# expected; anything in this set is the regression.
PUBLIC_IP_TYPES = ("shared_v4", "v4", "v6")


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
        if "allocateIpAddress" in query:
            data = {
                "allocateIpAddress": {
                    "ipAddress": {"address": FLYCAST, "type": "private_v6"},
                }
            }
        elif "ipAddresses" in query:
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

    @property
    def allocations(self) -> list[dict[str, Any]]:
        """The `input` of every allocateIpAddress mutation sent."""
        return [
            b.get("variables", {}).get("input", {})
            for b in self.bodies
            if "allocateipaddress" in b.get("query", "").lower()
        ]

    def assert_no_allocation(self) -> None:
        offenders = [q for q in self.queries if "allocateipaddress" in q.lower()]
        assert not offenders, f"allocateIpAddress mutation was sent: {offenders}"

    def assert_no_public_allocation(self) -> None:
        offenders = [a for a in self.allocations if a.get("type") in PUBLIC_IP_TYPES]
        assert not offenders, f"public IP was allocated: {offenders}"


@pytest.fixture
def provider() -> FlyProvider:
    return FlyProvider(api_token="test-token", org="test-org")


@pytest.fixture
def graphql() -> RecordingGraphQL:
    recorder = RecordingGraphQL()
    with patch("runtm_worker.providers.fly.httpx.post", recorder):
        yield recorder


@pytest.fixture
def private(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployments served only through the proxy."""
    monkeypatch.setenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", PROXY_DOMAIN)


@pytest.fixture
def public(monkeypatch: pytest.MonkeyPatch) -> None:
    """The historical behaviour: deployments on the public internet."""
    monkeypatch.delenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", raising=False)


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


def _run_remote_build(tmp_path: Path) -> list[str]:
    """Drive build_remote and return the flyctl argv it invoked."""
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
    return mock_run.call_args[0][0]


class TestPrivateDeployments:
    """With a proxy domain set, nothing about the app may be public.

    Three separate mechanisms used to expose a deployment, and removing any two
    of them still leaves it reachable, so each gets its own assertion.
    """

    def _job(self) -> DeployJob:
        return DeployJob(db=MagicMock(), storage=MagicMock(), fly_api_token="test-token")

    def test_deploy_passes_no_public_ips(self, private: None, tmp_path: Path) -> None:
        """Mechanism 1: flyctl's own allocation for [http_service]."""
        assert "--no-public-ips" in _run_remote_build(tmp_path)

    def test_public_deploy_keeps_allocating(self, public: None, tmp_path: Path) -> None:
        """Unset means unchanged — a self-hosted Runtm has no proxy to hide behind."""
        assert "--no-public-ips" not in _run_remote_build(tmp_path)

    def test_public_dns_is_skipped(self, private: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mechanism 2: the {app}.{base_domain} CNAME and its certificate."""
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "example.com")
        job = self._job()
        provider = MagicMock(spec=FlyProvider)

        job._provision_custom_subdomain(provider, APP_NAME, MagicMock())

        provider.add_custom_domain.assert_not_called()

    def test_public_dns_still_provisioned_when_public(
        self, public: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "example.com")
        job = self._job()
        provider = MagicMock(spec=FlyProvider)
        provider.add_custom_domain.return_value = MagicMock(
            dns_records=[], certificate_status="ready"
        )

        # Stop at the certificate step; the DNS half needs a configured
        # Settings object and is not what this test is about.
        with patch.object(job, "_get_dns_provider", return_value=None):
            job._provision_custom_subdomain(provider, APP_NAME, MagicMock())

        provider.add_custom_domain.assert_called_once()

    def test_ensure_fly_app_allocates_flycast(self, private: None) -> None:
        job = self._job()
        provider = MagicMock(spec=FlyProvider)
        provider._get_app.return_value = None
        provider.ensure_private_ipv6.return_value = FLYCAST

        job._ensure_fly_app(provider, APP_NAME, MagicMock())

        provider.ensure_private_ipv6.assert_called_once_with(APP_NAME)

    def test_existing_app_gets_flycast_backfilled(self, private: None) -> None:
        """An app first deployed before this change has no Flycast address."""
        job = self._job()
        provider = MagicMock(spec=FlyProvider)
        provider._get_app.return_value = {"name": APP_NAME}
        provider.ensure_private_ipv6.return_value = FLYCAST

        created = job._ensure_fly_app(provider, APP_NAME, MagicMock())

        assert created is False
        provider._create_app.assert_not_called()
        provider.ensure_private_ipv6.assert_called_once_with(APP_NAME)

    def test_no_flycast_when_public(self, public: None) -> None:
        job = self._job()
        provider = MagicMock(spec=FlyProvider)
        provider._get_app.return_value = None

        job._ensure_fly_app(provider, APP_NAME, MagicMock())

        provider.ensure_private_ipv6.assert_not_called()

    def test_deploy_survives_a_failed_allocation(self, private: None) -> None:
        """Unreachable beats undeployed: a failed allocation must not raise."""
        job = self._job()
        provider = MagicMock(spec=FlyProvider)
        provider._get_app.return_value = None
        provider.ensure_private_ipv6.return_value = None

        assert job._ensure_fly_app(provider, APP_NAME, MagicMock()) is True


class TestFlycastAllocation:
    """Mechanism 3 in reverse: the only address Runtm hands out is private."""

    def test_allocation_is_private_typed(
        self, provider: FlyProvider, graphql: RecordingGraphQL
    ) -> None:
        address = provider.ensure_private_ipv6(APP_NAME)

        assert address == FLYCAST
        graphql.assert_no_public_allocation()
        assert [a.get("type") for a in graphql.allocations] == ["private_v6"]

    def test_allocation_is_idempotent(self, provider: FlyProvider) -> None:
        """A redeploy must not stack up a second Flycast address."""
        with patch.object(
            provider,
            "_get_app_ips",
            return_value=[{"address": FLYCAST, "type": "private_v6"}],
        ):
            recorder = RecordingGraphQL()
            with patch("runtm_worker.providers.fly.httpx.post", recorder):
                address = provider.ensure_private_ipv6(APP_NAME)

        assert address == FLYCAST
        assert recorder.allocations == []

    def test_a_public_only_app_still_gets_flycast(
        self, provider: FlyProvider, graphql: RecordingGraphQL
    ) -> None:
        """The recorded app has a public v6 and no private one — allocate."""
        assert provider.ensure_private_ipv6(APP_NAME) == FLYCAST
        assert len(graphql.allocations) == 1

    def test_allocation_failure_is_not_fatal(self, provider: FlyProvider) -> None:
        def explode(url: str, **kwargs: Any) -> MagicMock:
            return MagicMock(status_code=500, json=lambda: {}, text="boom")

        with (
            patch.object(provider, "_get_app_ips", return_value=[]),
            patch("runtm_worker.providers.fly.httpx.post", explode),
        ):
            assert provider.ensure_private_ipv6(APP_NAME) is None
