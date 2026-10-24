"""@ 菜单要的知识候选（conversations/mention_api.py 用）：本人能查的知识库，带所在目录、标签集；按名字找文件。

范围收窄同检索（授权、开放范围），所以列出来的就是 @ 了真能查的。标签只给支持点名的知识源：
标签是名为「标签」的多值字段，可选值就是标签集。
"""

from .service import KnowledgeService

TAG_FIELD = "标签"
MAX_BASES = 500
MAX_DESCRIPTION = 200


async def mentionable_bases(service: KnowledgeService, workspace_id: str, user_id: str) -> list[dict]:
    caller = await service.caller(workspace_id, user_id)
    listing = await service.bases(caller)
    found = []
    for base in listing.bases[:MAX_BASES]:
        tags = next((f.options for f in base.info.fields if f.name == TAG_FIELD), ()) if base.pinnable else ()
        found.append({"id": base.id, "name": base.info.name or base.id,
                      "description": " ".join(base.info.description.split())[:MAX_DESCRIPTION],
                      "source": base.source_name, "path": list(base.info.path), "tags": list(tags),
                      "pinnable": base.pinnable})
    return found


async def mentionable_documents(service: KnowledgeService, workspace_id: str, user_id: str,
                                query: str) -> list[dict]:
    caller = await service.caller(workspace_id, user_id)
    return [{"id": d.doc_ref, "title": d.title, "base": d.base, "base_name": d.base_name}
            for d in await service.find_documents(caller, query)]
