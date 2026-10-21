"""workflow.yaml 里的引用语法：只取值，不求值（E2 退路，技术方案 6.5 节）。

语法只有两种，写在字符串里：
- `${inputs.<名字>[.<字段>|.<下标>]...}`：流程入参；
- `${steps.<步骤键>.output[.<字段>|.<下标>]...}`：前面某一步的结果。

整个字符串恰好是一个引用时，替换成被引用的原始值（可以是对象、数组、数字）；
引用嵌在更长的文本里时，替换成文本（字符串原样，其他值转成 JSON）。
不支持任何运算、函数、默认值或条件：引用只是路径，安装时就能逐条校验它指向的东西存在。
"""

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

REF = re.compile(r"\$\{([^{}]*)\}")
SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_REF_DEPTH = 16


class ReferenceInvalid(ValueError):
    """引用写法不合法（安装期）。"""


class ReferenceUnresolved(LookupError):
    """引用指向的值在运行时不存在。message 可以给用户看。"""


@dataclass(frozen=True)
class Ref:
    root: str  # inputs | steps
    step: str | None  # root 为 steps 时的步骤键
    path: tuple[str, ...]  # inputs 之后、或 output 之后的路径

    @property
    def text(self) -> str:
        head = "inputs" if self.root == "inputs" else f"steps.{self.step}.output"
        return "${" + ".".join((head, *self.path)) + "}"


def parse_ref(body: str) -> Ref:
    parts = body.strip().split(".")
    if any(not SEGMENT.match(part) for part in parts) or len(parts) > MAX_REF_DEPTH:
        raise ReferenceInvalid(f"引用 ${{{body[:80]}}} 写法不合法")
    if parts[0] == "inputs" and len(parts) >= 2:
        return Ref(root="inputs", step=None, path=tuple(parts[1:]))
    if parts[0] == "steps" and len(parts) >= 3 and parts[2] == "output":
        return Ref(root="steps", step=parts[1], path=tuple(parts[3:]))
    raise ReferenceInvalid(f"引用 ${{{body[:80]}}} 只能是 inputs.<名字> 或 steps.<步骤键>.output")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def find_refs(value: Any) -> tuple[Ref, ...]:
    """取出一个值里（递归）所有字符串中的引用；写法不合法即抛 ReferenceInvalid。"""
    refs: list[Ref] = []
    for text in _strings(value):
        if "${" in text and REF.sub("", text).find("${") >= 0:
            raise ReferenceInvalid("有未闭合或嵌套的 ${")
        refs.extend(parse_ref(match.group(1)) for match in REF.finditer(text))
    return tuple(refs)


def _walk(value: Any, path: tuple[str, ...], ref: Ref) -> Any:
    current = value
    for segment in path:
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            raise ReferenceUnresolved(f"引用 {ref.text} 指向的值不存在")
    return current


def lookup(ref: Ref, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> Any:
    if ref.root == "inputs":
        return _walk(inputs, ref.path, ref)
    if ref.step not in outputs:
        raise ReferenceUnresolved(f"引用 {ref.text} 指向的步骤还没有结果")
    return _walk(outputs[ref.step], ref.path, ref)


def _render(text: str, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> Any:
    whole = REF.fullmatch(text)
    if whole:
        return lookup(parse_ref(whole.group(1)), inputs, outputs)

    def replace(match: re.Match) -> str:
        value = lookup(parse_ref(match.group(1)), inputs, outputs)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return REF.sub(replace, text)


def resolve(value: Any, inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> Any:
    """把一个值里的引用全部替换掉，返回新值，不修改原值。"""
    if isinstance(value, str):
        return _render(value, inputs, outputs) if "${" in value else value
    if isinstance(value, Mapping):
        return {key: resolve(item, inputs, outputs) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [resolve(item, inputs, outputs) for item in value]
    return value
