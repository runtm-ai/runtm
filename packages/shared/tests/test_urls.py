"""Deployment URL construction, public and proxied.

RUNTM_DEPLOYMENT_PROXY_DOMAIN is the single switch between the two modes, so
these tests pin what it changes: the URL a user is handed, and whether a public
hostname exists to be certificated at all.
"""

from __future__ import annotations

import pytest

from runtm_shared.urls import (
    construct_deployment_url,
    deployment_label,
    deployments_are_private,
    get_subdomain_for_app,
)

APP_NAME = "runtm-dep-abc123de"
PROXY_DOMAIN = "apps.runtm.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither variable leaks in from the developer's shell."""
    monkeypatch.delenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", raising=False)
    monkeypatch.delenv("RUNTM_BASE_DOMAIN", raising=False)


class TestPublicMode:
    def test_defaults_to_fly_dev(self) -> None:
        assert construct_deployment_url(APP_NAME) == f"https://{APP_NAME}.fly.dev"

    def test_base_domain_wins_over_fly_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "runtm.com")
        assert construct_deployment_url(APP_NAME) == f"https://{APP_NAME}.runtm.com"

    def test_subdomain_is_certificatable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "runtm.com")
        assert get_subdomain_for_app(APP_NAME) == f"{APP_NAME}.runtm.com"

    def test_not_private(self) -> None:
        assert deployments_are_private() is False


class TestPrivateMode:
    @pytest.fixture(autouse=True)
    def proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", PROXY_DOMAIN)

    def test_is_private(self) -> None:
        assert deployments_are_private() is True

    def test_url_is_the_proxied_host(self) -> None:
        assert construct_deployment_url(APP_NAME) == f"https://dep-abc123de.{PROXY_DOMAIN}"

    def test_proxy_domain_beats_base_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both set is the production config: the public host must not win."""
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "runtm.com")
        url = construct_deployment_url(APP_NAME)
        assert url == f"https://dep-abc123de.{PROXY_DOMAIN}"
        assert "runtm-dep-abc123de.runtm.com" not in url

    def test_no_public_subdomain_to_certificate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A cert would publish the hostname in CT logs and imply public access."""
        monkeypatch.setenv("RUNTM_BASE_DOMAIN", "runtm.com")
        assert get_subdomain_for_app(APP_NAME) is None


class TestDeploymentLabel:
    def test_strips_the_app_prefix(self) -> None:
        assert deployment_label(APP_NAME) == "dep-abc123de"

    def test_passes_through_an_unprefixed_name(self) -> None:
        assert deployment_label("someapp") == "someapp"
