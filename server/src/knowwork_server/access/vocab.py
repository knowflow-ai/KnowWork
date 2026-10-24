"""权限词汇：角色、动作、资源与授权主体的格式和校验。全部是不可变值。"""

import re
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Action(StrEnum):
    USE = "use"
    EDIT = "edit"
    OWNER = "owner"


# 高档蕴含低档：owner ⊃ edit ⊃ use
ACTION_RANK: dict[str, int] = {Action.USE: 1, Action.EDIT: 2, Action.OWNER: 3}

# 可授权的资源类型；工作空间自身的管理权用保留资源 WORKSPACE_RESOURCE 表达，不能经授权接口授出
RESOURCE_TYPES: frozenset[str] = frozenset({"skill", "tool", "knowledge_base", "knowledge_source"})
WORKSPACE_RESOURCE = "workspace"
SUBJECT_KINDS: frozenset[str] = frozenset({"user", "group", "dept", "role"})

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
WILDCARD = "*"
# 允许「前缀*」通配的资源类型，判定用 Casbin keyMatch（*只能在末尾，按前缀匹配），不需要另写规则：
# - 一个 MCP 服务的工具都以 mcp__<服务名>__ 开头，tool:mcp__<服务名>__* 覆盖整个服务；
# - 知识库 id 是 <知识源id>.<知识库id>，knowledge_base:<知识源id>.* 覆盖一个来源下的全部知识库。
PREFIX_WILDCARD_TYPES: frozenset[str] = frozenset({"tool", "knowledge_base"})
# 能经授权接口授出的资源类型。knowledge_source 只表示谁能改这条连接，由空间角色的默认规则给；
# 谁能查授在它下面的知识库上。
GRANTABLE_TYPES: frozenset[str] = RESOURCE_TYPES - {"knowledge_source"}
KNOWLEDGE_BASE = "knowledge_base"


class InvalidInput(ValueError):
    """输入不合法。message 可以直接给用户看。"""


def act_implies(requested: str, granted: str) -> bool:
    """Casbin 自定义函数：授予的动作是否覆盖请求的动作。未知动作一律不覆盖。"""
    want = ACTION_RANK.get(requested)
    have = ACTION_RANK.get(granted)
    return want is not None and have is not None and want <= have


def validate_id(value: str, label: str = "id") -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise InvalidInput(f"{label} 格式不合法")
    return value


def parse_action(value: str) -> Action:
    try:
        return Action(value)
    except ValueError as error:
        raise InvalidInput("动作只能是 use、edit、owner") from error


def parse_role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError as error:
        raise InvalidInput("角色只能是 owner、admin、editor、viewer") from error


@dataclass(frozen=True)
class Subject:
    kind: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @staticmethod
    def parse(text: str) -> "Subject":
        kind, sep, ident = (text or "").partition(":")
        if not sep or kind not in SUBJECT_KINDS:
            raise InvalidInput("授权主体须为 user:<id>、group:<id>、dept:<id> 或 role:<角色>")
        if kind == "role":
            parse_role(ident)
        else:
            validate_id(ident, "主体 id")
        return Subject(kind, ident)

    @staticmethod
    def user(user_id: str) -> "Subject":
        return Subject("user", user_id)


@dataclass(frozen=True)
class Resource:
    """可授权的资源，id 为 * 表示该类型的全部资源。"""

    type: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.type}:{self.id}"

    @property
    def is_wildcard(self) -> bool:
        return self.id == WILDCARD

    @staticmethod
    def parse(text: str, *, allow_wildcard: bool = True, types: frozenset[str] = RESOURCE_TYPES) -> "Resource":
        """types 是可接受的资源类型：默认内置的，装了资源层级时再加上它引入的类型（AccessCore.resource_types）。"""
        rtype, sep, ident = (text or "").partition(":")
        if not sep or rtype not in types:
            raise InvalidInput("资源须写成 <类型>:<id>，类型为 " + "、".join(sorted(types)))
        if ident == WILDCARD or ident.endswith(WILDCARD) and rtype in PREFIX_WILDCARD_TYPES:
            if not allow_wildcard:
                raise InvalidInput("这里不接受通配资源")
            if ident != WILDCARD:
                validate_id(ident[:-1], "资源 id 前缀")
            return Resource(rtype, ident)
        validate_id(ident, "资源 id")
        return Resource(rtype, ident)


def grantable(resource: Resource, extra: frozenset[str] = frozenset()) -> Resource:
    """授权接口额外的约束：知识源不单独授权；知识库要写成 <知识源id>.<知识库id> 或 <知识源id>.*。
    extra 是资源层级引入的类型（例如目录），都可以授权。"""
    if resource.type not in GRANTABLE_TYPES | extra:
        raise InvalidInput("知识源不单独授权：谁能改连接由空间角色决定，谁能查请授权它下面的知识库")
    if resource.type == KNOWLEDGE_BASE and not resource.is_wildcard:
        source, dot, base = resource.id.partition(".")
        if not source or not dot or not base or "." in base or (base.endswith(WILDCARD) and base != WILDCARD):
            raise InvalidInput("知识库须写成 <知识源id>.<知识库id>，或用 <知识源id>.* 表示一个来源下的全部知识库")
    return resource


def parse_resource_type(value: str, types: frozenset[str] = RESOURCE_TYPES) -> str:
    if value not in types:
        raise InvalidInput("资源类型只能是 " + "、".join(sorted(types)))
    return value


def clean_name(name: str) -> str:
    cleaned = (name or "").strip() if isinstance(name, str) else ""
    if not 1 <= len(cleaned) <= 100:
        raise InvalidInput("名称长度须为 1 到 100 个字符")
    return cleaned
