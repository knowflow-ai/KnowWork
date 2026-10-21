"""会话文件（附件原件、解析结果、产物）的对象存储。

复用 skills/blobstore.py 的按摘要寻址存储，不另起一套键值存储：
- 每个会话一个独立命名空间（本地是一个子目录，S3 是一个键前缀），即
  `{prefix}/{workspace_id}/{session_id}/sha256/xx/<摘要>`；
  技术方案 9.2 的 `{workspace}/{session}/{artifact_id}/{filename}` 中，artifact_id 与 filename 放在表里，
  对象键只按内容寻址。这样读出时仍校验摘要，同一会话里重复的内容只存一份；
- 命名空间按会话隔开，删会话时把该会话表里引用过的摘要逐个删掉即可，不需要跨会话的引用计数，
  也不会误删技能包或其他会话的对象。
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..skills.blobstore import LocalBlobStore, S3BlobStore

SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_PREFIX = "session-files"


class SessionObjectStore(Protocol):
    async def put(self, data: bytes) -> str: ...

    async def get(self, digest: str, *, max_bytes: int) -> bytes: ...

    async def delete(self, digest: str) -> None: ...


# 按（工作空间, 会话）取该会话的存储
SessionStores = Callable[[str, str], SessionObjectStore]


def _segment(value: str) -> str:
    if not isinstance(value, str) or not SEGMENT.fullmatch(value):
        raise ValueError("非法的工作空间或会话标识")
    return value


def local_session_stores(root: str | Path) -> SessionStores:
    base = Path(root)

    def stores(workspace_id: str, session_id: str) -> SessionObjectStore:
        return LocalBlobStore(base / _segment(workspace_id) / _segment(session_id))

    return stores


def s3_session_stores(client: Any, bucket: str, *, prefix: str = DEFAULT_PREFIX) -> SessionStores:
    clean = prefix.strip("/")

    def stores(workspace_id: str, session_id: str) -> SessionObjectStore:
        path = f"{_segment(workspace_id)}/{_segment(session_id)}"
        return S3BlobStore(client, bucket, prefix=f"{clean}/{path}" if clean else path)

    return stores
