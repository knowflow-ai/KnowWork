"""会话回调令牌的下发与续期：工具桥（受众 tools）与技能提供方（受众 skills，见 skills.internal_api）共用这一套，
差别只在续期时调宿主的哪个接口（rotate）。

令牌有效期短（默认 900 秒）。打开或恢复宿主会话时签一张新的随会话下发；之后由续期循环给距上次签发
满 interval 的会话换新令牌，每轮开始前再用 ensure_fresh() 兜一次。间隔按墙上时钟算，原因同模型令牌
（gateway/credential_rotation.py）：asyncio 的单调时钟在 macOS 睡眠时不走，令牌有效期却是墙上时间。

几条约束：
- 每次追踪记一个代际号。续期取快照时带上代际号，只有代际号没变才因"宿主说会话不存在"而停止追踪，
  否则会把续期途中刚被恢复（重新追踪）的会话误删，令牌过期后工具调用一律 401 且不会自愈。
- 一轮续期有界并发，签发与轮换各自有单次超时，宿主变慢时一轮耗时不随会话数线性增长。
- 签发函数可以拒绝（ToolAccessRevoked，例如成员已被移出工作空间）：续期时据此停止追踪并取消该会话
  正在跑的回合；建会话或恢复时直接拒绝打开。其他签发失败统一成 ToolCredentialUnavailable。
"""

import asyncio
import contextlib
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ..kernel.contract import AgentKernel, SessionNotFound
from .models import Conversation

log = logging.getLogger(__name__)

TICK_SECONDS = 15.0
TokenIssuer = Callable[[Conversation], Awaitable[str]]
Rotate = Callable[[str, str], Awaitable[None]]
# 返回 True 放行、False 拒绝（权限已收回）、None 表示当下判不了（判定器不可用），按签发失败处理
MembershipCheck = Callable[[Conversation], bool | None]


class ToolAccessRevoked(Exception):
    """签发方拒绝：会话所属用户已无权使用该工作空间。"""

    def __init__(self, conversation_id: str) -> None:
        super().__init__("tool access revoked")
        self.conversation_id = conversation_id


class ToolCredentialUnavailable(Exception):
    """签发失败（签发方异常或超时）。原因只写日志，不进消息。"""

    def __init__(self, conversation_id: str) -> None:
        super().__init__("tool credential unavailable")
        self.conversation_id = conversation_id


def require_membership(issue: TokenIssuer, is_member: MembershipCheck) -> TokenIssuer:
    """给签发函数加成员身份复核：明确不通过就不签发，抛 ToolAccessRevoked。

    判不了（None）或复核本身出错都不签发，但按签发失败处理：不能把权限服务故障当成权限被收回去取消回合。
    """

    async def guarded(conversation: Conversation) -> str:
        verdict = is_member(conversation)
        if verdict is None:
            raise ToolCredentialUnavailable(conversation.id)
        if verdict is not True:
            raise ToolAccessRevoked(conversation.id)
        return await issue(conversation)

    return guarded


@dataclass(frozen=True)
class _Tracked:
    generation: int
    conversation: Conversation
    issued_at: float  # 墙上时钟：上次签发（打开、恢复或续期成功）的时间


