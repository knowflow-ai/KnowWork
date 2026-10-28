"""knowledge_fetch：把知识库里的单张图、几页页图或原文件放进这个会话的工作区（沙箱的 /workspace/input），交回路径。

- 看图里的具体内容（读折线图数值、看接线图、看示意图的含义）：入库时的图片描述常常写不全，要作答的模型带着问题去看。
  取来以后用 DSH 官方的 read_image 读这个路径，不另做看图工具（当前模型不能看图时业务服务把 read_image 收掉，
  这个工具照样能取，只是看不了）；
- 图用检索、阅读结果里的图 id（IMG:<引用编号>:<第几张>），按引用目录换回是哪份文档的哪张图，编出来的编号不认；
  页图给文档与页码，一次最多 MAX_FETCH_PAGES 页；原文件（例如 Excel）交给 Python 算；
- 判权同阅读（知识服务的 fetch）；知识源要声明 fetch 能力。没开沙箱时不提供这个工具（放不进工作区）。
"""

import json
import mimetypes
import re
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from ..sandbox.contract import INPUT_DIR, SandboxError
from ..sessionfiles.names import InvalidFileName, clean_name
from ..tools.registry import BuiltinTool, ToolContext, ToolFailure, ToolSpec
from .citation_store import CitationStore
from .errors import KnowledgeError
from .models import MAX_FETCH_PAGES, FetchedFile, FetchRequest
from .service import KnowledgeService

FETCH_NAME = "knowledge_fetch"
MAX_FETCH_BYTES = 50 * 1024 * 1024  # 一次放进工作区的合计上限
IMAGE_ID = re.compile(r"^\[?IMG:(\d{1,6}):(\d{1,3})\]?$")
# 当前模型不能看图时业务服务把 read_image 收掉了：说清楚看不了时怎么办，免得模型以为图里没东西或者编图里的内容
IMAGE_NOTE = ("图已放进工作区：要看图里的内容就用 read_image 读 path，带着要回答的问题看；"
              "可用工具里没有 read_image 时当前模型看不了图，按检索到的文字作答，并说明图里的细节没法确认")
FILE_NOTE = "原文件已放进工作区，可以用 Python 等读 path"

FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "image": {"type": "string", "description": "检索或阅读结果里的图 id，如 IMG:2:0，取这张图"},
        "source_id": {"type": "string", "description": "取页图或原文件时：知识源，取自结果的 source_id"},
        "doc_ref": {"type": "string", "description": "取页图或原文件时：文档引用，取自结果的 doc_ref"},
        "pages": {"type": "string", "description": f"取页图：页码或范围，如 5、5-6，一次最多 {MAX_FETCH_PAGES} 页"},
        "original": {"type": "boolean", "description": "取原文件（如 Excel 要用 Python 计算时）"},
    },
    "additionalProperties": False,
}


class SessionInput(Protocol):
    """会话沙箱放文件的入口（sandbox.contract.Sandbox 满足它）。"""

    async def put_input(self, workspace_id: str, session_id: str, name: str, data: bytes) -> Any: ...


TurnBases = Callable[[ToolContext], Any]


def _pages(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{1,5})\s*(?:[-~～到]\s*(\d{1,5}))?\s*", value)
    if not match:
        raise ToolFailure("pages 要写成页码或范围，如 5、5-6")
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def _file_name(file: FetchedFile) -> str:
    """放进工作区的名字：加「知识库-」前缀，免得和用户上传的附件重名、互相覆盖。名字不合法时按类型给一个。"""
    try:
        return clean_name(f"知识库-{file.name}")
    except InvalidFileName:
        extension = mimetypes.guess_extension(file.mime) or ""
        return f"知识库-文件{f'-第{file.page}页' if file.page else ''}{extension}"


def fetch_tool(service: KnowledgeService, citations: CitationStore, sandbox: SessionInput, *,
               catalog_of: Callable[[ToolContext], str], turn_bases: TurnBases) -> BuiltinTool:
    async def request_of(ctx: ToolContext, arguments: dict[str, Any]) -> tuple[str, FetchRequest]:
        image, pages, original = arguments.get("image"), arguments.get("pages"), arguments.get("original") is True
        if sum((bool(image), bool(pages), original)) != 1:
            raise ToolFailure("image、pages、original 只能给一个：取一张图、取几页页图，或者取原文件")
        if image:
            match = IMAGE_ID.match(str(image).strip())
            if not match:
                raise ToolFailure("image 要写检索结果里的图 id，如 IMG:2:0")
            number, index = int(match.group(1)), int(match.group(2))
            entry = (await citations.load(ctx.workspace_id, catalog_of(ctx))).lookup(number)
            images = entry.evidence.images if entry else ()
            if index >= len(images):
                raise ToolFailure(f"没有 IMG:{number}:{index} 这张图：只能取这个会话检索、阅读结果里出现过的图")
            evidence = entry.evidence
            return evidence.source_id, FetchRequest(doc_ref=evidence.doc_ref, what="image",
                                                    image_ref=images[index].ref)
        source_id, doc_ref = str(arguments.get("source_id") or ""), str(arguments.get("doc_ref") or "")
        if not source_id or not doc_ref:
            raise ToolFailure("取页图或原文件要给 source_id 和 doc_ref")
        try:
            if original:
                return source_id, FetchRequest(doc_ref=doc_ref, what="file")
            first, last = _pages(str(pages))
            return source_id, FetchRequest(doc_ref=doc_ref, what="pages", first_page=first, last_page=last)
        except ValidationError as error:
            raise ToolFailure(f"页码范围不对：一次最多取 {MAX_FETCH_PAGES} 页") from error

    async def handler(ctx: ToolContext, arguments: dict[str, Any]) -> str:
        source_id, request = await request_of(ctx, arguments)
        caller = await service.caller(ctx.workspace_id, ctx.user_id)
        try:
            files = await service.fetch(caller, source_id, request, bases=await turn_bases(ctx))
        except KnowledgeError as error:
            raise ToolFailure(str(error)) from error
        if sum(len(f.data) for f in files) > MAX_FETCH_BYTES:
            raise ToolFailure(f"文件太大（超过 {MAX_FETCH_BYTES // 1024 // 1024} MB），放不进工作区")
        placed = []
        for file in files:
            name = _file_name(file)
            try:
                await sandbox.put_input(ctx.workspace_id, ctx.session_id, name, file.data)
            except SandboxError as error:
                raise ToolFailure(f"放进工作区没有成功：{error}") from error
            placed.append({"path": f"{INPUT_DIR}/{name}", "type": file.mime, "size": len(file.data),
                           **({"page": file.page} if file.page else {})})
        images = any(f.mime.startswith("image/") for f in files)
        notes = [IMAGE_NOTE if images else FILE_NOTE]
        return json.dumps({"files": placed, "notes": notes}, ensure_ascii=False)

    return BuiltinTool(ToolSpec(
        FETCH_NAME, "把知识库里的图、页图或原文件放进本会话的工作区，交回路径。要看图里的具体内容（读数、流程、示意图的"
                    "含义）时先用它取图，再用 read_image 读返回的 path；图用检索或阅读结果里的图 id（如 IMG:2:0），"
                    f"图要连同周围文字一起看或跨页时取页图（给 source_id、doc_ref 和页码，一次最多 {MAX_FETCH_PAGES} 页）；"
                    "要算表格里的数时取原文件交给 Python。",
        FETCH_SCHEMA, "read", concurrent=True), handler)
