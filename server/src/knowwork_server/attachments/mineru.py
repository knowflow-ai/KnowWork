"""MinerU 4.1+ HTTP 客户端：把 PDF、Word、PPT 解析成 Markdown。

协议是 MinerU 4.1 的 V1 API（3.x 的 /tasks 与 /file_parse、4.0 的 /v1/uploads 与 /v1/parse/jobs 都已删除，
连上旧版服务时按「解析服务版本不支持」失败、不重试，不去猜旧协议）：
- 优先异步任务：POST /v1/tasks（JSON 清单，files 里给文件名与大小）→ 按回执里的 upload 地址 PUT 文件字节
  （命中源缓存时回执没有 upload，跳过）→ 轮询 GET /v1/tasks/{id} → 完成后从 files[].result_url 下载结果
  ZIP（markdown.md、middle_json.json、images/）；
- /v1/tasks 返回 404/405/501（只部署了同步解析服务 file-parse-server）时回退到同步 POST /v1/file_parse：
  multipart 传单文件，成功直接回 ZIP 字节；202 表示服务等不到解析完成，按 request_id 轮询到终态再取结果
  （同一 request_id 断线后可以恢复）；两边都 404 才报版本不支持。
附件只要 Markdown（parse），知识库要版面与图片（parse_full）：4.1 的结果按 output_formats 打包，markdown 只在
default 里有，ZIP 整包都得下载；parse 不把 middle_json 与图片解包进内存，parse_full 才全取。

解析档位 tier（flash/basic/standard/advanced）提交时带上，None 是让 MinerU 自动推荐；语言、VLM 推理服务
地址（sglang / vllm）这些都不再是请求字段，由 MinerU 侧部署配置。

失败分类决定是否重试：格式不支持、文件损坏不重试；服务超时、服务暂不可用可重试。
任务状态为 failed 时不能一律当成文件坏了：服务端配置错（例如后端缺 GPU 设备、VLM 服务连不上）也报 failed。
错误信息里看得出文件问题的才不重试，其余按服务问题重试，重试用尽后给用户的话同时提到两种可能。
"""

import asyncio
import io
import json
import logging
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

log = logging.getLogger(__name__)

SUCCESS_STATES = frozenset({"success", "completed", "done", "finished", "succeeded"})
FAILURE_STATES = frozenset({"failed", "error", "cancelled", "canceled"})
# 父任务终态之外的状态（waiting-file、pending、running）都在等
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
    """任务/文件状态为 failed 时按错误信息分类。原始信息只写日志，不给用户看。
    4.1 里错误是 {"error": {"code", "message"}} 对象，兼容字符串形式。"""
    fields = payload if isinstance(payload, dict) else {}
    error = fields.get("error")
    if isinstance(error, dict):
        text = str(error.get("message") or error.get("code") or "")
    else:
        text = str(fields.get("error") or fields.get("message") or fields.get("msg") or "")
    text = text[:500]
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
    if status in (400, 413, 422):  # 服务拒收这个文件（格式、大小）
        return corrupt()
    if status == 500 or status in _UNAVAILABLE:  # 500 大多是服务自己崩了（解析进程崩溃、缺资源），按服务问题可重试
        return unavailable()
    return bad_protocol()


def _submit_error(status: int) -> MinerUError:
    """提交清单阶段（POST /v1/tasks）还没有文件字节，4xx 基本都是清单或参数没被接受（协议、档位、文件名），
    不是文件坏了——按「文件损坏」提示会误导用户，重试也帮不上忙，归为协议问题。"""
    if status in (400, 413, 415, 422):
        return bad_protocol()
    return _status_error(status)


@dataclass(frozen=True)
class MinerUOptions:
    # 解析档位：None 让 MinerU 自动推荐（standard → basic → flash → advanced），也可以强制 flash/basic/standard/advanced
    tier: str | None = None
    parse_timeout_seconds: float = 600.0
    poll_interval_seconds: float = 2.0
    poll_max_interval_seconds: float = 10.0
    # 单个 HTTP 请求的超时（与 MinerUProvider 给 http 客户端的一致）：同步接口的 wait_timeout 不能超过它，
    # 不然服务还在按 wait_timeout 等结果，连接先被客户端掐断，202 恢复路径走不到
    request_timeout_seconds: float = 60.0


DEFAULT_OPTIONS = MinerUOptions()


@dataclass(frozen=True)
class MinerUResult:
    markdown: str
    middle: dict | None              # middle_json（版面、块坐标、标题、表格 HTML、图片描述）
    images: dict[str, bytes]         # 图片文件名 → 内容，middle_json 里的 image_path 指向这里的名字


