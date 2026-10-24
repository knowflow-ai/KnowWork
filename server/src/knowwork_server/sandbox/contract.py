"""沙箱合同：附件、产物等模块只依赖这里，不认识容器实现。

目录约定：
- /workspace/input：附件，只读放入；
- 产物：模型用宿主的官方 present 声明交付的文件，回合结束时按路径收取；
- 其余位置是模型的工作目录。
"""

from dataclasses import dataclass
from typing import Protocol

INPUT_DIR = "/workspace/input"


@dataclass(frozen=True)
class SandboxFile:
    name: str  # 相对所在目录的文件名，不含路径分隔符
    size: int


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


class SandboxError(Exception):
    """沙箱不可用或操作失败。message 只写面向用户的说明，不带容器、主机等内部细节。"""


class SandboxTooLarge(SandboxError):
    """文件超出上限。"""


class SandboxConversionFailed(SandboxError):
    """LibreOffice 打不开或转不出这份文件（多半是文件损坏、加了密码）：换个时间重试也一样。
    超时、内存不够、沙箱不可用仍报 SandboxError，那些可以重试。"""


class SessionSandbox(Protocol):
    """一个会话的沙箱。实现负责按需创建容器、空闲回收与会话删除时销毁。"""

    async def put_input(self, workspace_id: str, session_id: str, name: str, data: bytes) -> SandboxFile: ...

    async def read_delivered(self, workspace_id: str, session_id: str, path: str, *, max_bytes: int) -> bytes: ...

    async def exec(self, workspace_id: str, session_id: str, command: list[str], *, timeout_seconds: float,
                   max_output_bytes: int) -> ExecResult: ...

    async def convert_to_pdf(self, workspace_id: str, session_id: str, extension: str, data: bytes, *,
                             max_bytes: int, timeout_seconds: float) -> bytes: ...

    async def convert_doc_to_docx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                                  timeout_seconds: float) -> bytes: ...

    async def recalc_xlsx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                          timeout_seconds: float) -> bytes: ...

    async def destroy(self, workspace_id: str, session_id: str) -> None: ...
