"""技能门的安装期规则。

- 专业工具 = 技能 allowed-tools 里不属于基础工具的那些；基础工具（DSH 内置的 skill、subagent，
  业务服务的通用工具）任何时候都可用，列进 allowed-tools 也不会被认领；
- 一个专业工具只能被一个技能认领；
- 解锁了专业工具却没声明 completion-tools 的技能直接拒绝：否则加载它的回合永远满足不了完成判据。
"""

from collections.abc import Mapping

from .errors import PackageInvalid, SkillConflict
from .manifest import SkillManifest

# 默认的基础工具：DSH 官方内置工具与业务服务的通用工具。主线可以按部署再追加。
DEFAULT_BASIC_TOOLS: frozenset[str] = frozenset({"skill", "subagent", "kw_now"})


def specialized_tools(manifest: SkillManifest, basic_tools: frozenset[str]) -> tuple[str, ...]:
    return tuple(tool for tool in manifest.allowed_tools if tool not in basic_tools)


def check_claims(manifest: SkillManifest, basic_tools: frozenset[str],
                 claimed_by: Mapping[str, str]) -> tuple[str, ...]:
    """校验并返回本技能认领的专业工具。claimed_by：同一范围内其他技能已认领的「工具 → 技能名」。"""
    claims = specialized_tools(manifest, basic_tools)
    if claims and not manifest.completion_tools:
        raise PackageInvalid("completion_tools_required",
                             f"技能解锁了专业工具 {claims[0]}，必须声明 completion-tools", file="SKILL.md",
                             field="completion-tools")
    for tool in claims:
        owner = claimed_by.get(tool)
        if owner is not None and owner != manifest.name:
            raise SkillConflict("tool_already_claimed", f"工具 {tool} 已被技能 {owner} 认领，一个工具只能属于一个技能")
    return claims
