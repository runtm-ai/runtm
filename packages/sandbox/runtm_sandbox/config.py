"""Configuration generation for sandbox-runtime.

Generates JSON configuration files that sandbox-runtime (srt) uses
to configure bubblewrap/seatbelt isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from runtm_shared.types import SandboxConfig

logger = structlog.get_logger()


def generate_srt_config(config: SandboxConfig) -> dict:
    """Generate sandbox-runtime configuration.

    Creates a configuration dict that matches the sandbox-runtime
    expected format for filesystem and network isolation.

    Args:
        config: Sandbox configuration.

    Returns:
        Dict suitable for writing to sandbox-config.json.
    """
    # Filesystem configuration
    # srt expects: allowWrite, denyWrite, denyRead
    filesystem_config = {
        "allowWrite": config.guardrails.allow_write_paths.copy(),
        "denyWrite": config.guardrails.deny_write_paths.copy(),
        "denyRead": [],  # Required by srt
    }

    # Network configuration
    # srt expects: allowedDomains, deniedDomains (not allowDomains)
    if config.guardrails.network.enabled:
        network_config = {
            "allowedDomains": config.guardrails.network.allow_domains.copy(),
            "deniedDomains": [],  # Required by srt
        }
    else:
        network_config = {
            "allowedDomains": [],
            "deniedDomains": ["*"],  # Block all when network disabled
        }

    return {
        "filesystem": filesystem_config,
        "network": network_config,
        "allowPty": True,
    }


def write_config_file(srt_config: dict, config_path: Path) -> None:
    """Write sandbox-runtime config to a file.

    Args:
        srt_config: Configuration dict from generate_srt_config().
        config_path: Path to write the config file.
    """
    # Create parent directories if needed
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Write formatted JSON
    config_path.write_text(json.dumps(srt_config, indent=2))

    logger.debug("Wrote sandbox config", path=str(config_path))
