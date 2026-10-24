"""授权状态。开源版没有授权，只跑开源功能；校验 License 的实现由扩展包提供（入口组 knowwork.license）。

开源代码不做签名校验：真正的保护在扩展包自己（扩展的代码不开源），这里只按校验结果决定加载哪些扩展。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

LicenseStatus = Literal["none", "valid", "expired", "invalid"]


@dataclass(frozen=True)
class LicenseState:
    status: LicenseStatus
    message: str  # 给系统管理员看的一句话
    modules: frozenset[str] = field(default_factory=frozenset)  # 授权的模块，只有 valid 时才有
    customer: str | None = None
    valid_until: datetime | None = None

    def allows(self, module: str) -> bool:
        return self.status == "valid" and module in self.modules


NO_LICENSE = LicenseState(status="none", message="没有安装授权，只启用开源功能")


class LicenseVerifier(Protocol):
    def verify(self) -> LicenseState:
        """读取并校验授权。不抛异常：读不到、过期、签名不对都用 status 与 message 说明。"""
        ...
