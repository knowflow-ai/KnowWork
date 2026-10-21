"""固定工作流的异常。message 面向用户，不含内部细节；code 是稳定错误码。"""

from typing import Literal

FailureStage = Literal["selection", "permission", "step", "tool", "knowledge", "generation", "delivery"]


class WorkflowError(Exception):
    status = 400

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class RunNotFound(WorkflowError):
    status = 404

    def __init__(self) -> None:
        super().__init__("run_not_found", "运行不存在")


class SkillUnavailable(WorkflowError):
    status = 404

    def __init__(self) -> None:
        super().__init__("skill_not_available", "技能不存在或对你不可用")


class NotAWorkflow(WorkflowError):
    status = 422

    def __init__(self) -> None:
        super().__init__("not_a_workflow", "这个技能的发布版本没有固定工作流")


class InputsInvalid(WorkflowError):
    status = 422


class RunConflict(WorkflowError):
    status = 409


class Forbidden(WorkflowError):
    status = 403

    def __init__(self) -> None:
        super().__init__("forbidden", "无权限执行该操作")


class StepFailed(Exception):
    """一步执行失败，按流程规则终止运行（可选步骤记为跳过）。message 可以给用户看。"""

    def __init__(self, code: str, message: str, stage: FailureStage = "step") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


class StepInterrupted(Exception):
    """一步没能完成、但不是这一步的错（宿主重启、同一动作还在执行）：运行标为中断，之后可续跑。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
