"""单元测试用的内核替身：按脚本回放订阅，记录调用。"""

import asyncio

from knowwork_server.kernel.contract import (
    CancelResult,
    KernelEvent,
    PendingApproval,
    PendingQuestion,
    Question,
    QuestionOption,
)

HOLD = object()  # 保持连接不再发帧，直到被取消


def frame(cursor: int, kind: str = "session/event", data=None, *, epoch: str = "e1", time: int = 1) -> KernelEvent:
    return KernelEvent(epoch=epoch, cursor=cursor, kind=kind, time=time, data=data)


def session_event(cursor: int, type_: str, data=None, *, epoch: str = "e1") -> KernelEvent:
    return frame(cursor, "session/event", {"type": type_, "seq": cursor, "time": 1, "data": data or {}}, epoch=epoch)


def status(cursor: int, value: str, *, epoch: str = "e1") -> KernelEvent:
    return frame(cursor, "agent/status", {"status": value}, epoch=epoch)


class FakeKernel:
    def __init__(self, subscriptions=None) -> None:
        # 每次 subscribe 取出一个脚本；脚本是帧 / 异常 / HOLD 组成的列表，用完即正常结束本次订阅
        self.subscriptions: list[list] = list(subscriptions or [])
        self.subscribe_calls: list[tuple[int, str | None]] = []
        self.opened: list = []
        self.sent: list = []
        self.open_effects: list = []  # 依次作用于 open_session：异常则抛出
        self.send_effects: list = []  # 依次作用于 send：异常则抛出
        self.rotated: list[str] = []
        self.tool_rotations: list[tuple[str, str]] = []
        self.skill_rotations: list[tuple[str, str]] = []
        self.disposed: list[str] = []
        self.dispose_effects: list = []
        self.tool_rotation_effects: list = []  # 依次作用于 rotate_tool_credential：异常则抛出
        self.cancelled: list[str] = []
        self.answered: list[tuple[str, str, object]] = []
        self.answer_effects: list = []  # 依次作用于 answer_question：异常则抛出

    async def subscribe(self, session_id, *, after=0, epoch=None):
        self.subscribe_calls.append((after, epoch))
        script = self.subscriptions.pop(0) if self.subscriptions else [HOLD]
        for item in script:
            if item is HOLD:
                await asyncio.Event().wait()
            elif isinstance(item, BaseException):
                raise item
            else:
                yield item

    async def open_session(self, spec):
        effect = self.open_effects.pop(0) if self.open_effects else None
        await asyncio.sleep(0)
        if isinstance(effect, BaseException):
            raise effect
        self.opened.append(spec)
        return "e1"

    async def send(self, session_id, turn):
        effect = self.send_effects.pop(0) if self.send_effects else None
        await asyncio.sleep(0)
        if isinstance(effect, BaseException):
            raise effect
        self.sent.append(turn)
        return f"m{len(self.sent)}"

    async def cancel(self, session_id):
        self.cancelled.append(session_id)
        return CancelResult(settled=True, status="idle")

    async def dispose(self, session_id):
        effect = self.dispose_effects.pop(0) if self.dispose_effects else None
        self.disposed.append(session_id)
        if isinstance(effect, BaseException):
            raise effect

    async def pending_approvals(self, session_id):
        return [PendingApproval(id="a1", tool_name="t")]

    async def decide_approval(self, session_id, approval_id, allow):
        return None

    async def pending_questions(self, session_id):
        question = Question(id="fmt", question="格式？", options=(QuestionOption(label="PDF"),))
        return [PendingQuestion(id="call_1", state="open", questions=(question,))]

    async def answer_question(self, session_id, question_id, answer):
        effect = self.answer_effects.pop(0) if self.answer_effects else None
        await asyncio.sleep(0)
        if isinstance(effect, BaseException):
            raise effect
        self.answered.append((session_id, question_id, answer))

    async def rotate_model_credential(self, token):
        self.rotated.append(token)

    async def rotate_tool_credential(self, session_id, token):
        effect = self.tool_rotation_effects.pop(0) if self.tool_rotation_effects else None
        if isinstance(effect, BaseException):
            raise effect
        self.tool_rotations.append((session_id, token))

    async def rotate_skill_credential(self, session_id, token):
        self.skill_rotations.append((session_id, token))


def token_actor(token: str, user_id: str = "dev-user", workspace_id: str = "default"):
    """测试用调用者依赖：固定令牌对应固定用户。生产装配用账号体系，见 app.py。"""
    from typing import Annotated

    from fastapi import Header, HTTPException

    from knowwork_server.conversations.api import Actor, bearer_matches

    async def dependency(authorization: Annotated[str | None, Header()] = None) -> Actor:
        if not bearer_matches(authorization, token):
            raise HTTPException(status_code=401, detail="未登录")
        return Actor(user_id=user_id, workspace_id=workspace_id)

    return dependency
