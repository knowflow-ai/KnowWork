"""内核合同 kernel.v1：业务服务与运行时宿主之间唯一的接口。

上层模块只能依赖这里的 Protocol 与模型，不能直接引用宿主客户端实现。
宿主协议的变化只改实现，不改这里；这里的变化必须升版本号。
"""

import json
import re
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class SessionOwner(_Frozen):
    """对话的属主。宿主的多用户适配层据此记录、比对属主级资源（定时任务这类）的归属，不据此判权限。"""

    workspace_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)


class SessionSpec(_Frozen):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    policy: SessionPolicy = SessionPolicy()
    resume: bool = False
    # 工具桥令牌：宿主据此向业务服务拉取并执行本会话可用的工具；为空则本会话不开工具桥。
    tools_token: str | None = Field(default=None, min_length=16, repr=False)
    # 技能令牌（受众 skills）：宿主据此拉取本会话可用技能清单与技能正文；为空则本会话没有技能。
    skills_token: str | None = Field(default=None, min_length=16, repr=False)
    # 属主：不给时宿主上属主级插件（定时任务等）对这个会话不可用
    owner: SessionOwner | None = None
    # 这段对话启用的预置插件键（plugins/catalog.py），业务服务已按档位与个人开关算好、排序。宿主按它装官方 Agent 预设；
    # 不带就是没有。设备上的本机宿主用官方预设，不认这一项
    plugins: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")], ...] = Field(default=(), max_length=32)


class OutlineEntry(_Frozen):
    """附件目录里的一条：在那份文字（解析出的 Markdown，或纯文本原件）里的行号与标题。"""

    line: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=200)


class TurnAttachment(_Frozen):
    """本轮消息附带的一个文件：已在本会话工作区的附件目录 input/ 里（照官方 dsh-attachment，模型按需读）。
    路径是工作区路径（workpath.py，例如 input/合同.pdf），给模型看哪种写法由宿主按自己的挂载点定。"""

    name: str = Field(min_length=1, max_length=200)
    path: str = Field(pattern=r"^input/[^/]{1,200}$")
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extracted_path: str | None = Field(default=None, pattern=r"^input/[^/]{1,200}$")
    # 文档解析还没完成：extracted_path 是解析好以后放的位置，现在那里还没有文件。网页发送前会等解析完，
    # 用户点了「直接发送」、或经接口发来的消息才会这样
    extraction_pending: bool = False
    # 给模型读的文字（附件 reading.py）：放得下的全文直接放进这一轮（text）；放不下的只给字数、页数与目录，
    # 模型按行号分段读。文字是解析出的 Markdown，纯文本附件就是原件本身
    text: str | None = Field(default=None, max_length=1_000_000)
    text_chars: int | None = Field(default=None, ge=0)
    text_pages: int | None = Field(default=None, ge=1)
    outline: tuple[OutlineEntry, ...] = Field(default=(), max_length=200)
    # 导读（附件 digest_job.py）：全文摘要与各段要点（带原文行号），放不进对话的长文档才有；还在生成时 digest_pending
    digest_path: str | None = Field(default=None, pattern=r"^input/[^/]{1,200}$")
    digest_pending: bool = False
    # 用户在编辑器里打开过这个附件：附件只读，打开的是工作目录根上的同名副本（documents/editing.py 的 working_copy），
    # 人可能已经改过。宿主告诉模型要改文档就改这份，别照只读原件另起一份把人改的盖掉
    working_copy_path: str | None = Field(default=None, pattern=r"^[^/]{1,200}$")


class KnowledgeEntry(_Frozen):
    """知识库目录里的一项：全局 id（<知识源>.<知识库>）、名字、截短的描述、能筛的字段（「名字（类型）」）、
    能按次打开的检索方式（knowledge_search 的同名参数：multi_hop 多跳、visual 视觉）。"""

    id: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=300)
    fields: tuple[str, ...] = Field(default=(), max_length=30)
    routes: tuple[Literal["multi_hop", "visual"], ...] = Field(default=(), max_length=2)


class KnowledgeCatalog(_Frozen):
    """本轮可查的知识库目录。宿主照官方技能目录的形态交给模型：列名字、描述和能筛的字段名，
    可选值、标签集由模型按需调 knowledge_bases 取。omitted 是太多没列出来的个数。"""

    bases: tuple[KnowledgeEntry, ...] = Field(default=(), max_length=100)
    omitted: int = Field(default=0, ge=0)


