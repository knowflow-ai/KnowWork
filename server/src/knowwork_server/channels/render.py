"""把回答整理成 IM 里能看的文字。

先去掉引用标记 [n] 与放图标记 [IMG:n:j]：IM 里看不到引用卡片，留着只是一串数字；文末出处列表以后再做。
不带网页的会话链接：会话属于渠道账号，提问的人打不开。
"""

import re

from ..knowledge.citations import IMAGE_MARK

_MARK = re.compile(r"\[(\d{1,6})\](?!\()")
_BLANKS = re.compile(r"\n{3,}")
# 企业微信 markdown 消息上限 20480 字节；留出说明的余量
MAX_BYTES = 18_000


def clean(answer: str) -> str:
    text = _MARK.sub("", IMAGE_MARK.sub("", answer or ""))
    return _BLANKS.sub("\n\n", text).strip()


def clip(text: str, limit: int = MAX_BYTES) -> str:
    """按字节截断，截断了就说明一句，不截半个字。"""
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[:limit].decode(errors="ignore").rstrip() + "\n\n……（内容太长，后面的没发出来）"
