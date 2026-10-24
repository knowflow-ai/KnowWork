"""沙箱合同的内存假实现：附件与产物的测试用，不起容器。"""

from dataclasses import dataclass, field

from knowwork_server.sandbox.contract import ExecResult, SandboxError, SandboxFile, SandboxTooLarge


@dataclass
class FakeSandbox:
    inputs: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict)
    outputs: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict)
    input_limit: int = 50 * 1024 * 1024
    fail_put: dict[str, Exception] = field(default_factory=dict)  # 文件名 → 放入时抛的异常
    fail_list: Exception | None = None
    fail_read: dict[str, Exception] = field(default_factory=dict)
    destroyed: list[tuple[str, str]] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    conversions: list[tuple[str, str]] = field(default_factory=list)  # (会话, 扩展名)
    fail_convert: Exception | None = None

    async def put_input(self, workspace_id, session_id, name, data):
        if name in self.fail_put:
            raise self.fail_put[name]
        if len(data) > self.input_limit:
            raise SandboxTooLarge("文件超过沙箱上限")
        self.inputs.setdefault((workspace_id, session_id), {})[name] = bytes(data)
        return SandboxFile(name=name, size=len(data))

    async def read_delivered(self, workspace_id, session_id, path, *, max_bytes):
        """outputs 按 /workspace 下的相对路径存；path 是容器路径。"""
        if self.fail_list is not None:
            raise self.fail_list
        name = path.removeprefix("/workspace/")
        self.reads.append(name)
        if name in self.fail_read:
            raise self.fail_read[name]
        try:
            data = self.outputs[(workspace_id, session_id)][name]
        except KeyError as error:
            raise SandboxError("文件不存在") from error
        if len(data) > max_bytes:
            raise SandboxTooLarge("文件超过上限")
        return data

    async def exec(self, workspace_id, session_id, command, *, timeout_seconds, max_output_bytes):
        return ExecResult(exit_code=0, stdout="", stderr="")

    async def convert_to_pdf(self, workspace_id, session_id, extension, data, *, max_bytes, timeout_seconds):
        self.conversions.append((session_id, extension))
        if self.fail_convert is not None:
            raise self.fail_convert
        return b"%PDF-1.7 converted from " + data[:16]

    async def convert_doc_to_docx(self, workspace_id, session_id, data, *, max_bytes, timeout_seconds):
        self.conversions.append((session_id, "doc"))
        if self.fail_convert is not None:
            raise self.fail_convert
        return b"PK converted from " + data[:16]

    async def recalc_xlsx(self, workspace_id, session_id, data, *, max_bytes, timeout_seconds):
        self.conversions.append((session_id, "recalc"))
        if self.fail_convert is not None:
            raise self.fail_convert
        return data

    async def destroy(self, workspace_id, session_id):
        self.destroyed.append((workspace_id, session_id))

    def write_output(self, workspace_id, session_id, name, data: bytes) -> None:
        """name 是 /workspace 下的相对路径。"""
        self.outputs.setdefault((workspace_id, session_id), {})[name] = data

    def delivered(self, workspace_id, session_id) -> list[str]:
        """这个会话写过的全部文件的宿主路径（扮演模型 present 了它们）。"""
        return [f"/sessions/{session_id}/{name}" for name in sorted(self.outputs.get((workspace_id, session_id), {}))]
