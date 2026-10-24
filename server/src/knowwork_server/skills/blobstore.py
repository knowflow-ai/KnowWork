"""按内容摘要寻址的对象存储，下载时校验摘要。

- 键 = SHA-256 十六进制摘要；同一内容写多次只存一份，写入天然幂等；
- 读出时重新计算摘要，与键不符一律视为损坏（BlobIntegrityError），不把可疑内容交出去；
- 两个实现：本地文件系统（开发与单机部署）、S3 兼容（部署用 RustFS）。S3 用 boto3，它是同步库，
  调用放到线程里执行，避免阻塞事件循环。

技能包目前不删除对象：发布版本不可变、可能被多个技能共享，回收留给以后的引用计数清理。
两个实现另提供 delete，只给按会话分命名空间的会话文件（附件与产物，见 sessionfiles）使用，技能侧不调用。
"""

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .errors import BlobIntegrityError

DIGEST = re.compile(r"^[0-9a-f]{64}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_digest(digest: str) -> str:
    if not isinstance(digest, str) or not DIGEST.match(digest):
        raise BlobIntegrityError("摘要格式不合法")
    return digest


def _verify(digest: str, data: bytes) -> bytes:
    if sha256_hex(data) != digest:
        raise BlobIntegrityError("内容与摘要不符")
    return data


class BlobStore(Protocol):
    async def put(self, data: bytes) -> str:
        """写入并返回摘要。"""
        ...

    async def get(self, digest: str, *, max_bytes: int) -> bytes:
        """读出并校验摘要；超过 max_bytes、不存在或不符都抛 BlobIntegrityError。"""
        ...

    async def exists(self, digest: str) -> bool: ...


def _relative_key(digest: str) -> str:
    return f"sha256/{digest[:2]}/{digest}"


class LocalBlobStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, digest: str) -> Path:
        return self._root / _relative_key(_checked_digest(digest))

    async def put(self, data: bytes) -> str:
        digest = await asyncio.to_thread(sha256_hex, data)
        await asyncio.to_thread(self._write, self._path(digest), data)
        return digest

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp, path)  # 原子替换：读者要么看不到，要么看到完整内容
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    async def get(self, digest: str, *, max_bytes: int) -> bytes:
        path = self._path(digest)
        return _verify(digest, await asyncio.to_thread(self._read, path, max_bytes))

    @staticmethod
    def _read(path: Path, max_bytes: int) -> bytes:
        try:
            with path.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        except OSError as error:
            raise BlobIntegrityError("对象不存在或不可读") from error
        if len(data) > max_bytes:
            raise BlobIntegrityError("对象超过大小上限")
        return data

    async def exists(self, digest: str) -> bool:
        return await asyncio.to_thread(self._path(digest).is_file)

    async def delete(self, digest: str) -> None:
        """删除对象；不存在视为已删除。"""
        path = self._path(digest)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as error:
            raise BlobIntegrityError("删除对象失败") from error


class S3BlobStore:
    """S3 兼容存储。client 是 boto3 的 S3 客户端（见 make_s3_client）。"""

    def __init__(self, client: Any, bucket: str, *, prefix: str = "skills") -> None:
        if not bucket:
            raise ValueError("bucket is required")
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def _key(self, digest: str) -> str:
        key = _relative_key(_checked_digest(digest))
        return f"{self._prefix}/{key}" if self._prefix else key

    async def put(self, data: bytes) -> str:
        digest = await asyncio.to_thread(sha256_hex, data)
        key = self._key(digest)
        try:
            if not await self.exists(digest):
                await asyncio.to_thread(self._client.put_object, Bucket=self._bucket, Key=key, Body=data,
                                        ContentType="application/octet-stream", Metadata={"sha256": digest})
        except Exception as error:
            raise BlobIntegrityError("写入对象存储失败") from error
        return digest

    async def get(self, digest: str, *, max_bytes: int) -> bytes:
        key = self._key(digest)
        return _verify(digest, await asyncio.to_thread(self._read, key, max_bytes))

    def _read(self, key: str, max_bytes: int) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            if int(response.get("ContentLength") or 0) > max_bytes:
                raise BlobIntegrityError("对象超过大小上限")
            body = response["Body"]
            try:
                data = body.read(max_bytes + 1)
            finally:
                body.close()
        except BlobIntegrityError:
            raise
        except Exception as error:  # 网络、权限、不存在：对外一律是「读不到」
            raise BlobIntegrityError("对象不存在或不可读") from error
        if len(data) > max_bytes:
            raise BlobIntegrityError("对象超过大小上限")
        return data

    async def ensure_bucket(self) -> None:
        """启动时调用：桶不存在就建（全新部署的 RustFS 里还没有桶）；权限不足等其他错误直接报出。"""
        await asyncio.to_thread(self._ensure_bucket)

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) not in ("404", "NoSuchBucket", "NotFound"):
                raise BlobIntegrityError("对象存储不可用") from error
        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) not in ("BucketAlreadyOwnedByYou",
                                                                         "BucketAlreadyExists"):
                raise BlobIntegrityError("创建对象存储桶失败") from error

    async def exists(self, digest: str) -> bool:
        key = self._key(digest)
        return await asyncio.to_thread(self._head, key)

    async def delete(self, digest: str) -> None:
        """删除对象；S3 的 delete_object 对不存在的键也返回成功。"""
        key = self._key(digest)
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
        except Exception as error:
            raise BlobIntegrityError("删除对象失败") from error

    def _head(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def make_s3_client(*, endpoint_url: str, access_key: str, secret_key: str, region: str = "us-east-1") -> Any:
    """RustFS 等 S3 兼容服务：路径风格寻址、签名 v4。密钥由调用方从配置读入，这里不留默认值。"""
    import boto3
    from botocore.config import Config

    if not access_key or not secret_key:
        raise ValueError("S3 access key and secret key are required")
    return boto3.client("s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key, region_name=region,
                        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                      retries={"max_attempts": 3, "mode": "standard"}))
