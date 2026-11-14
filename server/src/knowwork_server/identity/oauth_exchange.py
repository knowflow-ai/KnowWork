"""令牌交换（RFC 8693）：原生应用拿自己的访问令牌，按受众换一个范围更小的短期令牌。

用处：桌面端的本机宿主要调模型、工具、技能，用的是和服务端宿主同一种短期令牌（受众 model、tools、skills），
不拿用户的访问令牌本身。凭据只在桌面端主进程，主进程换好再经私有通道交给本机宿主（照 DSH 官方桌面版的做法）。

开源只做交换的流程（核对客户端与访问令牌、找受众）；每个受众怎么签、要什么参数、谁能换，由登记的交换者决定。
没登记的受众一律拒绝（invalid_target）。
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from ..auth.principal import Principal

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


@dataclass(frozen=True)
class Exchanged:
    token: str
    expires_in: int


# 交换者：拿到换令牌的人（原生应用登录的身份）与请求里的参数（空间、会话等由受众自己解释）；不给就抛 OAuthError
Exchanger = Callable[[Principal, Mapping[str, str]], Awaitable[Exchanged]]


class TokenExchangers:
    """受众登记表：装配时登记，之后只读。"""

    def __init__(self) -> None:
        self._exchangers: dict[str, Exchanger] = {}

    def register(self, audience: str, exchanger: Exchanger) -> None:
        if audience in self._exchangers:
            raise ValueError(f"受众 {audience} 已登记")
        self._exchangers = {**self._exchangers, audience: exchanger}

    def get(self, audience: str) -> Exchanger | None:
        return self._exchangers.get(audience)
