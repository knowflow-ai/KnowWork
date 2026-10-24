"""对象存储按配置装配：配了 S3 兼容存储（部署里是 RustFS）就用它，否则落本机目录。api 与 worker 进程共用。

- blob_store：技能包，在存储根下（键 sha256/...）；
- session_stores：会话文件，命名空间 session-files；
- namespaced_stores：给扩展用的命名空间（例如知识库用 library），同样按（工作空间, 二级标识）分开、按摘要寻址。
"""

import re
from typing import Any

from .config import Settings
from .sessionfiles.storage import DEFAULT_PREFIX, SessionStores, local_session_stores, s3_session_stores
from .skills.blobstore import BlobStore, LocalBlobStore, S3BlobStore, make_s3_client


def s3_client(settings: Settings) -> Any:
    return make_s3_client(endpoint_url=settings.s3_endpoint_url,
                          access_key=settings.s3_access_key.get_secret_value(),
                          secret_key=settings.s3_secret_key.get_secret_value())


def blob_store(settings: Settings) -> BlobStore:
    if settings.s3_endpoint_url:
        return S3BlobStore(s3_client(settings), settings.s3_bucket)
    return LocalBlobStore(settings.blob_dir)


_NAMESPACE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
# 已经有人用的位置：技能包在根下的 sha256/，会话文件在 session-files/
_TAKEN = frozenset({"sha256", DEFAULT_PREFIX})


def namespaced_stores(settings: Settings, namespace: str) -> SessionStores:
    if not _NAMESPACE.fullmatch(namespace) or namespace in _TAKEN:
        raise ValueError(f"invalid storage namespace: {namespace!r}")
    if settings.s3_endpoint_url:
        return s3_session_stores(s3_client(settings), settings.s3_bucket, prefix=namespace)
    return local_session_stores(f"{settings.blob_dir.rstrip('/')}/{namespace}")


def session_stores(settings: Settings) -> SessionStores:
    if settings.s3_endpoint_url:
        return s3_session_stores(s3_client(settings), settings.s3_bucket)
    return local_session_stores(f"{settings.blob_dir.rstrip('/')}/{DEFAULT_PREFIX}")