class ToolCredentials:
    """rotate 默认续期工具桥令牌；技能令牌传 kernel.rotate_skill_credential，label 用于日志与任务名。"""

    def __init__(self, kernel: AgentKernel, issue: TokenIssuer, *, interval: float = 300.0,
                 call_timeout: float = 10.0, concurrency: int = 8, rotate: Rotate | None = None,
                 label: str = "tool", clock: Callable[[], float] = time.time) -> None:
        if call_timeout <= 0:
            raise ValueError("call_timeout must be positive")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._kernel = kernel
        self._issue = issue
        self._rotate = rotate or kernel.rotate_tool_credential
        self._label = label
        self._interval = interval
        self._tick = min(TICK_SECONDS, interval / 4)
        self._clock = clock
        self._call_timeout = call_timeout
        self._concurrency = concurrency
        self._generations = itertools.count(1)
        self._open: dict[str, _Tracked] = {}
        self._task: asyncio.Task | None = None

    async def token_for(self, conversation: Conversation) -> str:
        """签发随建会话或恢复下发的令牌。拿不到就抛出：没有工具令牌的会话不应打开。"""
        try:
            return await asyncio.wait_for(self._issue(conversation), timeout=self._call_timeout)
        except ToolAccessRevoked:
            raise
        except Exception as error:
            log.warning("tool credential issue failed for %s: %s", conversation.id, type(error).__name__,
                        exc_info=True)
            raise ToolCredentialUnavailable(conversation.id) from error

    def track(self, conversation: Conversation) -> int:
        """开始（或重新）追踪会话，返回本次追踪的代际号。调用方刚随打开或恢复下发过令牌。"""
        generation = next(self._generations)
        self._open = {**self._open, conversation.id: _Tracked(generation, conversation, self._clock())}
        return generation

    def forget(self, conversation_id: str, *, generation: int | None = None) -> None:
        """停止追踪。给了代际号时，只有当前追踪仍是那一代才删除。"""
        current = self._open.get(conversation_id)
        if current is None or (generation is not None and current.generation != generation):
            return
        self._open = {cid: t for cid, t in self._open.items() if cid != conversation_id}

    def tracked(self) -> set[str]:
        return set(self._open)

    async def renew_all(self) -> None:
        await self._renew(tuple(self._open.values()))

    async def renew_due(self) -> None:
        """只续距上次签发已满一个间隔的会话（续期循环每个小间隔调一次）。"""
        now = self._clock()
        await self._renew(tuple(e for e in self._open.values() if now - e.issued_at >= self._interval))

    async def ensure_fresh(self, conversation_id: str) -> None:
        """每轮开始前调用：这个会话的令牌距上次签发已满一个间隔（例如电脑刚从睡眠醒来）就立即续上。"""
        entry = self._open.get(conversation_id)
        if entry is not None and self._clock() - entry.issued_at >= self._interval:
            await self._renew_one(entry)

    async def _renew(self, snapshot: tuple[_Tracked, ...]) -> None:
        if not snapshot:
            return
        gate = asyncio.Semaphore(self._concurrency)

        async def bounded(entry: _Tracked) -> None:
            async with gate:
                await self._renew_one(entry)

        await asyncio.gather(*(bounded(entry) for entry in snapshot))

    async def _renew_one(self, entry: _Tracked) -> None:
        conversation_id = entry.conversation.id
        try:
            token = await asyncio.wait_for(self._issue(entry.conversation), timeout=self._call_timeout)
            await asyncio.wait_for(self._rotate(conversation_id, token), timeout=self._call_timeout)
            self._renewed(entry)
        except ToolAccessRevoked:
            self.forget(conversation_id, generation=entry.generation)
            await self._cancel_turn(conversation_id)
        except SessionNotFound:
            self.forget(conversation_id, generation=entry.generation)
        except Exception:
            log.warning("%s credential renewal failed for %s", self._label, conversation_id, exc_info=True)

    def _renewed(self, entry: _Tracked) -> None:
        current = self._open.get(entry.conversation.id)
        if current is not None and current.generation == entry.generation:
            self._open = {**self._open, entry.conversation.id: replace(current, issued_at=self._clock())}

    async def _cancel_turn(self, conversation_id: str) -> None:
        log.info("tool access revoked for %s; cancelling its turn", conversation_id)
        try:
            await asyncio.wait_for(self._kernel.cancel(conversation_id), timeout=self._call_timeout)
        except SessionNotFound:
            pass
        except Exception:
            log.warning("cancel after tool access revocation failed for %s", conversation_id, exc_info=True)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name=f"{self._label}-credential-renewal")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick)
            try:
                await self.renew_due()
            except Exception:
                log.exception("tool credential renewal round failed")
