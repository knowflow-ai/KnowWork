"""Casbin 规则的不可变表示与构造函数。组织结构到 g 规则、授权到 p 规则的映射都只在这里定义。"""

from dataclasses import dataclass, field

from .vocab import WILDCARD, WORKSPACE_RESOURCE, Action, Role


@dataclass(frozen=True)
class Rule:
    ptype: str  # "p" 或 "g"
    values: tuple[str, ...]

    @property
    def workspace_id(self) -> str:
        return self.values[1] if self.ptype == "p" else self.values[2]

    def padded(self) -> tuple[str, str, str, str]:
        v = (*self.values, "", "", "", "")
        return v[0], v[1], v[2], v[3]


@dataclass(frozen=True)
class RuleChange:
    """一次事务里对策略的增删，提交后原样回放到内存里的 Casbin。"""

    added: tuple[Rule, ...] = field(default_factory=tuple)
    removed: tuple[Rule, ...] = field(default_factory=tuple)

    def merge(self, other: "RuleChange") -> "RuleChange":
        return RuleChange(added=self.added + other.added, removed=self.removed + other.removed)

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed


def user_key(user_id: str) -> str:
    return f"user:{user_id}"


def group_key(group_id: str) -> str:
    return f"group:{group_id}"


def dept_key(dept_id: str) -> str:
    return f"dept:{dept_id}"


def role_key(role: str) -> str:
    return f"role:{role}"


def link(child: str, parent: str, workspace_id: str) -> Rule:
    return Rule("g", (child, parent, workspace_id))


def member_role(user_id: str, role: str, workspace_id: str) -> Rule:
    return link(user_key(user_id), role_key(role), workspace_id)


def group_member(user_id: str, group_id: str, workspace_id: str) -> Rule:
    return link(user_key(user_id), group_key(group_id), workspace_id)


def dept_member(user_id: str, dept_id: str, workspace_id: str) -> Rule:
    return link(user_key(user_id), dept_key(dept_id), workspace_id)


def dept_parent(child_id: str, parent_id: str, workspace_id: str) -> Rule:
    return link(dept_key(child_id), dept_key(parent_id), workspace_id)


def permission(subject: str, workspace_id: str, obj: str, action: str) -> Rule:
    return Rule("p", (subject, workspace_id, obj, action))


# 空间角色的默认权限。workspace:use = 成员可读组织与成员；workspace:edit = 管理成员、组织与授权；
# workspace:owner = 管理空间所有者。资源默认只给模型分组的「可用」，其余资源靠显式授权。
_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    (Role.OWNER, WILDCARD, Action.OWNER),
    (Role.ADMIN, WORKSPACE_RESOURCE, Action.EDIT),
    (Role.ADMIN, "skill:*", Action.OWNER),
    (Role.ADMIN, "tool:*", Action.OWNER),
    (Role.ADMIN, "knowledge_base:*", Action.OWNER),
    (Role.ADMIN, "knowledge_source:*", Action.OWNER),
    (Role.ADMIN, "model_group:*", Action.OWNER),
    (Role.EDITOR, WORKSPACE_RESOURCE, Action.USE),
    (Role.EDITOR, "model_group:*", Action.USE),
    (Role.VIEWER, WORKSPACE_RESOURCE, Action.USE),
    (Role.VIEWER, "model_group:*", Action.USE),
)


def default_role_policies(workspace_id: str) -> tuple[Rule, ...]:
    return tuple(permission(role_key(role), workspace_id, obj, act) for role, obj, act in _DEFAULTS)
