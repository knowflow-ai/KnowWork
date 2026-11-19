"""沙箱合同：附件、产物等模块只依赖这里，不认识容器实现。

文件一律用工作区路径（workpath.WorkPath），沙箱自己把它换成容器里的位置：
- 工作区里的 input/：附件，只读放入（workpath.INPUT）；
- 产物：模型用宿主的官方 present 声明交付的文件，回合结束时按路径收取；
- 其余位置是模型的工作目录。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # workpath 引用本模块的 SandboxError，这里只在类型检查时反过来引用它
    from ..workpath import WorkPath


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


class SandboxNotStarted(SandboxError):
    """会话没有在运行的沙箱：还没建过，或空闲后被回收了（回收后工作区是空的）。只读的操作不为它新建容器。"""


class SandboxFileError(SandboxError):
    """文件操作失败，code 说明是哪一种：not_found、is_directory、not_regular_file、permission_denied、io_error。"""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class SandboxConversionFailed(SandboxError):
    """LibreOffice 打不开或转不出这份文件（多半是文件损坏、加了密码）：换个时间重试也一样。
    超时、内存不够、沙箱不可用仍报 SandboxError，那些可以重试。"""


class SessionSandbox(Protocol):
    """一个会话的沙箱。实现负责按需创建容器、空闲回收与会话删除时销毁。"""

    async def put_input(self, workspace_id: str, session_id: str, name: str, data: bytes) -> SandboxFile: ...

    async def read_delivered(self, workspace_id: str, session_id: str, path: "WorkPath", *,
                             max_bytes: int) -> bytes: ...

    # 人在编辑器里改过、存了盘的文件写回会话的工作区（附件目录以外），下一轮模型读到的是改过的。
    # 只在沙箱还在时写（返回 True），不为此新建：沙箱回收后，读文件本来就先读存下来的最新一版
    async def write_back(self, workspace_id: str, session_id: str, path: "WorkPath", data: bytes) -> bool: ...

    async def exec(self, workspace_id: str, session_id: str, command: list[str], *, timeout_seconds: float,
                   max_output_bytes: int) -> ExecResult: ...

    async def convert_to_pdf(self, workspace_id: str, session_id: str, extension: str, data: bytes, *,
                             max_bytes: int, timeout_seconds: float) -> bytes: ...

    async def convert_doc_to_docx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                                  timeout_seconds: float) -> bytes: ...

    async def recalc_xlsx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                          timeout_seconds: float) -> bytes: ...

    async def extract_text(self, workspace_id: str, session_id: str, extension: str, data: bytes, *,
                           max_bytes: int, timeout_seconds: float) -> bytes: ...

    async def destroy(self, workspace_id: str, session_id: str) -> None: ...
