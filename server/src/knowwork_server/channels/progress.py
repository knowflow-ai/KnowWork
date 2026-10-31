"""智能体在 IM 里的进度行：每一步一行，过渡说明优先，没有就用工具的人话名字加目标（叫法同网页的步骤标签
web/src/features/chat/events/stepLabels.ts）。

IM 里一条消息只能整段替换，没有网页那样的步骤区：过程收进消息上方的引用块，
只追加不替换，答完整条换成只有回答。模型的思考内容（reasoning）不推：IM 里没法折叠，原文又长，还可能带着
检索到的片段，群里全员可见。
"""

import json
import re

MAX_LINE = 80
MAX_LINES = 6  # 只留最近几步，步数多时消息不至于被进度占满

LABELS = {
    "knowledge_search": "检索知识库",
    "knowledge_read": "阅读文档",
    "knowledge_bases": "查看知识库",
    "knowledge_gap": "记下知识库缺口",
    "bash": "运行命令",
    "read": "读取文件",
    "write": "写入文件",
    "edit": "修改文件",
    "present": "交付文件",
    "kw_now": "获取当前时间",
    "skill": "加载技能",
    "subagent": "交给子任务",
}
# 目标取参数里固定的一项；文档、文件这类内部引用不给 IM 里的人看
TARGET_KEY = {"knowledge_search": "query", "knowledge_gap": "missing", "bash": "description", "skill": "name"}
_MCP = re.compile(r"^mcp__(?:ent_)?([a-z][a-z0-9-]*)__(.+)$")  # 企业 MCP 的 ent_ 是内部前缀，不给人看


def one_line(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"


def tool_line(name: str, arguments: object) -> str:
    mcp = _MCP.match(name)
    label = LABELS.get(name) or (f"{mcp[1]} · {mcp[2]}" if mcp else "调用工具")
    key = TARGET_KEY.get(name)
    if not key:
        return label
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return label
    target = args.get(key) if isinstance(args, dict) else None
    return f"{label}：{one_line(target)}" if isinstance(target, str) and target.strip() else label


def render(lines: list[str]) -> str:
    return "\n".join(f"> {line}" for line in lines[-MAX_LINES:])
