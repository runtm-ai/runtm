"""Tests for volume support in the remote builder path."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from runtm_shared.types import VolumeConfig
from runtm_worker.builder.docker import DockerBuilder


class TestFlyTomlVolumeGeneration:
    """Verify that fly.toml includes [[mounts]] when volumes are provided."""

    def _build_remote_with_volumes(
        self,
        tmp_path: Path,
        volumes: list[VolumeConfig] | None = None,
    ) -> tuple[str, list[str]]:
        """Call build_remote and return (fly_toml_content, logs).

        build_remote will fail at flyctl execution, but we only
        care about the fly.toml it generates before that point.
        """
        context = tmp_path / "context"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM alpine")

        builder = DockerBuilder(use_remote_builder=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Deployed successfully",
                stderr="",
            )
            with patch.object(builder, "_ensure_fly_volumes") as mock_ensure:
                builder.build_remote(
                    context_path=context,
                    app_name="test-app",
                    deployment_id="dep_abc123test",
                    fly_api_token="test-token",
                    volumes=volumes,
                )

        fly_toml = (context / "fly.toml").read_text()
        return fly_toml, mock_ensure, mock_run

    def test_no_volumes_no_mounts_section(self, tmp_path: Path) -> None:
        fly_toml, mock_ensure, _ = self._build_remote_with_volumes(tmp_path, volumes=None)
        assert "[[mounts]]" not in fly_toml
        mock_ensure.assert_not_called()

    def test_empty_volumes_no_mounts_section(self, tmp_path: Path) -> None:
        fly_toml, mock_ensure, _ = self._build_remote_with_volumes(tmp_path, volumes=[])
        assert "[[mounts]]" not in fly_toml
        mock_ensure.assert_not_called()

    def test_single_volume_generates_mount(self, tmp_path: Path) -> None:
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]
        fly_toml, mock_ensure, _ = self._build_remote_with_volumes(tmp_path, volumes=vols)
        assert "[[mounts]]" in fly_toml
        assert 'source = "data"' in fly_toml
        assert 'destination = "/data"' in fly_toml
        mock_ensure.assert_called_once()

    def test_multiple_volumes_generate_mounts(self, tmp_path: Path) -> None:
        vols = [
            VolumeConfig(name="data", path="/data", size_gb=1),
            VolumeConfig(name="cache", path="/cache", size_gb=5),
        ]
        fly_toml, mock_ensure, _ = self._build_remote_with_volumes(tmp_path, volumes=vols)
        assert fly_toml.count("[[mounts]]") == 2
        assert 'source = "data"' in fly_toml
        assert 'destination = "/data"' in fly_toml
        assert 'source = "cache"' in fly_toml
        assert 'destination = "/cache"' in fly_toml

    def test_volumes_passed_to_ensure(self, tmp_path: Path) -> None:
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]
        _, mock_ensure, _ = self._build_remote_with_volumes(tmp_path, volumes=vols)
        call_kwargs = mock_ensure.call_args
        assert call_kwargs[1]["app_name"] == "test-app"
        assert call_kwargs[1]["volumes"] == vols
        assert call_kwargs[1]["fly_api_token"] == "test-token"


class TestEnsureFlyVolumes:
    """Test _ensure_fly_volumes creates volumes via Machines API."""

    def test_creates_new_volume(self) -> None:
        builder = DockerBuilder(use_remote_builder=True)
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]
        logs: list[str] = []

        with patch("httpx.get") as mock_get, patch("httpx.post") as mock_post:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=[]),
            )
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"id": "vol_123"}),
            )

            builder._ensure_fly_volumes(
                app_name="test-app",
                volumes=vols,
                fly_api_token="tok",
                region="iad",
                logs=logs,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["name"] == "data"
        assert call_kwargs[1]["json"]["size_gb"] == 1
        assert call_kwargs[1]["json"]["region"] == "iad"
        assert any("Created volume" in l for l in logs)

    def test_skips_existing_volume(self) -> None:
        builder = DockerBuilder(use_remote_builder=True)
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]
        logs: list[str] = []

        with patch("httpx.get") as mock_get, patch("httpx.post") as mock_post:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value=[{"name": "data", "region": "iad", "id": "vol_existing"}]
                ),
            )

            builder._ensure_fly_volumes(
                app_name="test-app",
                volumes=vols,
                fly_api_token="tok",
                region="iad",
                logs=logs,
            )

        mock_post.assert_not_called()
        assert any("already exists" in l for l in logs)

    def test_handles_api_error_gracefully(self) -> None:
        builder = DockerBuilder(use_remote_builder=True)
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]
        logs: list[str] = []

        with patch("httpx.get") as mock_get, patch("httpx.post") as mock_post:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=[]),
            )
            mock_post.return_value = MagicMock(
                status_code=500,
                text="Internal Server Error",
                json=MagicMock(return_value={"error": "volume creation failed"}),
            )

            builder._ensure_fly_volumes(
                app_name="test-app",
                volumes=vols,
                fly_api_token="tok",
                region="iad",
                logs=logs,
            )

        assert any("Warning" in l for l in logs)


class TestBuildAndPushVolumes:
    """Test that build_and_push forwards volumes to build_remote."""

    def test_volumes_forwarded(self, tmp_path: Path) -> None:
        import zipfile

        artifact = tmp_path / "artifact.zip"
        with zipfile.ZipFile(artifact, "w") as zf:
            zf.writestr("Dockerfile", "FROM alpine")

        builder = DockerBuilder(use_remote_builder=True)
        vols = [VolumeConfig(name="data", path="/data", size_gb=1)]

        with patch.object(builder, "build_remote") as mock_remote:
            mock_remote.return_value = MagicMock(
                success=True,
                image_tag="test:tag",
                image_label="tag",
                error=None,
                logs=[],
                deployed=True,
                url="https://test.fly.dev",
            )
            builder.build_and_push(
                artifact_path=artifact,
                image_name="test-app",
                deployment_id="dep_abc123test",
                fly_api_token="test-token",
                volumes=vols,
            )

        mock_remote.assert_called_once()
        assert mock_remote.call_args[1]["volumes"] == vols
