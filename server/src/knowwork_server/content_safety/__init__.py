"""内容安全（敏感词）：管理员给空间配一份敏感词，员工发给模型的内容、模型的回答和它要调用的工具参数，命中就拦下、
给一句管理员写好的提示，并记进审计。匹配是字面匹配，不做语义判断。

查在宿主插件里做（runtime-host 的 content-guard.mjs，挂 DSH 官方接缝），词表与判断在这里：插件把文字送来，这里匹配、
答复命中与否。没保存过策略的空间就是关闭，插件原样放行。方案见技术文档「内容安全」。
"""

from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit.sink import AuditSink
from .api import PREFIX, PUBLIC_PREFIX, OwnerOf, TokenVerifier, build_internal_router, build_router
from .service import AccessChecker, ContentSafetyService
from .sources import CUSTOM_SOURCE, WordSource, WordSources


@dataclass(frozen=True)
class ContentSafety:
    service: ContentSafetyService


def build_content_safety(sessionmaker: async_sessionmaker, access: AccessChecker, audit: AuditSink | None, *,
                         sources: WordSources | None = None) -> ContentSafety:
    """sessionmaker 要用按空间的工厂（行级安全）；sources 是扩展登记现成词表的地方（ApiContext 上同一个）。"""
    return ContentSafety(service=ContentSafetyService(sessionmaker, access, audit, sources=sources))


def internal_router(module: ContentSafety, tokens: TokenVerifier, is_member, *, path_prefix: str = PREFIX,
                    owner_of: OwnerOf | None = None) -> APIRouter:
    """宿主回调的检查接口：内部端口给服务端宿主；path_prefix 给 PUBLIC_PREFIX 时挂在公开端口，给桌面端的本机宿主。
    owner_of 只在内部端口给：模型网关拿专属宿主的模型令牌来查。"""
    return build_internal_router(module.service, tokens, is_member, path_prefix=path_prefix, owner_of=owner_of)


__all__ = ["CUSTOM_SOURCE", "PUBLIC_PREFIX", "ContentSafety", "ContentSafetyService", "WordSource", "WordSources",
           "build_content_safety", "build_router", "internal_router"]