MENTION_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class TurnMention(_Frozen):
    """用户这一轮 @ 的一项，宿主写进这条消息（模型读得到，业务服务投影时取出来显示在气泡上，重新生成时照原样再发）。
    type 是 knowledge_base、folder、document、tag、mcp、skill，或 extension（扩展登记的类型，conversations/mention_kinds.py）；
    id 同 conversations/mentions.py 的 Mention.id（技能就是技能名，宿主写成 /技能名，官方插件据此注入技能；mcp 是服务标识，
    宿主写成「这一轮优先用它的工具」）；base、base_name 是文件、标签所在的知识库；extension 另带 kind（扩展的类型标识）
    与 label（类型名，例如「数据源」，宿主写成「用户点名的数据源：…」）。"""

    type: Literal["knowledge_base", "folder", "document", "tag", "mcp", "skill", "extension"]
    id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    base: str | None = Field(default=None, max_length=200)
    base_name: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, pattern=MENTION_KIND_PATTERN)
    label: str | None = Field(default=None, min_length=1, max_length=20)


class TurnContext(_Frozen):
    """扩展给这一轮补的上下文（例如记忆），两段文字，由扩展写好、转义好，宿主原样包进 <system-reminder> 交给模型：

    - standing（常驻）：会话里一直有效的，宿主照知识库目录的办法发——内容没变不重发（不动前缀缓存），变了追加一份新的，
      从有变成空时追加一句「不再适用」；
    - turn：只和这一轮有关的（例如按本轮提问召回的几条），宿主紧跟在本轮提问后面追加一次；
    - persona：这段对话的身份（例如专家是谁、管什么、怎么回答），宿主放进系统提示里部署人设的那一段（官方的作用域段，只替换
      这个会话的），比追加在对话里的背景分量重，问「你是谁」按它答；空的照部署的默认人设。部署人设后面的通用要求不受影响。"""

    standing: str = Field(default="", max_length=8000)
    turn: str = Field(default="", max_length=4000)
    persona: str = Field(default="", max_length=8000)


# 回答方式的标识：内置 agent、quick，扩展可以登记别的（conversations/answer_modes.py）
ANSWER_MODE_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
# 模型别名（同 models.schemas 的 Alias）：输入框选的模型，宿主按它换这段对话的模型
MODEL_ALIAS_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
EXTENSION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
EXTENSION_STATE_MAX_BYTES = 64 * 1024


class AnswerModeSpec(_Frozen):
    """扩展登记的回答方式这一轮的内容：放行哪些官方全局工具、提问后面加什么指令。内置的两种宿主自己认得，不带它。"""

    tools: tuple[str, ...] = Field(default=(), max_length=64)
    prompt: str = Field(default="", max_length=20_000)


