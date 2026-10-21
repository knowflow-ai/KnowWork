"""消息内容块的规整：会话日志里的消息（user、assistant、tool、system）统一成「块」给界面看，长文字截断并标记。"""

from dataclasses import dataclass
from typing import Any

TEXT_LIMIT = 20_000


@dataclass(frozen=True)
class Block:
    """type：text、reasoning、tool-call，或原样的其他类型（图片、附件等只给类型）。"""

    type: str
    text: str = ""
    name: str | None = None
    call_id: str | None = None
    truncated: bool = False


def clip(text: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def block_of(raw: Any) -> Block | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None
    kind = raw["type"]
    if kind == "tool-call":
        text, cut = clip(str(raw.get("arguments") or ""))
        return Block(type=kind, text=text, name=str(raw.get("name") or ""), call_id=raw.get("id"), truncated=cut)
    if isinstance(raw.get("text"), str):
        text, cut = clip(raw["text"])
        return Block(type=kind, text=text, truncated=cut)
    return Block(type=kind, text=f"[{kind}]")


def blocks_of(message: Any) -> tuple[Block, ...]:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        text, cut = clip(content)
        return (Block(type="text", text=text, truncated=cut),)
    found = (block_of(item) for item in content) if isinstance(content, list) else ()
    return tuple(block for block in found if block is not None)


def source_of(message: Any) -> str | None:
    """消息从哪来：user 是用户亲手发的；runtime-context、skill-catalog、skill-invocation、subagent-settled
    这类是宿主替模型塞进去的上下文，也以 user 角色出现。"""
    source = message.get("source") if isinstance(message, dict) else None
    kind = source.get("kind") if isinstance(source, dict) else None
    return kind if isinstance(kind, str) else None


def text_of(message: Any, kind: str = "text") -> str:
    return "".join(block.text for block in blocks_of(message) if block.type == kind)
