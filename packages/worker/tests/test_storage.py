"""Tests for artifact storage implementations and factory."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtm_shared.errors import ArtifactNotFoundError, StorageReadError, StorageWriteError
from runtm_worker.storage.local import LocalFileStore


class TestLocalFileStore:
    """Tests for LocalFileStore (baseline — must keep working after refactor)."""

    def setup_method(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.store = LocalFileStore(self.tmpdir)

    def test_put_and_get(self) -> None:
        data = b"hello artifact"
        self.store.put("dep_abc/artifact.zip", data)
        assert self.store.get("dep_abc/artifact.zip") == data

    def test_exists_true(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        assert self.store.exists("dep_abc/artifact.zip") is True

    def test_exists_false(self) -> None:
        assert self.store.exists("nonexistent/artifact.zip") is False

    def test_get_missing_raises(self) -> None:
        with pytest.raises(ArtifactNotFoundError):
            self.store.get("nonexistent/artifact.zip")

    def test_delete(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        self.store.delete("dep_abc/artifact.zip")
        assert self.store.exists("dep_abc/artifact.zip") is False

    def test_get_uri(self) -> None:
        uri = self.store.get_uri("dep_abc/artifact.zip")
        assert uri.startswith("file://")

    def test_get_path(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        path = self.store.get_path("dep_abc/artifact.zip")
        assert isinstance(path, Path)
        assert path.exists()

    def test_get_size(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"12345")
        assert self.store.get_size("dep_abc/artifact.zip") == 5

    def test_get_size_missing(self) -> None:
        assert self.store.get_size("nonexistent") is None

    def test_path_traversal_blocked(self) -> None:
        with pytest.raises(StorageWriteError):
            self.store.put("../../etc/passwd", b"evil")


class TestS3FileStore:
    """Tests for S3FileStore using mocked boto3."""

    def _make_store(self):
        from runtm_worker.storage.s3 import S3FileStore

        return S3FileStore(
            bucket="test-bucket",
            endpoint_url="https://fly.storage.tigris.dev",
            region="auto",
        )

    @patch("runtm_worker.storage.s3.boto3")
    def test_put(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        store = self._make_store()
        uri = store.put("artifacts/dep_abc/artifact.zip", b"hello")

        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="artifacts/dep_abc/artifact.zip",
            Body=b"hello",
        )
        assert "s3://" in uri

    @patch("runtm_worker.storage.s3.boto3")
    def test_get(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_body = MagicMock()
        mock_body.read.return_value = b"artifact-data"
        mock_client.get_object.return_value = {"Body": mock_body}

        store = self._make_store()
        data = store.get("artifacts/dep_abc/artifact.zip")

        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="artifacts/dep_abc/artifact.zip",
        )
        assert data == b"artifact-data"

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_missing_raises_artifact_not_found(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
        mock_client.get_object.side_effect = mock_client.exceptions.NoSuchKey(
            error_response, "GetObject"
        )
        mock_client.exceptions.NoSuchKey = type(
            "NoSuchKey", (Exception,), {}
        )
        mock_client.get_object.side_effect = mock_client.exceptions.NoSuchKey(
            "Not found"
        )

        store = self._make_store()
        with pytest.raises(ArtifactNotFoundError):
            store.get("nonexistent/artifact.zip")

    @patch("runtm_worker.storage.s3.boto3")
    def test_delete(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        store = self._make_store()
        store.delete("artifacts/dep_abc/artifact.zip")

        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="artifacts/dep_abc/artifact.zip",
        )

    @patch("runtm_worker.storage.s3.boto3")
    def test_exists_true(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.head_object.return_value = {"ContentLength": 100}

        store = self._make_store()
        assert store.exists("artifacts/dep_abc/artifact.zip") is True

    @patch("runtm_worker.storage.s3.boto3")
    def test_exists_false(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.exceptions.ClientError = type("ClientError", (Exception,), {})
        mock_client.head_object.side_effect = mock_client.exceptions.ClientError("Not found")

        store = self._make_store()
        assert store.exists("nonexistent/artifact.zip") is False

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_uri(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        store = self._make_store()
        uri = store.get_uri("artifacts/dep_abc/artifact.zip")
        assert uri == "s3://test-bucket/artifacts/dep_abc/artifact.zip"

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_size(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.head_object.return_value = {"ContentLength": 42}

        store = self._make_store()
        assert store.get_size("artifacts/dep_abc/artifact.zip") == 42

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_size_missing(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.exceptions.ClientError = type("ClientError", (Exception,), {})
        mock_client.head_object.side_effect = mock_client.exceptions.ClientError("Not found")

        store = self._make_store()
        assert store.get_size("nonexistent") is None

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_path_downloads_to_temp(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_body = MagicMock()
        mock_body.read.return_value = b"zip-contents"
        mock_client.get_object.return_value = {"Body": mock_body}

        store = self._make_store()
        path = store.get_path("artifacts/dep_abc/artifact.zip")

        assert isinstance(path, Path)
        assert path.exists()
        assert path.read_bytes() == b"zip-contents"

    @patch("runtm_worker.storage.s3.boto3")
    def test_put_write_error(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.put_object.side_effect = Exception("Network error")

        store = self._make_store()
        with pytest.raises(StorageWriteError):
            store.put("key", b"data")

    @patch("runtm_worker.storage.s3.boto3")
    def test_get_read_error(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_object.side_effect = Exception("Network error")
        mock_client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})

        store = self._make_store()
        with pytest.raises(StorageReadError):
            store.get("key")


class TestGetArtifactStore:
    """Tests for the factory function."""

    def test_default_returns_local(self) -> None:
        from runtm_worker.storage import get_artifact_store

        store = get_artifact_store(
            backend="local",
            storage_path="/tmp/test-artifacts",
        )
        assert isinstance(store, LocalFileStore)

    @patch("runtm_worker.storage.s3.boto3")
    def test_s3_backend(self, mock_boto3: MagicMock) -> None:
        from runtm_worker.storage import get_artifact_store
        from runtm_worker.storage.s3 import S3FileStore

        mock_boto3.client.return_value = MagicMock()

        store = get_artifact_store(
            backend="s3",
            s3_bucket="my-bucket",
            s3_endpoint_url="https://fly.storage.tigris.dev",
            s3_region="auto",
        )
        assert isinstance(store, S3FileStore)

    def test_invalid_backend_raises(self) -> None:
        from runtm_worker.storage import get_artifact_store

        with pytest.raises(ValueError, match="Unknown storage backend"):
            get_artifact_store(backend="azure-blob")

    def test_s3_missing_bucket_raises(self) -> None:
        from runtm_worker.storage import get_artifact_store

        with pytest.raises(ValueError, match="S3_BUCKET"):
            get_artifact_store(backend="s3", s3_bucket="")
