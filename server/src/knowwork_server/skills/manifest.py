"""SKILL.md 的 frontmatter 解析（技术方案 6.1 节）。

字段：name、description（必填）；version、allowed-tools、completion-tools、env、
disable-model-invocation、user-invocable（选填）。

- 工具清单可以写成空格或逗号分隔的字符串，也可以写成 YAML 列表；
- `env` 是缩进块，每行「名字 required|optional 说明」。YAML 会把多行普通标量折成一行，
  所以 env 块在交给 YAML 之前先单独取出来解析（照 KnowFlow skill_registry 的写法）。
  没写标记按 optional：让别人的任务失败不是对含糊声明的安全读法；
- completion-tools 必须是 allowed-tools 的子集：技能不能以它不被允许调用的工具作为完成判据。
"""

import re
from dataclasses import dataclass
from typing import Any

import yaml

from .errors import PackageInvalid

SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # 与 DSH 官方 isSkillName 相同
MAX_NAME = 64
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")  # 与工具桥的工具名格式相同
MAX_TOOLS = 64
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_ENV = 32
MAX_DESCRIPTION = 2000
MAX_FRONTMATTER = 64 * 1024
MAX_BODY = 200_000
_ENV_KEY = re.compile(r"^env:\s*(?:\|[-+]?)?\s*(?:#.*)?$")
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}
FILE = "SKILL.md"


@dataclass(frozen=True)
class EnvSpec:
    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    version: str
    allowed_tools: tuple[str, ...]
    completion_tools: tuple[str, ...]
    env: tuple[EnvSpec, ...]
    body: str
    model_invocable: bool
    user_invocable: bool

    def describe(self) -> dict[str, Any]:
        """可落库、可返回给前端的 JSON 形态（不含正文）。"""
        return {"name": self.name, "description": self.description, "version": self.version,
                "allowedTools": list(self.allowed_tools), "completionTools": list(self.completion_tools),
                "env": [{"name": e.name, "required": e.required, "description": e.description} for e in self.env],
                "modelInvocable": self.model_invocable, "userInvocable": self.user_invocable}


def _invalid(code: str, message: str, field: str = "") -> PackageInvalid:
    return PackageInvalid(code, message, file=FILE, field=field)


def _split(text: str) -> tuple[list[str], str]:
    normalized = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise _invalid("missing_frontmatter", "SKILL.md 必须以 YAML frontmatter 开头")
    lines = normalized.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end < 0:
        raise _invalid("invalid_frontmatter", "SKILL.md 的 frontmatter 没有闭合")
    front = lines[1:end]
    if sum(len(line) + 1 for line in front) > MAX_FRONTMATTER:
        raise _invalid("invalid_frontmatter", "frontmatter 过大")
    return front, "\n".join(lines[end + 1:]).strip("\n")


def _take_env(front: list[str]) -> tuple[list[str], list[str] | None]:
    """取出 env 缩进块，返回（其余行，env 行；没有 env 键时为 None）。"""
    start = next((i for i, line in enumerate(front) if line.startswith("env:")), -1)
    if start < 0:
        return front, None
    if not _ENV_KEY.match(front[start]):
        raise _invalid("invalid_env", "env 必须写成缩进块，每行「名字 required|optional 说明」", "env")
    stop = start + 1
    while stop < len(front) and (not front[stop].strip() or front[stop][:1].isspace()):
        stop += 1
    block = [line.strip() for line in front[start + 1:stop] if line.strip()]
    return front[:start] + front[stop:], [line[2:].strip() if line.startswith("- ") else line for line in block]


def _env_specs(lines: list[str]) -> tuple[EnvSpec, ...]:
    specs: list[EnvSpec] = []
    for line in lines:
        parts = line.split(None, 2)
        marker = parts[1].lower() if len(parts) > 1 else ""
        if marker in ("required", "optional"):
            description = parts[2].strip() if len(parts) > 2 else ""
        else:
            description = line[len(parts[0]):].strip()
        if not ENV_NAME.match(parts[0]) or len(description) > 200:
            raise _invalid("invalid_env", f"环境变量声明不合法：{line[:80]}", "env")
        specs.append(EnvSpec(name=parts[0], required=marker == "required", description=description))
    if len(specs) > MAX_ENV or len({s.name for s in specs}) != len(specs):
        raise _invalid("invalid_env", f"环境变量最多 {MAX_ENV} 个且不能重名", "env")
    return tuple(specs)


