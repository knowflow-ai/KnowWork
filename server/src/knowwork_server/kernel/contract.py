"""内核合同 kernel.v1：业务服务与运行时宿主之间唯一的接口。

上层模块只能依赖这里的 Protocol 与模型，不能直接引用宿主客户端实现。
宿主协议的变化只改实现，不改这里；这里的变化必须升版本号。
"""

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "kernel.v1"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kernel.v1"] = SCHEMA_VERSION


class SkillGate(_Frozen):
    """技能门。required_tool 为空时只设补救上限，完成判据来自本会话加载过的技能（completion-tools）。"""

    required_tool: str | None = Field(default=None, min_length=1)
    max_nudges: int | None = Field(default=None, ge=0, le=5)


class SessionPolicy(_Frozen):
    """宿主不保存策略：打开和恢复会话时都由业务服务给出。"""

    deny: tuple[str, ...] = ()
    gate: SkillGate | None = None


class SessionSpec(_Frozen):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    policy: SessionPolicy = SessionPolicy()
    resume: bool = False
    # 工具桥令牌：宿主据此向业务服务拉取并执行本会话可用的工具；为空则本会话不开工具桥。
    tools_token: str | None = Field(default=None, min_length=16, repr=False)
    # 技能令牌（受众 skills）：宿主据此拉取本会话可用技能清单与技能正文；为空则本会话没有技能。
    skills_token: str | None = Field(default=None, min_length=16, repr=False)


class TurnAttachment(_Frozen):
    """本轮消息附带的一个文件：已在本会话容器的 /workspace/input 里（照官方 dsh-attachment，模型按需读）。"""

    name: str = Field(min_length=1, max_length=200)
    path: str = Field(pattern=r"^/workspace/input/[^/]{1,200}$")
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extracted_path: str | None = Field(default=None, pattern=r"^/workspace/input/[^/]{1,200}$")


class TurnInput(_Frozen):
    text: str = Field(min_length=1, max_length=200_000)
    mode: Literal["followup", "steer"] = "followup"
    language: Literal["zh", "en"] | None = None  # 用户在聊天页指定的语言；不给时按提问判断（kernel/locale.py）
    attachments: tuple[TurnAttachment, ...] = Field(default=(), max_length=20)


class KernelEvent(_Frozen):
    """宿主事件帧。kind 为 session/event 时 data 是 DSH 原始会话事件；其余为 KnowWork 插件事件。

    入站帧来自另一个进程，宿主新增字段不应让业务服务拒收，所以这里忽略未知字段。
    帧格式本身不合法（缺游标等）时由宿主客户端换成 kind=kernel.invalid 的占位帧。
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    epoch: str = Field(min_length=1, max_length=64)
    cursor: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=64)
    time: int = 0
    data: Any = None


INVALID_FRAME_KIND = "kernel.invalid"
GAP_FRAME_KIND = "gap"


class PendingApproval(_Frozen):
    id: str
    tool_name: str
    call_id: str | None = None
    reason: str | None = None


class QuestionOption(_Frozen):
    label: str
    description: str | None = None


class Question(_Frozen):
    """Agent 向用户提的一个问题（官方 ask_user_question 的词汇）。"""

    id: str
    question: str
    header: str | None = None
    detail: str | None = None
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = False


class PendingQuestion(_Frozen):
    """一次提问（一批问题），id 是工具调用 id。

    state：open 是工具调用还在前台等回答；continued 是前台等待已结束（到期或宿主重启过），
    问题仍可答，回答会以新一轮送回 Agent。
    """

    id: str
    state: Literal["open", "continued"]
    questions: tuple[Question, ...]


class QuestionAnswerItem(_Frozen):
    """一题的回答：selected 是选中的选项标签；单选题填了 custom 时 selected 为空；两者都空表示跳过。"""

    id: str = Field(min_length=1, max_length=256)
    selected: tuple[str, ...] = Field(default=(), max_length=64)
    custom: str | None = Field(default=None, max_length=4000)


class QuestionAnswer(_Frozen):
    """一次提问的完整回答：每题恰好一条。是否对得上问题由宿主核对（它手里有原问题）。"""

    answers: tuple[QuestionAnswerItem, ...] = Field(min_length=1, max_length=64)


class CancelResult(_Frozen):
    settled: bool
    status: str


class KernelError(Exception):
    """宿主返回的错误。code 是宿主的稳定错误码，业务服务据此决定怎么处理。"""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SessionNotFound(KernelError):
    pass


class SessionBusy(KernelError):
    pass


class EpochMismatch(KernelError):
    """宿主重启过，旧游标失效。调用方应从头重新读取。"""


class AgentKernel(Protocol):
    async def open_session(self, spec: SessionSpec) -> str: ...

    async def send(self, session_id: str, turn: TurnInput) -> str: ...

    async def cancel(self, session_id: str) -> CancelResult: ...

    async def dispose(self, session_id: str) -> None: ...

    async def pending_approvals(self, session_id: str) -> list[PendingApproval]: ...

    async def decide_approval(self, session_id: str, approval_id: str, allow: bool) -> None: ...

    async def pending_questions(self, session_id: str) -> list[PendingQuestion]: ...

    async def answer_question(self, session_id: str, question_id: str, answer: QuestionAnswer) -> None: ...

    async def rotate_model_credential(self, token: str) -> None: ...

    async def rotate_tool_credential(self, session_id: str, token: str) -> None: ...

    async def rotate_skill_credential(self, session_id: str, token: str) -> None: ...

    def subscribe(self, session_id: str, *, after: int = 0, epoch: str | None = None) -> AsyncIterator[KernelEvent]: ...
