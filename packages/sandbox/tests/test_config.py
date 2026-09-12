"""Tests for sandbox-runtime configuration generation."""

from __future__ import annotations

from runtm_shared.types import (
    GuardrailsConfig,
    NetworkConfig,
    SandboxConfig,
)


class TestGenerateSrtConfig:
    """Tests for generating sandbox-runtime configuration."""

    def test_generates_valid_config_structure(self) -> None:
        """Should generate config with filesystem and network sections."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig()
        srt_config = generate_srt_config(config)

        assert "filesystem" in srt_config
        assert "network" in srt_config

    def test_default_config_allows_current_dir_writes(self) -> None:
        """Should allow writes to current directory by default."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig()
        srt_config = generate_srt_config(config)

        assert "." in srt_config["filesystem"]["allowWrite"]

    def test_includes_default_network_allowlist(self) -> None:
        """Should include default allowed domains."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig()
        srt_config = generate_srt_config(config)

        # Should have the default domains
        domains = srt_config["network"]["allowDomains"]
        assert "pypi.org" in domains
        assert "github.com" in domains
        assert "api.anthropic.com" in domains

    def test_network_disabled_returns_empty_allowlist(self) -> None:
        """Should return empty domain list when network is disabled."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig(
            guardrails=GuardrailsConfig(
                network=NetworkConfig(enabled=False),
            )
        )
        srt_config = generate_srt_config(config)

        assert srt_config["network"]["allowDomains"] == []

    def test_custom_write_paths(self) -> None:
        """Should use custom write paths when specified."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig(
            guardrails=GuardrailsConfig(
                allow_write_paths=[".", "output/", "tmp/"],
            )
        )
        srt_config = generate_srt_config(config)

        assert "output/" in srt_config["filesystem"]["allowWrite"]
        assert "tmp/" in srt_config["filesystem"]["allowWrite"]

    def test_deny_write_paths(self) -> None:
        """Should include deny write paths in config."""
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig(
            guardrails=GuardrailsConfig(
                deny_write_paths=["secrets/", ".env"],
            )
        )
        srt_config = generate_srt_config(config)

        assert "secrets/" in srt_config["filesystem"]["denyWrite"]
        assert ".env" in srt_config["filesystem"]["denyWrite"]

    def test_enables_pty_support(self) -> None:
        """Should allow pty operations so interactive TUIs can use raw mode.

        Without allowPty, the macOS seatbelt profile denies the tty control
        ioctls and Node/ink TUIs (including Claude Code) fail setRawMode
        with EPERM, leaving terminal echo on.
        """
        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig()
        srt_config = generate_srt_config(config)

        assert srt_config["allowPty"] is True

    def test_custom_network_allowlist(self) -> None:
        """Should use custom domain allowlist when specified."""
        from runtm_sandbox.config import generate_srt_config

        custom_domains = ["internal.company.com", "api.custom.dev"]
        config = SandboxConfig(
            guardrails=GuardrailsConfig(
                network=NetworkConfig(
                    enabled=True,
                    allow_domains=custom_domains,
                ),
            )
        )
        srt_config = generate_srt_config(config)

        assert srt_config["network"]["allowDomains"] == custom_domains


class TestConfigSerialization:
    """Tests for config serialization."""

    def test_config_is_json_serializable(self) -> None:
        """Generated config should be JSON serializable."""
        import json

        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig()
        srt_config = generate_srt_config(config)

        # Should not raise
        json_str = json.dumps(srt_config)
        assert json_str is not None

    def test_config_roundtrips_through_json(self) -> None:
        """Config should survive JSON serialization/deserialization."""
        import json

        from runtm_sandbox.config import generate_srt_config

        config = SandboxConfig(
            guardrails=GuardrailsConfig(
                allow_write_paths=[".", "build/"],
                deny_write_paths=[".git/"],
                network=NetworkConfig(
                    enabled=True,
                    allow_domains=["example.com"],
                ),
            )
        )

        srt_config = generate_srt_config(config)
        json_str = json.dumps(srt_config)
        loaded = json.loads(json_str)

        assert loaded["filesystem"]["allowWrite"] == [".", "build/"]
        assert loaded["filesystem"]["denyWrite"] == [".git/"]
        assert loaded["network"]["allowDomains"] == ["example.com"]


class TestConfigWriteToFile:
    """Tests for writing config to file."""

    def test_write_config_to_file(self, tmp_path) -> None:
        """Should write config to a file correctly."""
        from runtm_sandbox.config import generate_srt_config, write_config_file

        config = SandboxConfig()
        srt_config = generate_srt_config(config)
        config_path = tmp_path / "sandbox-config.json"

        write_config_file(srt_config, config_path)

        assert config_path.exists()
        import json

        loaded = json.loads(config_path.read_text())
        assert "filesystem" in loaded
        assert "network" in loaded

    def test_write_config_creates_parent_dirs(self, tmp_path) -> None:
        """Should create parent directories if they don't exist."""
        from runtm_sandbox.config import generate_srt_config, write_config_file

        config = SandboxConfig()
        srt_config = generate_srt_config(config)
        config_path = tmp_path / "nested" / "dir" / "sandbox-config.json"

        write_config_file(srt_config, config_path)

        assert config_path.exists()