class _NoAliasLoader(yaml.SafeLoader):
    """拒绝锚点与别名：嵌套别名能把 64KB 的 frontmatter 展开成指数级大小（YAML 炸弹）。"""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases are not allowed")
        return super().compose_node(parent, index)


def _mapping(front: list[str]) -> dict[str, Any]:
    try:
        parsed = yaml.load("\n".join(front), Loader=_NoAliasLoader) or {}
    except yaml.YAMLError as error:
        raise _invalid("invalid_frontmatter", "frontmatter 不是合法的 YAML") from error
    if not isinstance(parsed, dict):
        raise _invalid("invalid_frontmatter", "frontmatter 必须是键值映射")
    return parsed


def _tools(meta: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = meta.get(key)
    if raw is None:
        return ()
    if isinstance(raw, str):
        items = [token for token in re.split(r"[\s,]+", raw) if token]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        items = [item.strip() for item in raw if item.strip()]
    else:
        raise _invalid("invalid_tools", f"{key} 必须是工具名列表", key)
    names = tuple(dict.fromkeys(items))
    if len(names) > MAX_TOOLS or any(not TOOL_NAME.match(name) for name in names):
        raise _invalid("invalid_tools", f"{key} 里有不合法的工具名，或超过 {MAX_TOOLS} 个", key)
    return names


def _flag(meta: dict[str, Any], key: str, default: bool) -> bool:
    value = meta.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE | _FALSE:
        return text in _TRUE
    raise _invalid("invalid_invocation_policy", f"{key} 必须是布尔值", key)


def _text(meta: dict[str, Any], key: str) -> str:
    value = meta.get(key)
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise _invalid("invalid_metadata", f"{key} 必须是字符串", key)
    return str(value).strip()


def _identity(meta: dict[str, Any]) -> tuple[str, str, str]:
    name = _text(meta, "name")
    if not name:
        raise _invalid("missing_metadata", "缺少技能名称 name", "name")
    if len(name) > MAX_NAME or not SKILL_NAME.match(name):
        raise _invalid("invalid_skill_name", "技能名称必须是小写 kebab-case，最长 64 个字符", "name")
    description = _text(meta, "description")
    if not description:
        raise _invalid("missing_metadata", "缺少技能说明 description", "description")
    if len(description) > MAX_DESCRIPTION:
        raise _invalid("invalid_metadata", f"description 不能超过 {MAX_DESCRIPTION} 个字符", "description")
    version = _text(meta, "version")
    if version and not VERSION.match(version):
        raise _invalid("invalid_version", "version 只能包含字母、数字与 ._+-，最长 64 个字符", "version")
    return name, description, version


def parse_skill_md(text: str) -> SkillManifest:
    front, body = _split(text)
    rest, env_lines = _take_env(front)
    meta = _mapping(rest)
    name, description, version = _identity(meta)
    if not body.strip():
        raise _invalid("empty_skill_body", "SKILL.md 没有正文")
    if len(body) > MAX_BODY:
        raise _invalid("skill_body_too_large", f"SKILL.md 正文不能超过 {MAX_BODY} 个字符")
    allowed = _tools(meta, "allowed-tools")
    completion = _tools(meta, "completion-tools")
    stray = [tool for tool in completion if tool not in allowed]
    if stray:
        raise _invalid("completion_not_allowed", f"completion-tools 里的 {stray[0]} 不在 allowed-tools 里",
                       "completion-tools")
    return SkillManifest(
        name=name, description=description, version=version, allowed_tools=allowed, completion_tools=completion,
        env=_env_specs(env_lines or []), body=body,
        model_invocable=not _flag(meta, "disable-model-invocation", False),
        user_invocable=_flag(meta, "user-invocable", True),
    )
