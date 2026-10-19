"""The remote of a depot: a copy of its contents, results, input identities and runs in object storage, shared by the
machines that use it.

    <remote>/cas/sha256/ab/<sha>             contents
    <remote>/ac/ab/<key>.json                results
    <remote>/inputs/<kind>/<digest>.json     input identities by name ({"name", "sha256", "size"}; digest: the
                                             sha256 of the name)
    <remote>/runs/<run>.json                 run manifests
    <remote>/runs/selections/<digest>        the id of the latest run of a selection
    <remote>/catalogs/versions/<id>.json     one catalog version and its labels
    <remote>/catalogs/<sha>.bin              catalog bytes, verified when fetched

A remote is `s3://<bucket>/<prefix>` (S3-compatible object storage: path-style requests, checksums only where the
request needs them, so stores other than AWS answer them) or `file://<directory>` (a shared directory). Every
operation but `list` names one object, so a remote behind a gateway that caches listings answers them all; `list` is
used for catalog version discovery and `depot gc`. Contents and results are written once: a write is skipped when
the object is there with the same size. The local depot is the remote's cache (pipeline.depot).
"""

from __future__ import annotations

import os
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from ..core.cache import temp_path

__all__ = ["Remote", "open_remote"]


class Remote:
    """The operations of a remote, by object key (a relative path under the remote's root)."""

    url: str

    def get(self, key: str) -> bytes | None:
        """The object's bytes, None when it does not exist."""
        raise NotImplementedError

    def size(self, key: str) -> int | None:
        """The object's size, None when it does not exist."""
        raise NotImplementedError

    def put(self, key: str, data: bytes) -> None:
        """Write (or replace) the object."""
        raise NotImplementedError

    def put_file(self, key: str, path: Path) -> None:
        """Write (or replace) the object with a file's bytes."""
        self.put(key, Path(path).read_bytes())

    def delete(self, keys: list[str]) -> None:
        """Remove objects (missing ones are no error)."""
        raise NotImplementedError

    def list(self, prefix: str) -> Iterator[tuple[str, int, float]]:
        """(key, size, time it was written: seconds since the epoch) of every object whose key starts with
        `prefix`."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.url!r})"


class FileRemote(Remote):
    """A remote in a directory (`file://<directory>`)."""

    def __init__(self, url: str, root: Path):
        self.url, self.root = url, Path(root)

    def __getstate__(self):
        return {"url": self.url, "root": self.root}

    def __setstate__(self, state):
        self.__init__(state["url"], state["root"])

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not key or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"not an object key: {key!r}")
        return self.root.joinpath(*parts)

    def get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    def size(self, key: str) -> int | None:
        try:
            return self._path(key).stat().st_size
        except FileNotFoundError:
            return None

    def put(self, key: str, data: bytes) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_path(dst)
        try:
            with open(tmp, "xb") as f:
                f.write(data)
            os.replace(tmp, dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def delete(self, keys: list[str]) -> None:
        for k in keys:
            self._path(k).unlink(missing_ok=True)

    def list(self, prefix: str) -> Iterator[tuple[str, int, float]]:
        if not self.root.is_dir():
            return
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not p.name.endswith(".part"):
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    st = p.stat()
                    yield key, st.st_size, st.st_mtime


class S3Remote(Remote):
    """A remote in S3-compatible object storage (`s3://<bucket>/<prefix>`), through boto3 (the `remote` extra)."""

    def __init__(self, url: str, endpoint: str | None, key_id: str | None, secret: str | None, region: str):
        parts = urllib.parse.urlsplit(url)
        if not parts.netloc:
            raise ValueError("an s3:// remote names its bucket: s3://<bucket>/<prefix>")
        self.url, self.bucket = url, parts.netloc
        prefix = parts.path.strip("/")
        self.prefix = f"{prefix}/" if prefix else ""
        self.endpoint, self.key_id, self.secret, self.region = endpoint, key_id, secret, region
        self._local = threading.local()

    def __getstate__(self):
        return {k: getattr(self, k) for k in ("url", "endpoint", "key_id", "secret", "region")}

    def __setstate__(self, state):
        self.__init__(state["url"], state["endpoint"], state["key_id"], state["secret"], state["region"])

    def __repr__(self) -> str:  # never the credentials
        return f"S3Remote({self.url!r})"

    @property
    def _s3(self):
        """One client per thread (boto3 clients are not shared between threads here)."""
        client = getattr(self._local, "client", None)
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise RuntimeError("an s3:// depot remote needs boto3: pip install 'nnnotes[remote]'") from None
            config = Config(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 8, "mode": "adaptive"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            )
            client = self._local.client = boto3.client(
                "s3",
                endpoint_url=self.endpoint or None,
                aws_access_key_id=self.key_id or None,
                aws_secret_access_key=self.secret or None,
                region_name=self.region,
                config=config,
            )
        return client

    @staticmethod
    def _missing(error) -> bool:
        return error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")

    def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self._s3.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()
        except ClientError as e:
            if self._missing(e):
                return None
            raise

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self._s3.head_object(Bucket=self.bucket, Key=self.prefix + key)["ContentLength"])
        except ClientError as e:
            if self._missing(e):
                return None
            raise

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data)

    def put_file(self, key: str, path: Path) -> None:
        self._s3.upload_file(str(path), self.bucket, self.prefix + key)

    def delete(self, keys: list[str]) -> None:
        for i in range(0, len(keys), 1000):
            batch = [{"Key": self.prefix + k} for k in keys[i : i + 1000]]
            if batch:
                response = self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
                errors = response.get("Errors", [])
                if errors:
                    codes = ", ".join(sorted({error.get("Code", "Unknown") for error in errors}))
                    raise RuntimeError(f"S3 deletion failed for {len(errors)} object(s): {codes}")

    def list(self, prefix: str) -> Iterator[tuple[str, int, float]]:
        pages = self._s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + prefix)
        for page in pages:
            for o in page.get("Contents", ()):
                yield o["Key"][len(self.prefix) :], int(o["Size"]), o["LastModified"].timestamp()


def open_remote(
    url: str,
    *,
    endpoint: str | None = None,
    key_id: str | None = None,
    secret: str | None = None,
    region: str | None = None,
) -> Remote:
    """The remote a URL names: `s3://<bucket>/<prefix>` (with the endpoint, credentials and signing region of the
    object storage; region default us-east-1) or `file://<directory>`."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "s3":
        return S3Remote(url, endpoint, key_id, secret, region or "us-east-1")
    if parts.scheme == "file":
        path = urllib.parse.unquote(parts.path)
        if parts.netloc or not path:  # file://host/path is not a local directory
            raise ValueError("a file:// remote names a local directory: file:///<directory>")
        if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]  # file:///C:/x
        return FileRemote(url, Path(path))
    raise ValueError("a depot remote is s3://<bucket>/<prefix> or file:///<directory>")
