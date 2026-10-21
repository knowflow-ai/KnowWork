"""内存里的 Casbin 判定器。

策略的权威副本在 casbin_rule 表；这里是它的内存镜像，写事务提交后按 RuleChange 增量回放，
回放失败或尚未加载时进入不可用状态，所有判定一律拒绝（fail-closed），直到重新整体加载。

性能：策略按 (域, 资源) 建索引（Casbin 官方的 FastModel），一次判定只在几个候选桶里跑
匹配器：精确资源、`<类型>:*`、`*`，以及本空间里能匹配上的「前缀*」资源（工具与知识库允许，见
vocab.PREFIX_WILDCARD_TYPES）。前缀通配另记在 _Prefixes 里，和判定器一起整体替换、增量更新，
不去读 Casbin 索引的私有字段。通配只允许这几种写法，授权接口会校验。
"""

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import casbin
from casbin.model import FastModel, fast_policy_filter

from .rules import Rule, RuleChange, user_key
from .vocab import WILDCARD, act_implies

log = logging.getLogger(__name__)

MODEL_TEXT = """
[request_definition]
r = sub, dom, obj, act

[policy_definition]
p = sub, dom, obj, act

[role_definition]
g = _, _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = r.dom == p.dom && keyMatch(r.obj, p.obj) && actImplies(r.act, p.act) && g(r.sub, p.sub, r.dom)
"""

# FastModel 的索引键：p 规则的第 1、2 个字段，即域与资源
_INDEX_ORDER = (1, 2)


@dataclass(frozen=True)
class VisibleSet:
    """可见资源集合。all_ 为真表示该类型的全部资源可见（命中了通配授权）。"""

    all_: bool
    ids: frozenset[str]

    def allows(self, resource_id: str) -> bool:
        if self.all_ or resource_id in self.ids:
            return True
        # 「前缀*」授权（工具与知识库，见 vocab.PREFIX_WILDCARD_TYPES）
        return any(item.endswith(WILDCARD) and resource_id.startswith(item[:-1]) for item in self.ids)


NOTHING_VISIBLE = VisibleSet(all_=False, ids=frozenset())


def _new_enforcer(rules: Iterable[Rule]) -> casbin.Enforcer:
    model = FastModel(_INDEX_ORDER)
    model.load_model_from_text(MODEL_TEXT)
    enforcer = casbin.Enforcer(model)
    enforcer.add_function("actImplies", act_implies)
    # 带域角色管理器默认装了一个「名字相等」的匹配函数，语义上与不装相同，但它让每次 add_link
    # 都扫一遍全部角色（O(n²)），1000 条 g 规则时首次判定要 0.8 秒。主体名不用模式匹配，去掉它。
    enforcer.get_role_manager().matching_func = None
    unique = dict.fromkeys(rules)  # 去重且保序；批量接口遇到重复会整批失败
    links = [r for r in unique if r.ptype == "g"]
    _add_batch(enforcer, [r for r in unique if r.ptype == "p"], links)
    for domain in {r.workspace_id for r in links}:
        enforcer.get_roles_for_user_in_domain("", domain)  # 预建各域的角色图，避免首个请求承担
    return enforcer


def _add_batch(enforcer: casbin.Enforcer, policies: list[Rule], links: list[Rule]) -> None:
    # 走批量接口：单条 add_policy 每次都会对 FastPolicy 求 len（O(n)），1 万条时整体加载要十几秒
    if policies:
        enforcer.add_policies([list(r.values) for r in policies])
    if links:
        enforcer.add_named_grouping_policies("g", [list(r.values) for r in links])


def _add(enforcer: casbin.Enforcer, rule: Rule) -> None:
    if rule.ptype == "p":
        _add_batch(enforcer, [rule], [])
    else:
        _add_batch(enforcer, [], [rule])


def _remove(enforcer: casbin.Enforcer, rule: Rule) -> None:
    if rule.ptype == "p":
        enforcer.remove_policy(*rule.values)
    else:
        enforcer.remove_named_grouping_policy("g", *rule.values)


def _candidate_keys(obj: str) -> tuple[str, ...]:
    rtype, sep, _ = obj.partition(":")
    typed = (f"{rtype}:{WILDCARD}",) if sep else ()
    return (obj, *typed, WILDCARD)


