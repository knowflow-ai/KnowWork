"""没配沙箱镜像时的占位实现：附件放不进、产物收不到，都明确告诉用户，而不是静默丢掉。"""

from .contract import ExecResult, SandboxError, SandboxFile

NOT_ENABLED = "当前部署没有启用会话沙箱，附件与产物不可用"


class UnavailableSandbox:
    async def put_input(self, workspace_id: str, session_id: str, name: str, data: bytes) -> SandboxFile:
        raise SandboxError(NOT_ENABLED)

    async def read_delivered(self, workspace_id: str, session_id: str, path: str, *, max_bytes: int) -> bytes:
        raise SandboxError(NOT_ENABLED)

    async def exec(self, workspace_id: str, session_id: str, command: list[str], *, timeout_seconds: float,
                   max_output_bytes: int) -> ExecResult:
        raise SandboxError(NOT_ENABLED)

    async def convert_to_pdf(self, workspace_id: str, session_id: str, extension: str, data: bytes, *,
                             max_bytes: int, timeout_seconds: float) -> bytes:
        raise SandboxError(NOT_ENABLED)

    async def convert_doc_to_docx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                                  timeout_seconds: float) -> bytes:
        raise SandboxError(NOT_ENABLED)

    async def recalc_xlsx(self, workspace_id: str, session_id: str, data: bytes, *, max_bytes: int,
                          timeout_seconds: float) -> bytes:
        raise SandboxError(NOT_ENABLED)

    async def destroy(self, workspace_id: str, session_id: str) -> None:
        return None
