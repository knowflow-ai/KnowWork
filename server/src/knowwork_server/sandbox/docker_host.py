"""Docker daemon 地址：校验、TLS、建客户端（对应 WeKnora 的 docker_host.go 与 docker_engine.go 的连接部分）。

- 留空：用 docker-py 的 `DockerClient.from_env()`，它与 Docker CLI 同一顺序：DOCKER_HOST → 当前 docker context
  （DOCKER_CONTEXT 或 ~/.docker/config.json）→ unix:///var/run/docker.sock，与 WeKnora 的 DetectLocalDockerHost 一致；
- unix://：路径必须是绝对路径；
- tcp:// / http:// / https://：远端 daemon，必须给 TLS 证书目录（ca.pem、cert.pem、key.pem，Docker 自己的
  DOCKER_CERT_PATH 布局），并且总是校验 daemon 证书。能创建容器的远端 API 等于那台机器的 root shell，
  明文 2375 不可接受。证书留在应用主机上，不进数据库。
- API 版本不在构建时协商（daemon 可能没起），第一次真正调用时由 docker-py 自动协商。
"""

import os
from collections.abc import Callable
from pathlib import Path

import docker
from docker.tls import TLSConfig

REMOTE_SCHEMES = ("tcp", "http", "https")
TLS_FILES = ("ca.pem", "cert.pem", "key.pem")
ClientFactory = Callable[[], docker.DockerClient]


def _split(host: str) -> tuple[str, str] | None:
    scheme, sep, address = host.strip().partition("://")
    return (scheme.lower(), address) if sep else None


def validate_docker_host(host: str) -> str:
    trimmed = host.strip()
    if not trimmed:
        return ""
    parts = _split(trimmed)
    if parts is None:
        raise ValueError("docker_host 必须带协议（unix:// 或 tcp://）")
    scheme, address = parts
    if scheme == "unix":
        if not address.startswith("/"):
            raise ValueError("docker_host 的 unix 套接字路径必须是绝对路径")
        return trimmed
    if scheme in REMOTE_SCHEMES:
        if not address or "/" in address.rstrip("/"):
            raise ValueError("docker_host 远端地址格式应为 tcp://主机:端口")
        return trimmed
    raise ValueError("docker_host 只支持 unix://、tcp://、http://、https://")


def is_remote(host: str) -> bool:
    parts = _split(host)
    return parts is not None and parts[0] in REMOTE_SCHEMES


def validate_remote_tls(host: str, tls_cert_path: str) -> None:
    if host.strip() and is_remote(host) and not tls_cert_path.strip():
        raise ValueError("远端 Docker（tcp://）必须配置 TLS 证书目录 KNOWWORK_SANDBOX_DOCKER_TLS_CERT_PATH")


def tls_config(cert_path: str) -> TLSConfig:
    """证书文件缺一个就拒绝启动：带病启动只会在第一次建沙箱时才报错。"""
    root = Path(cert_path)
    missing = [name for name in TLS_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError(f"TLS 证书目录缺少 {', '.join(missing)}")
    return TLSConfig(client_cert=(str(root / "cert.pem"), str(root / "key.pem")),
                     ca_cert=str(root / "ca.pem"), verify=True)


def client_factory(host: str, tls_cert_path: str, *, timeout_seconds: float) -> ClientFactory:
    """返回一个建 docker-py 客户端的函数（第一次用到时才调用，见 DockerEngine）。"""
    timeout = int(max(1, round(timeout_seconds)))
    tls = tls_config(tls_cert_path) if host.strip() and is_remote(host) else None

    def build() -> docker.DockerClient:
        if not host.strip():
            return docker.DockerClient.from_env(timeout=timeout, environment=dict(os.environ))
        return docker.DockerClient(base_url=host.strip(), tls=tls or False, timeout=timeout, version="auto")

    return build