def _prefix_key(rule: Rule) -> tuple[str, str] | None:
    """「前缀*」授权规则的（域, 资源）；其他规则返回 None。"""
    if rule.ptype != "p":
        return None
    domain, obj = rule.values[1], rule.values[2]
    ident = obj.partition(":")[2]
    return (domain, obj) if ident.endswith(WILDCARD) and ident != WILDCARD else None


class _Prefixes:
    """各空间里的「前缀*」资源及引用它的规则条数（同一资源可授给多个主体，删到 0 才移除）。"""

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._counts: dict[str, Counter[str]] = {}
        for rule in rules:
            self.add(rule)

    def add(self, rule: Rule) -> None:
        key = _prefix_key(rule)
        if key is not None:
            self._counts.setdefault(key[0], Counter())[key[1]] += 1

    def remove(self, rule: Rule) -> None:
        key = _prefix_key(rule)
        counts = self._counts.get(key[0]) if key is not None else None
        if counts is not None and counts[key[1]] > 0:
            counts[key[1]] -= 1
            counts += Counter()  # 去掉计数为 0 的项

    def matching(self, domain: str, obj: str) -> tuple[str, ...]:
        return tuple(key for key in self._counts.get(domain, ()) if obj.startswith(key[:-1]) and key != obj)


class PolicyEngine:
    def __init__(self) -> None:
        self._enforcer: casbin.Enforcer | None = None
        self._prefixes = _Prefixes()

    @property
    def healthy(self) -> bool:
        return self._enforcer is not None

    def replace(self, rules: Iterable[Rule]) -> None:
        """整体替换：先建好新判定器再切换引用，切换前的判定不受影响。"""
        rules = tuple(rules)
        enforcer, prefixes = _new_enforcer(rules), _Prefixes(rules)
        self._enforcer, self._prefixes = enforcer, prefixes

    def invalidate(self) -> None:
        self._enforcer = None

    def apply(self, change: RuleChange) -> None:
        enforcer = self._enforcer
        if enforcer is None or change.empty:
            return
        try:
            for rule in change.removed:
                _remove(enforcer, rule)
                self._prefixes.remove(rule)
            for rule in change.added:
                _add(enforcer, rule)
                self._prefixes.add(rule)
        except Exception:
            self.invalidate()
            raise

    def check(self, user_id: str, workspace_id: str, obj: str, action: str) -> bool:
        enforcer = self._enforcer
        if enforcer is None:
            return False
        try:
            return self._enforce(enforcer, user_key(user_id), workspace_id, obj, action)
        except Exception:
            log.exception("access check failed; denying")
            return False

    def _enforce(self, enforcer: casbin.Enforcer, sub: str, dom: str, obj: str, act: str) -> bool:
        policy = enforcer.get_model().model["p"]["p"].policy
        for key in (*_candidate_keys(obj), *self._prefixes.matching(dom, obj)):
            with fast_policy_filter(policy, dom, key):
                allowed, _ = enforcer.enforce_ex(sub, dom, obj, act)
            if allowed:
                return True
        return False

    def visible(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet:
        enforcer = self._enforcer
        if enforcer is None:
            return NOTHING_VISIBLE
        try:
            permissions = _implicit_permissions(enforcer, user_key(user_id), workspace_id)
        except Exception:
            log.exception("visible resource computation failed; returning nothing")
            return NOTHING_VISIBLE
        return _collect(permissions, workspace_id, resource_type, action)


def _implicit_permissions(enforcer: casbin.Enforcer, sub: str, domain: str) -> list[list[str]]:
    """与 get_implicit_permissions_for_user 同义，但只扫一遍策略：官方实现对每个角色各扫一遍全表。"""
    subjects = {sub, *enforcer.get_implicit_roles_for_user(sub, domain)}
    return [p for p in enforcer.get_filtered_policy(1, domain) if p[0] in subjects]


def _collect(permissions: list[list[str]], workspace_id: str, resource_type: str, action: str) -> VisibleSet:
    prefix = f"{resource_type}:"
    ids: set[str] = set()
    for _sub, dom, obj, act in (tuple(p[:4]) for p in permissions):
        if dom != workspace_id or not act_implies(action, act):
            continue
        if obj in (WILDCARD, f"{prefix}{WILDCARD}"):
            return VisibleSet(all_=True, ids=frozenset())
        if obj.startswith(prefix):
            ids.add(obj[len(prefix):])
    return VisibleSet(all_=False, ids=frozenset(ids))