def _field(payload: Any, name: str) -> Any:
    return payload.get(name) if isinstance(payload, dict) else None


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

    async def parse_full(self, filename: str, data: bytes, mime: str, *, layout: bool = True) -> MinerUResult:
        """结果按 output_formats 打包，markdown 只在 default 里有，所以 ZIP 整包都要下载；
        layout 为假（附件路径）时不把 middle_json 与图片读进内存，省的是解包与内存，不是传输。
        一次解析（提交、上传、轮询、下载）共用同一个总时限，超了按「服务超时」处理。"""
        return await self._run(filename, data, mime, layout=layout)

    async def _run(self, filename: str, data: bytes, mime: str, *, layout: bool) -> MinerUResult:
        deadline = self._clock() + self._options.parse_timeout_seconds
        try:
            return await self._parse(filename, data, mime, deadline, layout)
        except MinerUError:
            raise
        except httpx.TimeoutException as error:
            raise timed_out() from error
        except httpx.TransportError as error:
            raise unavailable() from error
        except (ValueError, KeyError, TypeError) as error:  # JSON 不合法或字段缺失
            log.warning("mineru returned malformed payload: %s", type(error).__name__)
            raise bad_protocol() from error

    async def _parse(self, filename: str, data: bytes, mime: str, deadline: float, layout: bool) -> MinerUResult:
        manifest: dict[str, Any] = {"files": [{"file_name": filename, "bytes": len(data)}],
                                    "output_formats": ["default"]}
        if self._options.tier:
            manifest["tier"] = self._options.tier
        response = await self._http.post("/v1/tasks", json=manifest)
        if response.status_code in _NO_ASYNC:
            return await self._parse_sync(filename, data, mime, deadline, layout)
        if response.status_code >= 400:
            raise _submit_error(response.status_code)
        receipt = response.json()
        files = _field(receipt, "files")
        if not isinstance(_field(receipt, "task_id"), str) or not receipt["task_id"] \
                or not isinstance(files, list) or not files or not isinstance(files[0], dict):
            raise bad_protocol()
        upload = _field(files[0], "upload")
        if isinstance(upload, dict):
            await self._upload(upload, data)
        result_url = await self._wait(receipt["task_id"], _field(files[0], "file_id"), deadline)
        return _unzip(await self._download(result_url), layout)

    async def _upload(self, upload: dict[str, Any], data: bytes) -> None:
        url = _field(upload, "url")
        if not isinstance(url, str) or not url:
            raise bad_protocol()
        headers = {str(key): str(value) for key, value in (_field(upload, "headers") or {}).items()}
        request = self._http.build_request("PUT", url, content=data, headers=headers)
        if self._cross_origin(url):  # 跨源的签名上传地址（对象存储）不带令牌，地址原样用
            request.headers.pop("authorization", None)
        response = await self._http.send(request)
        if response.status_code in (404, 410):  # 上传记录没找到/链接过期：整次重来还来得及
            raise unavailable()
        if response.status_code >= 400:
            raise _status_error(response.status_code)

    def _cross_origin(self, url: str) -> bool:
        """相对地址（带不带前导斜杠都是解析服务自己）不算跨源。"""
        target = urlsplit(url)
        if not (target.scheme and target.hostname):
            return False
        return not self._same_origin(url)

    def _same_origin(self, url: str) -> bool:
        base, target = urlsplit(str(self._http.base_url)), urlsplit(url)
        default_port = {"https": 443, "http": 80}
        return (base.scheme, base.hostname, base.port or default_port.get(base.scheme)) == \
            (target.scheme, target.hostname, target.port or default_port.get(target.scheme))

    async def _wait(self, task_id: str, file_id: Any, deadline: float) -> str:
        interval = self._options.poll_interval_seconds
        while self._clock() < deadline:
            response = await self._http.get(f"/v1/tasks/{task_id}")
            if response.status_code == 404:  # 任务有保留时限，服务重启/缓存过期后就没了：整次重来
                raise unavailable()
            if response.status_code >= 400:
                raise _status_error(response.status_code)
            payload = response.json()
            state = str(_field(payload, "status") or "").strip().lower()
            if state in SUCCESS_STATES or state in FAILURE_STATES or state == "partial":
                entry = _entry(payload, file_id)
                file_state = str(_field(entry, "status") or state).strip().lower()
                if file_state in SUCCESS_STATES:
                    result_url = _field(entry, "result_url")
                    if not isinstance(result_url, str) or not result_url:
                        raise bad_protocol()
                    return result_url
                if file_state in FAILURE_STATES:
                    raise task_failure(entry)
                raise unavailable()  # 父子状态对不上这类意外终态：整次重来
            await self._sleep(interval)
            interval = min(self._options.poll_max_interval_seconds, interval * 1.5)
        raise timed_out()

    async def _download(self, url: str) -> bytes:
        request = self._http.build_request("GET", url)
        if self._cross_origin(url):  # 跨源的签名结果地址（对象存储）不带令牌
            request.headers.pop("authorization", None)
        response = await self._http.send(request)
        if response.status_code == 404:  # 结果被清了/签名地址失效：整次重来
            raise unavailable()
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        return response.content

    async def _parse_sync(self, filename: str, data: bytes, mime: str, deadline: float, layout: bool) -> MinerUResult:
        remaining = deadline - self._clock()
        # wait_timeout 封顶在 http 客户端自己的请求超时内（留几秒余量给提交与响应本身），也不超过这次解析的总预算：
        # 服务按它等结果后回 200（ZIP）或 202（还没完，按 request_id 接着轮询）
        wait_timeout = int(max(0, min(3600, self._options.request_timeout_seconds - 5, remaining)))
        form: dict[str, Any] = {"output_formats": "default", "request_id": uuid4().hex,
                                "wait_timeout": wait_timeout}
        if self._options.tier:
            form["tier"] = self._options.tier
        files = {"file": (filename, data, mime)}
        response = await self._http.post("/v1/file_parse", files=files, data=form)
        if response.status_code == 404:
            raise MinerUError("parser_version_unsupported", "文档解析服务版本不受支持（需要 MinerU 4.1+）",
                              retryable=False)
        if response.status_code == 202:  # 服务等不到解析完成：按 request_id 接着等（同一请求可以恢复）
            return _unzip(await self._wait_sync(form["request_id"], response.json(), deadline), layout)
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        return _unzip(response.content, layout)

    async def _wait_sync(self, request_id: str, status: Any, deadline: float) -> bytes:
        interval = self._options.poll_interval_seconds
        while self._clock() < deadline:
            state = str(_field(status, "status") or "").strip().lower()
            if state in SUCCESS_STATES:
                url = _field(status, "result_url")
                return await self._download(url if isinstance(url, str) and url
                                            else f"/v1/file_parse/{request_id}/result")
            if state in FAILURE_STATES:
                raise task_failure(status)
            await self._sleep(interval)
            interval = min(self._options.poll_max_interval_seconds, interval * 1.5)
            response = await self._http.get(f"/v1/file_parse/{request_id}")
            if response.status_code == 404:  # 记录过了保留期：整次重来
                raise unavailable()
            if response.status_code >= 400:
                raise _status_error(response.status_code)
            status = response.json()
        raise timed_out()


