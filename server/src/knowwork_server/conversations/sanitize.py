"""落库前清洗宿主帧：PostgreSQL 的 JSONB 不接受 \\u0000、NaN，也存不下孤立的代理码点。"""

import math
from typing import Any

MAX_DEPTH = 64


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "replace").decode("utf-8")
    return text


def clean_json(value: Any, depth: int = 0) -> Any:
    """返回清洗后的副本，不修改入参。嵌套过深的部分截断为 None。"""
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        return {_clean_text(str(k)): clean_json(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean_json(v, depth + 1) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
