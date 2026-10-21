"""MinerU 3.x HTTP 客户端：把 PDF、Word、PPT 解析成 Markdown。

协议照 KnowFlow 的 MinerUFastAPIAdapter（knowflow/server/services/knowledgebases/mineru_parse/fastapi_adapter.py）：
- 优先异步任务：POST /tasks（multipart，字段 files）→ 轮询 GET /tasks/{id} → GET /tasks/{id}/result；
- /tasks 返回 404/405/501（老版本没有异步接口）时回退到同步 POST /file_parse；
- 结果形如 {"results": {"<文件名>": {"md_content": "..."}}}。
只要 Markdown，不取 middle_json 与图片，减小传输量。

MinerU 4.0 删掉了 /tasks 与 /file_parse（改为 /v1/uploads + /v1/parse/jobs），两个接口都 404 时
按「解析服务版本不支持」失败、不重试，不去猜新协议。

失败分类决定是否重试：格式不支持、文件损坏不重试；服务超时、服务暂不可用可重试。
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

SUCCESS_STATES = frozenset({"success", "completed", "done", "finished", "succeeded"})
FAILURE_STATES = frozenset({"failed", "error", "cancelled", "canceled"})
_NO_ASYNC = frozenset({404, 405, 501})
_UNAVAILABLE = frozenset({429, 502, 503, 504})


class MinerUError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def unsupported() -> MinerUError:
    return MinerUError("unsupported_format", "解析服务不支持这种文件格式", retryable=False)


def corrupt() -> MinerUError:
    return MinerUError("file_corrupt", "文档解析失败，文件可能已损坏或加密", retryable=False)


def timed_out() -> MinerUError:
    return MinerUError("timeout", "文档解析服务超时", retryable=True)


def unavailable() -> MinerUError:
    return MinerUError("service_unavailable", "文档解析服务暂时不可用", retryable=True)


def bad_protocol() -> MinerUError:
    return MinerUError("parser_protocol", "文档解析服务返回了无法识别的结果", retryable=False)


def _status_error(status: int) -> MinerUError:
    if status == 415:
        return unsupported()
    if status in (400, 413, 422) or status == 500:
        return corrupt()
    if status in _UNAVAILABLE:
        return unavailable()
    return bad_protocol()


@dataclass(frozen=True)
class MinerUOptions:
    backend: str = "pipeline"
    lang: str = "ch"
    parse_timeout_seconds: float = 600.0
    poll_interval_seconds: float = 2.0
    poll_max_interval_seconds: float = 10.0

DEFAULT_OPTIONS = MinerUOptions()


class MinerUClient:
    """http 由调用方给（base_url 已设好），便于复用连接池与测试注入 MockTransport。"""

    def __init__(self, http: httpx.AsyncClient, options: MinerUOptions = DEFAULT_OPTIONS, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._http = http
        self._options = options
        self._sleep = sleep
        self._clock = clock

    async def parse(self, filename: str, data: bytes, mime: str) -> str:
        try:
            return await self._parse(filename, data, mime)
        except MinerUError:
            raise
        except httpx.TimeoutException as error:
            raise timed_out() from error
        except httpx.TransportError as error:
            raise unavailable() from error
        except (ValueError, KeyError, TypeError) as error:  # JSON 不合法或字段缺失
            log.warning("mineru returned malformed payload: %s", type(error).__name__)
            raise bad_protocol() from error

    def _form(self) -> dict[str, Any]:
        return {"backend": self._options.backend, "parse_method": "auto", "lang_list": [self._options.lang],
                "formula_enable": "true", "table_enable": "true", "return_md": "true",
                "return_middle_json": "false", "return_images": "false"}

    async def _parse(self, filename: str, data: bytes, mime: str) -> str:
        files = {"files": (filename, data, mime)}
        response = await self._http.post("/tasks", files=files, data=self._form())
        if response.status_code in _NO_ASYNC:
            return await self._parse_sync(filename, data, mime)
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        task_id = response.json().get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise bad_protocol()
        await self._wait(task_id)
        result = await self._http.get(f"/tasks/{task_id}/result")
        if result.status_code >= 400:
            raise _status_error(result.status_code)
        return _markdown(result.json())

    async def _parse_sync(self, filename: str, data: bytes, mime: str) -> str:
        files = {"files": (filename, data, mime)}
        response = await self._http.post("/file_parse", files=files, data=self._form())
        if response.status_code == 404:
            raise MinerUError("parser_version_unsupported", "文档解析服务版本不受支持（需要 MinerU 3.x）",
                              retryable=False)
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        return _markdown(response.json())

    async def _wait(self, task_id: str) -> None:
        deadline = self._clock() + self._options.parse_timeout_seconds
        interval = self._options.poll_interval_seconds
        while self._clock() < deadline:
            response = await self._http.get(f"/tasks/{task_id}")
            if response.status_code == 404:  # 任务只在 MinerU 内存里，服务重启后就没了：整次重来
                raise unavailable()
            if response.status_code >= 400:
                raise _status_error(response.status_code)
            state = str(response.json().get("status") or "").strip().lower()
            if state in SUCCESS_STATES:
                return
            if state in FAILURE_STATES:
                raise corrupt()
            await self._sleep(interval)
            interval = min(self._options.poll_max_interval_seconds, interval * 1.5)
        raise timed_out()


def _markdown(payload: Any) -> str:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not results:
        raise bad_protocol()
    first = next(iter(results.values()))
    content = first.get("md_content") if isinstance(first, dict) else None
    if not isinstance(content, str):
        raise bad_protocol()
    return content


def build_mineru_client(base_url: str, options: MinerUOptions, *, request_timeout: float = 60.0,
                        transport: httpx.AsyncBaseTransport | None = None) -> tuple[MinerUClient, httpx.AsyncClient]:
    """返回客户端与底层 http（调用方负责 aclose）。"""
    http = httpx.AsyncClient(base_url=base_url, timeout=request_timeout, transport=transport)
    return MinerUClient(http, options), http
