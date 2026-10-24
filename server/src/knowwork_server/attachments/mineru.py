"""MinerU 3.x HTTP 客户端：把 PDF、Word、PPT 解析成 Markdown。

协议照 KnowFlow 的 MinerUFastAPIAdapter（knowflow/server/services/knowledgebases/mineru_parse/fastapi_adapter.py）：
- 优先异步任务：POST /tasks（multipart，字段 files）→ 轮询 GET /tasks/{id} → GET /tasks/{id}/result；
- /tasks 返回 404/405/501（老版本没有异步接口）时回退到同步 POST /file_parse；
- 结果形如 {"results": {"<文件名>": {"md_content": "...", "middle_json": ..., "images": {...}}}}。
附件只要 Markdown（parse），不取 middle_json 与图片，减小传输量；知识库要版面与图片（parse_full），
两边共用这一个客户端和同一套失败分类。

MinerU 4.0 删掉了 /tasks 与 /file_parse（改为 /v1/uploads + /v1/parse/jobs），两个接口都 404 时
按「解析服务版本不支持」失败、不重试，不去猜新协议。

失败分类决定是否重试：格式不支持、文件损坏不重试；服务超时、服务暂不可用可重试。
异步任务状态为 failed 时不能一律当成文件坏了：服务端配置错（例如后端缺 GPU 设备、
VLM 服务连不上）也报 failed。错误信息里看得出文件问题的才不重试，其余按服务问题重试，重试用尽后
给用户的话同时提到两种可能。
"""

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

SUCCESS_STATES = frozenset({"success", "completed", "done", "finished", "succeeded"})
FAILURE_STATES = frozenset({"failed", "error", "cancelled", "canceled"})
# MinerU 任务失败信息里表示「文件本身有问题」的片段（小写比较）
_FILE_HINTS = ("password", "encrypt", "corrupt", "damaged", "broken", "invalid pdf", "not a pdf", "no pages",
               "cannot open", "failed to open", "eof marker", "xref", "bad zip", "not a zip", "加密", "损坏", "无法打开")
# 表示「解析服务自己的配置或资源有问题」的片段：一样重试，但提示管理员检查解析服务
_SERVICE_HINTS = ("device", "cuda", "out of memory", "oom", "connection", "server_url", "no module", "model")
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


def task_failure(payload: Any) -> MinerUError:
    """异步任务状态为 failed 时按错误信息分类。原始信息只写日志，不给用户看。"""
    fields = payload if isinstance(payload, dict) else {}
    text = str(fields.get("error") or fields.get("message") or fields.get("msg") or "")[:500]
    lowered = text.lower()
    log.warning("mineru task failed: %s", text or "（没有错误信息）")
    if any(hint in lowered for hint in _FILE_HINTS):
        return corrupt()
    if any(hint in lowered for hint in _SERVICE_HINTS):
        return MinerUError("parser_misconfigured", "文档解析服务出了问题，请联系管理员检查解析服务", retryable=True)
    return MinerUError("parse_failed", "文档解析失败：文件可能有问题，也可能是解析服务异常", retryable=True)


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
    effort: str | None = None      # 3.x 的 hybrid 后端：high 时版面与表格更细
    image_analysis: bool = False   # 图片解析增强：打开才给图片写描述、把图表还原成数据表
    # VLM 推理服务（sglang / vllm，常见是 30000 端口）：*-http-client 后端由 MinerU 去访问它，每次请求带上（照 KnowFlow）
    server_url: str | None = None


# 要 VLM 推理服务的后端；effort、image_analysis 只对 hybrid 系列有用（MinerU 官方文档：Adapted only for hybrid backend）
HTTP_CLIENT_BACKENDS = frozenset({"vlm-http-client", "hybrid-http-client"})
# 管理后台「解析模式」能强制的后端（auto 是不强制，按用途：知识库 hybrid、附件 pipeline）。hybrid 自动判断版面
# 有时效果不好，所以开放整页交给 VLM 的 vlm-http-client；*-engine 后端在 MinerU 进程里跑模型，没配设备就失败，不开放
CHOOSABLE_BACKENDS = ("auto", "vlm-http-client", "hybrid-http-client", "pipeline")
HYBRID_BACKENDS = frozenset({"hybrid-engine", "hybrid-auto-engine", "hybrid-http-client"})