class TurnInput(_Frozen):
    text: str = Field(min_length=1, max_length=200_000)
    mode: Literal["followup", "steer"] = "followup"
    language: Literal["zh", "en"] | None = None  # 用户在聊天页指定的语言；不给时按提问判断（kernel/locale.py）
    # 提问人的时区（IANA 名字，网页取浏览器的）：模型按它算「今天」。不给时宿主用部署时区，名字不合规时宿主也改用部署时区
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    # 回答方式：智能体、只查知识库的快速问答，或扩展登记的。宿主在这条消息进入步骤时切换，会话中途可以换
    answer_mode: str = Field(default="agent", pattern=ANSWER_MODE_PATTERN)
    answer_mode_spec: AnswerModeSpec | None = None  # 扩展登记的方式由业务服务发消息时现算
    attachments: tuple[TurnAttachment, ...] = Field(default=(), max_length=20)
    # 本轮的知识库目录；None = 这轮没算（宿主沿用上一轮的），空目录 = 没有可查的知识库
    knowledge: KnowledgeCatalog | None = None
    mentions: tuple[TurnMention, ...] = Field(default=(), max_length=30)
    # 扩展补的上下文（conversations/turn_context.py）；None = 这轮没算（宿主沿用上一轮的常驻那段，也没有这一轮的那段）
    context: TurnContext | None = None
    # 输入框选的模型别名；宿主按官方会话级选择换（存进会话，之后不带也沿用）。None = 不换
    model: str | None = Field(default=None, pattern=MODEL_ALIAS_PATTERN)
    # 扩展随这一轮交给宿主插件的状态（conversations/turn_state.py），按扩展名分、各自的 JSON，宿主收下就记进会话状态，
    # 扩展自己的宿主插件读。例如 Office 编辑：这段对话此刻在编辑器里开着哪几份（插件据此决定给不给模型编辑器工具）。
    # 在用户设备上执行的对话，设备交给本机宿主之前可以补上自己的（编辑器在那台电脑上）。None = 这轮没算，宿主沿用上一轮的
    extension_state: dict[str, Any] | None = None

    @field_validator("extension_state")
    @classmethod
    def _extension_state(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if len(value) > 16 or any(not EXTENSION_NAME_PATTERN.match(name) for name in value):
            raise ValueError("extension_state 的扩展名不合规")
        if len(json.dumps(value, ensure_ascii=False).encode()) > EXTENSION_STATE_MAX_BYTES:
            raise ValueError("extension_state 太大")
        return value


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
# 宿主事件帧的来源（KernelEvent.epoch）：根会话自己的官方事件是 host、子 Agent 的是 host:<子会话>，游标是官方会话日志的序号，
# 宿主重启后照旧有效（同设备上传的 device、device:<子会话>）。进程内的帧（Agent 状态、逐字输出、目标自动续跑的开关、
# 宿主实例号）来源是 live、不带序号，不落库、不推进续读位置
ROOT_SOURCE = "host"
LIVE_SOURCE = "live"
# 事件流开头宿主给的实例号（宿主重启后变）：只用来判断要不要补发令牌，不当游标
HOST_INSTANCE_KIND = "host/instance"
# 逐字输出的临时帧（官方 agent/assistant-stream 的正文片段）：宿主只推给当前订阅方、不进回放缓冲，
# 游标是当时最后一个，业务服务不落库、不推进续读位置（conversations/live.py）
LIVE_FRAME_KIND = "assistant/stream"


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


class HostModel(_Frozen):
    """推给宿主的一个可选对话模型：宿主据此声明官方模型适配器的路由（id 就是模型网关认的别名）。"""

    id: str = Field(pattern=MODEL_ALIAS_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    vision: bool = False


class BundledSkill(_Frozen):
    """宿主内置的技能（KnowWork 随宿主发的 Word、Excel、PPT 这类）：只给名字与说明，「技能」页与 @ 菜单列出来用。"""

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    user_invocable: bool = True


class CancelResult(_Frozen):
    settled: bool
    status: str


class HostCommand(_Frozen):
    """用户能在输入框敲 / 的一个命令（官方 dsh-commands，插件登记，宿主按会话列出；有几个官方命令宿主不放出来）。"""

    name: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=500)
    hint: str | None = Field(default=None, max_length=200)
    definition_id: str | None = Field(default=None, max_length=200)  # 官方命令的稳定编号，插件自己的没有


class CommandOutcome(_Frozen):
    """执行一行命令的结果（官方 command/done）：不经过模型，文字原样给用户看。"""

    command_id: str = Field(max_length=200)
    kind: Literal["success", "error"]
    text: str | None = Field(default=None, max_length=20000)


class KernelError(Exception):
    """宿主返回的错误。code 是宿主的稳定错误码，业务服务据此决定怎么处理。"""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SessionNotFound(KernelError):
    pass


class CommandUnknown(KernelError):
    """这段会话里没有这个命令（没开对应的插件、名字写错了，或是宿主不放出来的那几个）。"""


class SessionBusy(KernelError):
    pass


class ModelUnknown(KernelError):
    """宿主还没收到过这个模型（刚重启、还没推可选模型）。调用方推一次清单再发。"""


class AgentKernel(Protocol):
    async def open_session(self, spec: SessionSpec) -> str: ...

    async def send(self, session_id: str, turn: TurnInput) -> str: ...

    async def cancel(self, session_id: str) -> CancelResult: ...

    # 用户改了对话标题：写进宿主的官方会话标题（来源是用户，之后官方不再自动改）。会话不在宿主上时抛 SessionNotFound
    async def retitle(self, session_id: str, title: str) -> None: ...

    async def dispose(self, session_id: str) -> None: ...

    async def pending_approvals(self, session_id: str) -> list[PendingApproval]: ...

    async def decide_approval(self, session_id: str, approval_id: str, allow: bool) -> None: ...

    async def pending_questions(self, session_id: str) -> list[PendingQuestion]: ...

    async def answer_question(self, session_id: str, question_id: str, answer: QuestionAnswer) -> None: ...

    async def rotate_model_credential(self, token: str) -> None: ...

    async def rotate_tool_credential(self, session_id: str, token: str) -> None: ...

    async def rotate_skill_credential(self, session_id: str, token: str) -> None: ...

    async def bundled_skills(self) -> tuple[BundledSkill, ...]: ...

    async def sync_models(self, models: tuple[HostModel, ...]) -> None: ...

    async def commands(self, session_id: str) -> tuple[HostCommand, ...]: ...

    async def run_command(self, session_id: str, line: str) -> CommandOutcome: ...

    # 长任务目标的自动续跑此刻开着没有（官方 dsh-goal 只记在宿主内存里、不进会话日志）；没有目标时 False
    async def goal_armed(self, session_id: str) -> bool: ...

    # positions：各来源已经收到的官方序号（来源见 ROOT_SOURCE），宿主从那之后接着给（先补积压、再接实时，不断档）
    def subscribe(self, session_id: str, *, positions: Mapping[str, int] | None = None) -> AsyncIterator[KernelEvent]: ...
