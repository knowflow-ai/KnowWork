"""模型网关查敏感词用的客户端：回调业务服务内部端口上的内容安全接口（宿主插件用的同一套），带着专属宿主的模型令牌。

词表和判断只在业务服务，网关进程不读词表、不记审计（命中由业务服务记）。网关这边只缓存两样：
- 每个空间的策略摘要，几秒（同宿主插件）；
- 查过、没命中的片段摘要：同一段对话每次调模型都带着整段历史，查过的不再送。摘要按（空间，策略版本，方向）区分，
  管理员改了词表版本就变，旧的自然不再命中。
"""

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

POLICY_SECONDS = 5.0
MAX_CLEAN = 20_000
# 业务服务一次最多收 256 段、200 万字（content_safety/api.py），这里分批送，留些余量
MAX_BATCH_SEGMENTS = 200
MAX_BATCH_CHARS = 1_000_000
TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class CheckUnavailable(Exception):
    """检查接口不通或答非所问：开了检查的不放行。"""


class CheckRefused(Exception):
    """业务服务不认这个人了（不再是空间成员）：这台宿主也不该再调模型。"""


@dataclass(frozen=True)
class GatePolicy:
    input: bool
    output: bool
    holdback: int
    version: int


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    message: str = ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _batches(segments: Sequence[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for segment in segments:
        # 单段超过上限的照样单独送：业务服务会回 413，按查不成处理（不放行），不会悄悄放过
        if current and (len(current) >= MAX_BATCH_SEGMENTS or size + len(segment) > MAX_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(segment)
        size += len(segment)
    if current:
        batches.append(current)
    return batches


class ContentCheckClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """http 不能带默认鉴权头（每次带调用方的令牌）；base_url 是业务服务的 /internal/extensions/content-safety。"""
        self._http = http
        self._base = base_url.rstrip("/")
        self._clock = clock
        self._policies: dict[str, tuple[GatePolicy, float]] = {}
        self._clean: OrderedDict[tuple[str, int, str, str], None] = OrderedDict()

    async def _call(self, method: str, path: str, authorization: str, payload: dict | None = None) -> dict:
        try:
            response = await self._http.request(method, self._base + path, json=payload, timeout=TIMEOUT,
                                                headers={"authorization": authorization})
        except httpx.HTTPError as error:
            raise CheckUnavailable(type(error).__name__) from error
        if response.status_code in (401, 403):
            raise CheckRefused(str(response.status_code))
        if response.status_code != 200:
            raise CheckUnavailable(f"status {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise CheckUnavailable("not json") from error
        if not isinstance(body, dict):
            raise CheckUnavailable("not an object")
        return body

    async def policy(self, workspace_id: str, authorization: str) -> GatePolicy:
        cached = self._policies.get(workspace_id)
        if cached is not None and self._clock() - cached[1] < POLICY_SECONDS:
            return cached[0]
        body = await self._call("GET", "/policy", authorization)
        try:
            fresh = GatePolicy(input=bool(body["input"]), output=bool(body["output"]),
                               holdback=max(0, int(body["holdback"])), version=int(body["version"]))
        except (KeyError, TypeError, ValueError) as error:
            raise CheckUnavailable("bad policy") from error
        self._policies[workspace_id] = (fresh, self._clock())
        return fresh

    async def check(self, workspace_id: str, authorization: str, policy: GatePolicy, *, direction: str,
                    segments: Sequence[str], tool: str | None = None, remember: bool = False) -> Verdict:
        """remember：没命中的片段记下来，下次同样的不再送（请求里的历史用；流式输出的每一段都不一样，不记）。"""
        keys = {segment: (workspace_id, policy.version, direction, _digest(segment)) for segment in segments if segment}
        fresh = [segment for segment, key in keys.items() if key not in self._clean]
        for batch in _batches(fresh):
            payload = {"direction": direction, "segments": batch, **({"tool": tool[:256]} if tool else {})}
            body = await self._call("POST", "/check", authorization, payload)
            if body.get("blocked") is True:
                return Verdict(blocked=True, message=str(body.get("message") or ""))
            if body.get("blocked") is not False:
                raise CheckUnavailable("bad verdict")
        if remember:
            for segment in fresh:
                self._clean[keys[segment]] = None
            while len(self._clean) > MAX_CLEAN:
                self._clean.popitem(last=False)
        return Verdict(blocked=False)
