"""S3-compatible artifact storage implementation (Tigris / AWS S3 / R2)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import boto3

from runtm_shared.errors import ArtifactNotFoundError, StorageReadError, StorageWriteError
from runtm_shared.storage.base import ArtifactStore


class S3FileStore(ArtifactStore):
    """S3-compatible implementation of ArtifactStore.

    Works with any S3-compatible service (Tigris, AWS S3, Cloudflare R2).
    Fly.io's Tigris is the primary target — set endpoint_url to
    https://fly.storage.tigris.dev and use Fly-issued credentials.

    Usage:
        store = S3FileStore(bucket="runtm-artifacts")
        store.put("artifacts/dep_abc123/artifact.zip", data)
        data = store.get("artifacts/dep_abc123/artifact.zip")
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "auto",
    ):
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
        )

    def put(self, key: str, data: bytes) -> str:
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
            return self.get_uri(key)
        except Exception as e:
            raise StorageWriteError(key, str(e)) from e

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except self._client.exceptions.NoSuchKey as e:
            raise ArtifactNotFoundError(key) from e
        except Exception as e:
            raise StorageReadError(key, str(e)) from e

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            raise StorageWriteError(key, str(e)) from e

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self._client.exceptions.ClientError:
            return False

    def get_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def get_size(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
            return response["ContentLength"]
        except self._client.exceptions.ClientError:
            return None

    def get_path(self, key: str) -> Path:
        """Download artifact to a temp file and return the local path.

        The Docker builder requires a filesystem path to extract the zip.
        For S3-backed storage we download on demand.
        """
        data = self.get(key)
        tmp_dir = Path(tempfile.mkdtemp(prefix="runtm-artifact-"))
        filename = key.rsplit("/", 1)[-1] if "/" in key else key
        dest = tmp_dir / filename
        dest.write_bytes(data)
        return dest
