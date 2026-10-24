"""任务类型与失败。任务类型是入队方（api）和执行方（worker）共用的唯一定义：池、重试、租约、超时都写在这里，
两边不会各配一套（WeKnora 的 queueDefinitions 同理）。
"""

from dataclasses import dataclass, field
from typing import Literal

ErrorClass = Literal["file", "service", "internal"]

# 服务问题的默认退避：1 分钟、5 分钟、15 分钟、1 小时、4 小时。解析服务、模型这类外部服务出问题往往要人去处理，
# 前几次间隔短，抖一下就过去的能很快恢复；之后拉长，免得对一个挂掉的服务反复施压
SERVICE_DELAYS = (60.0, 300.0, 900.0, 3600.0, 14400.0)
# 内部错误（代码异常）最多再试两次，间隔短：多半是瞬时问题，重试多了只是重复报同一个错
INTERNAL_DELAYS = (30.0, 120.0)
MAX_DETAIL = 8000


@dataclass(frozen=True)
class JobKind:
    name: str
    pool: str = "core"
    max_tries: int = 3            # 总尝试次数，含第一次；租约过期被接手也算一次
    lease_seconds: float = 300.0  # 执行中每 lease/3 秒续一次；worker 崩溃后最多这么久被别的 worker 接手
    timeout_seconds: float = 1800.0  # 单次执行的总时限，处理函数可以按实际工作量延长（JobContext.extend_deadline）
    retry_delays: tuple[float, ...] = SERVICE_DELAYS
    priority: int = 0             # 越大越先领
    version: int = 1              # 载荷格式版本，改了载荷就加一；worker 只领不高于自己版本的任务
    cancelable: bool = True       # 删除、清理类任务做到一半停下会留下半截状态，设成 False
    label: str = ""               # 运行面板上显示的中文名，空着显示 name


class JobError(Exception):
    """处理函数用它报告可以分类的失败。

    - file：文件本身的问题（格式不支持、加密、损坏、超限），不重试；
    - service：外部服务的问题（不可用、超时、额度用尽、配置错），按退避重试；
    - internal：代码异常（未捕获的异常自动归到这里），最多再试两次。
    retryable 显式给出时以它为准（例如「没有配置解析服务」是服务类，但重试没有意义）。
    retry_after 给出时用它代替退避表（例如锁冲突，固定短间隔就能成功）。
    """

    def __init__(self, code: str, message: str, *, error_class: ErrorClass = "service",
                 retryable: bool | None = None, retry_after: float | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_class = error_class
        self.retryable = error_class != "file" if retryable is None else retryable
        self.retry_after = retry_after
        self.detail = detail


class JobCanceled(Exception):
    """任务被取消（JobContext 在检查点抛出）。"""


class JobReleased(Exception):
    """worker 正在停机，任务退回排队（JobContext 在检查点抛出），不算一次尝试。"""


class JobLost(Exception):
    """领取令牌已经不是自己的（租约过期被别人接手了），停手，什么都不写。"""


@dataclass(frozen=True)
class JobFailure:
    """最终失败时交给 on_give_up 的说明：message 是给用户看的中文。"""

    code: str
    message: str
    error_class: ErrorClass
    detail: str | None = field(default=None)
