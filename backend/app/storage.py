"""Byte storage: Vercel Blob in production, local disk in development.

Serverless instances share nothing, so every bit of state (originals, metadata,
passages) lives in object storage. Objects are written once and never
overwritten, which sidesteps CDN staleness on Blob reads; each listing returns a
version tag per object so readers can cache safely.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    name: str

    async def put(self, path: str, data: bytes, content_type: str | None = None) -> None: ...
    async def get(self, path: str) -> bytes | None: ...
    async def list(self, prefix: str) -> dict[str, str]: ...  # path -> version tag
    async def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _p(self, path: str) -> Path:
        return self.root / path

    async def put(self, path: str, data: bytes, content_type: str | None = None) -> None:
        def write() -> None:
            p = self._p(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)

        await asyncio.to_thread(write)

    async def get(self, path: str) -> bytes | None:
        p = self._p(path)
        return await asyncio.to_thread(p.read_bytes) if p.is_file() else None

    async def list(self, prefix: str) -> dict[str, str]:
        def scan() -> dict[str, str]:
            base = self._p(prefix)
            if not base.exists():
                return {}
            return {
                f.relative_to(self.root).as_posix(): str(f.stat().st_mtime_ns)
                for f in base.rglob("*")
                if f.is_file() and not f.name.endswith(".tmp")
            }

        return await asyncio.to_thread(scan)

    async def delete_prefix(self, prefix: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self._p(prefix), True)


class BlobStorage:
    name = "vercel-blob"

    def __init__(self, access: str) -> None:
        from vercel import blob  # imported lazily so local dev doesn't need credentials

        self.blob = blob
        self.access = access

    async def put(self, path: str, data: bytes, content_type: str | None = None) -> None:
        await self.blob.put_blob(path, data, access=self.access, content_type=content_type)

    async def get(self, path: str) -> bytes | None:
        try:
            return (await self.blob.get_blob(path, access=self.access)).body
        except self.blob.BlobNotFoundError:
            return None

    async def list(self, prefix: str) -> dict[str, str]:
        return {item.pathname: item.etag async for item in self.blob.query_blobs(prefix=prefix)}

    async def delete_prefix(self, prefix: str) -> None:
        paths = list(await self.list(prefix))
        for i in range(0, len(paths), 1000):  # the API deletes at most 1,000 per call
            await self.blob.delete_blob(paths[i : i + 1000])


def make_storage(data_dir: Path) -> Storage | None:
    """Blob when a store is connected; local disk outside Vercel; None if Vercel has no store."""
    if os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("BLOB_STORE_ID"):
        return BlobStorage(access=os.getenv("BLOB_ACCESS", "private"))
    if os.getenv("VERCEL"):
        return None  # a function's disk is ephemeral and per-instance: refuse rather than lose data
    return LocalStorage(data_dir)