def _entry(payload: dict[str, Any], file_id: Any) -> dict[str, Any]:
    """轮询回包里的文件条目：按提交时的 file_id 匹配，对不上就取第一条（一次只提交一个文件）。"""
    files = _field(payload, "files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("file_id") == file_id:
                return item
        for item in files:
            if isinstance(item, dict):
                return item
    return {}


def _unzip(data: bytes, layout: bool = True) -> MinerUResult:
    """结果 ZIP：markdown.md 必须有；middle_json.json 要是合法 JSON；images/ 里的名字去掉路径。
    名字按 basename 与 images/ 段匹配，兼容包里套一层目录的打包方式。layout 为假（附件只要 Markdown）
    时不把 middle_json 与图片读进内存。"""
    markdown, middle, images = None, None, {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                base = name.rsplit("/", 1)[-1]
                if base == "markdown.md" and markdown is None:
                    markdown = archive.read(name).decode("utf-8")
                elif base == "middle_json.json" and middle is None and layout:
                    middle = _middle(archive.read(name).decode("utf-8"))
                elif layout and not name.endswith("/") and ("/images/" in name or name.startswith("images/")):
                    images[base] = archive.read(name)
    except (zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise bad_protocol() from error
    if markdown is None:
        raise bad_protocol()
    return MinerUResult(markdown=markdown, middle=middle, images=images)


def _middle(value: Any) -> dict | None:
    if not value.strip():
        return None
    try:
        value = json.loads(value)
    except ValueError as error:
        raise bad_protocol() from error
    if not isinstance(value, dict):
        raise bad_protocol()
    return value


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

    async def parse_full(self, filename: str, data: bytes, mime: str, *, layout: bool = True) -> MinerUResult:
        async with self._gate.hold(self.GATE, self._limit):
            return await self._client.parse_full(filename, data, mime, layout=layout)


class ParserSettingsSource(Protocol):
    """按空间给出当前的解析服务配置（docparse.DocumentParsers 满足）；None 是没配。返回的配置要有
    base_url、api_key、tier（解析档位，None 是自动）、max_concurrency 这几项。"""

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
        forced = getattr(config, "tier", None)  # 管理后台强制的解析档位；为空是「自动」，按调用方的默认
        client = MinerUClient(http, replace(options, tier=forced or options.tier))
        return GatedMinerU(client, self._gate, config.max_concurrency) if self._gate is not None else client

    async def aclose(self) -> None:
        clients, self._http = list(self._http.values()), {}
        for http in clients:
            await http.aclose()
