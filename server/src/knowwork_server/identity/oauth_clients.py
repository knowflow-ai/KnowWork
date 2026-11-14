"""原生应用登录的客户端登记（OAuth 2.0 公开客户端，RFC 8252）。

开源只提供授权服务，不内置任何客户端：没登记时授权与换令牌一律拒绝。客户端由扩展登记（例如桌面端）。
回调只认本机回环地址（RFC 8252 7.3）：http、IP 字面量 127.0.0.1 或 [::1]、端口不限（应用每次启动临时监听一个），
路径必须与登记的一致。不认 localhost（RFC 8252 8.3：名字可能被解析到别处），也不认带查询串、片段或用户信息的地址。
"""

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

CLIENT_ID_PATTERN = r"^[a-z][a-z0-9-]{2,63}$"
MAX_REDIRECT_LENGTH = 300


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    # 给人看的名字：确认页「允许 <名字> 登录你的账号」、已登录的设备列表
    name: str
    # 本机回调的路径（例如 /callback）；主机必须是回环 IP，端口随应用
    redirect_path: str

    def __post_init__(self) -> None:
        if not self.redirect_path.startswith("/") or "?" in self.redirect_path or "#" in self.redirect_path:
            raise ValueError("回调路径必须以 / 开头，且不带查询串与片段")

    def accepts_redirect(self, redirect_uri: str) -> bool:
        if len(redirect_uri) > MAX_REDIRECT_LENGTH:
            return False
        try:
            parts = urlsplit(redirect_uri)
            host = parts.hostname
            _port = parts.port  # 端口不是数字时这里抛 ValueError
        except ValueError:
            return False
        if parts.scheme != "http" or host is None or parts.username or parts.password:
            return False
        if parts.query or parts.fragment or parts.path != self.redirect_path:
            return False
        try:
            return ip_address(host).is_loopback
        except ValueError:
            return False


class OAuthClients:
    """客户端登记表：扩展在装配时登记，之后只读。"""

    def __init__(self) -> None:
        self._clients: dict[str, OAuthClient] = {}

    def register(self, client: OAuthClient) -> None:
        if client.client_id in self._clients:
            raise ValueError(f"客户端 {client.client_id} 已登记")
        self._clients = {**self._clients, client.client_id: client}

    def get(self, client_id: str) -> OAuthClient | None:
        return self._clients.get(client_id)

    def registered(self) -> bool:
        """有没有登记任何原生应用：没有时，给原生应用用的公开入口都不挂（开源部署不多路由）。"""
        return bool(self._clients)