def usable(options: MinerUOptions) -> MinerUOptions:
    """要 VLM 服务的后端却没配地址时退回 pipeline：精度低一些，但解析得了（不然 MinerU 直接报错）。"""
    if options.backend in HTTP_CLIENT_BACKENDS and not options.server_url:
        return replace(options, backend="pipeline", effort=None)
    return options


@dataclass(frozen=True)
class MinerUResult:
    markdown: str
    middle: dict | None              # middle_json（版面、块坐标、标题、表格 HTML、图片描述）
    images: dict[str, bytes]         # 图片文件名 → 内容，middle_json 里的 image_path 指向这里的名字

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
        return (await self.parse_full(filename, data, mime, layout=False)).markdown

    async def parse_full(self, filename: str, data: bytes, mime: str, *, layout: bool = True,
                         image_analysis: bool | None = None) -> MinerUResult:
        """layout 为真时连同 middle_json 与图片一起取回；image_analysis 不给就用客户端的默认设置。"""
        form = self._form(layout, self._options.image_analysis if image_analysis is None else image_analysis)
        try:
            return await self._parse(filename, data, mime, form)
        except MinerUError:
            raise
        except httpx.TimeoutException as error:
            raise timed_out() from error
        except httpx.TransportError as error:
            raise unavailable() from error
        except (ValueError, KeyError, TypeError) as error:  # JSON 不合法或字段缺失
            log.warning("mineru returned malformed payload: %s", type(error).__name__)
            raise bad_protocol() from error

    def _form(self, layout: bool, image_analysis: bool) -> dict[str, Any]:
        flag = "true" if layout else "false"
        form = {"backend": self._options.backend, "parse_method": "auto", "lang_list": [self._options.lang],
                "formula_enable": "true", "table_enable": "true", "return_md": "true",
                "return_middle_json": flag, "return_images": flag}
        hybrid = self._options.backend in HYBRID_BACKENDS
        if self._options.backend in HTTP_CLIENT_BACKENDS and self._options.server_url:
            form["server_url"] = self._options.server_url
        if hybrid and self._options.effort:
            form["effort"] = self._options.effort
        if hybrid and image_analysis:
            form["image_analysis"] = "true"
        return form

    async def _parse(self, filename: str, data: bytes, mime: str, form: dict[str, Any]) -> MinerUResult:
        files = {"files": (filename, data, mime)}
        response = await self._http.post("/tasks", files=files, data=form)
        if response.status_code in _NO_ASYNC:
            return await self._parse_sync(filename, data, mime, form)
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        task_id = response.json().get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise bad_protocol()
        await self._wait(task_id)
        result = await self._http.get(f"/tasks/{task_id}/result")
        if result.status_code >= 400:
            raise _status_error(result.status_code)
        return _result(result.json())

    async def _parse_sync(self, filename: str, data: bytes, mime: str, form: dict[str, Any]) -> MinerUResult:
        files = {"files": (filename, data, mime)}
        response = await self._http.post("/file_parse", files=files, data=form)
        if response.status_code == 404:
            raise MinerUError("parser_version_unsupported", "文档解析服务版本不受支持（需要 MinerU 3.x）",
                              retryable=False)
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        return _result(response.json())

    async def _wait(self, task_id: str) -> None:
        deadline = self._clock() + self._options.parse_timeout_seconds
        interval = self._options.poll_interval_seconds
        while self._clock() < deadline:
            response = await self._http.get(f"/tasks/{task_id}")
            if response.status_code == 404:  # 任务只在 MinerU 内存里，服务重启后就没了：整次重来
                raise unavailable()
            if response.status_code >= 400:
                raise _status_error(response.status_code)
            payload = response.json()
            state = str(payload.get("status") or "").strip().lower()
            if state in SUCCESS_STATES:
                return
            if state in FAILURE_STATES:
                raise task_failure(payload)
            await self._sleep(interval)
            interval = min(self._options.poll_max_interval_seconds, interval * 1.5)
        raise timed_out()


def _result(payload: Any) -> MinerUResult:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not results:
        raise bad_protocol()
    first = next(iter(results.values()))
    content = first.get("md_content") if isinstance(first, dict) else None
    if not isinstance(content, str):
        raise bad_protocol()
    return MinerUResult(markdown=content, middle=_middle(first.get("middle_json")),
                        images=_images(first.get("images")))


def _middle(value: Any) -> dict | None:
    """MinerU 有时把 middle_json 当字符串返回，有时是对象（实测）。"""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise bad_protocol()
    return value


def _images(value: Any) -> dict[str, bytes]:
    """{"<文件名>": "data:image/jpeg;base64,..."}；只留文件名，防止名字里带路径。"""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise bad_protocol()
    images = {}
    for name, data in value.items():
        payload = data.split(",", 1)[1] if isinstance(data, str) and data.startswith("data:") else data
        try:
            images[str(name).rsplit("/", 1)[-1]] = base64.b64decode(payload, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise bad_protocol() from error
    return images


class GatedMinerU:
    """MinerU 同时只能跑几个解析：每次调用先占一个名额（jobs.gate，所有 worker 进程共用名额池 mineru）。"""

    GATE = "mineru"

    def __init__(self, client: MinerUClient, gate, limit: int) -> None:
        self._client = client
        self._gate = gate
        self._limit = limit

    async def parse(self, filename: str, data: bytes, mime: str) -> str:
        async with self._gate.hold(self.GATE, self._limit):
            return await self._client.parse(filename, data, mime)

    async def parse_full(self, filename: str, data: bytes, mime: str, *, layout: bool = True,
                         image_analysis: bool | None = None) -> MinerUResult:
        async with self._gate.hold(self.GATE, self._limit):
            return await self._client.parse_full(filename, data, mime, layout=layout, image_analysis=image_analysis)


class ParserSettingsSource(Protocol):
    """按空间给出当前的解析服务配置（docparse.DocumentParsers 满足）；None 是没配。返回的配置要有
    base_url、api_key、lang、max_concurrency、server_url（VLM 推理服务，可空）这几项。"""

    async def current(self, workspace_id: str) -> Any: ...


MinerU = MinerUClient | GatedMinerU


class MinerUProvider:
    """解析时按空间取 MinerU 客户端：配置在管理后台改，几秒内生效，不用重启（docparse 的缓存时长）。

    - http 连接按（地址, 令牌）复用，配置改了自然换一个，旧的留到关停时一起关（改配置很少，攒不了几个）；
    - 令牌有值时放在 Authorization: Bearer 里（MinerU 前面有网关要令牌的部署）；
    - gate 给了时每次调用先过闸门，名额按配置里的「同时解析上限」。
    """

    def __init__(self, settings: ParserSettingsSource, gate=None, *, request_timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._gate = gate
        self._timeout = request_timeout
        self._transport = transport
        self._http: dict[tuple[str, str | None], httpx.AsyncClient] = {}

    async def configured(self, workspace_id: str) -> bool:
        return await self._settings.current(workspace_id) is not None

    async def get(self, workspace_id: str, options: MinerUOptions) -> MinerU | None:
        config = await self._settings.current(workspace_id)
        if config is None:
            return None
        key = (config.base_url, config.api_key)
        http = self._http.get(key)
        if http is None:
            headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else None
            http = httpx.AsyncClient(base_url=config.base_url, timeout=self._timeout, headers=headers,
                                     transport=self._transport)
            self._http = {**self._http, key: http}
        forced = getattr(config, "backend", None)  # 管理后台强制的解析模式；为空是「自动」，按调用方的默认
        chosen = replace(options, lang=config.lang or options.lang, server_url=getattr(config, "server_url", None),
                         backend=forced or options.backend)
        client = MinerUClient(http, usable(chosen))
        return GatedMinerU(client, self._gate, config.max_concurrency) if self._gate is not None else client

    async def aclose(self) -> None:
        clients, self._http = list(self._http.values()), {}
        for http in clients:
            await http.aclose()


