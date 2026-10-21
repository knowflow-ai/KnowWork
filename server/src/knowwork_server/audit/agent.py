"""Agent 操作的审计：工具执行由 ToolService 的审计回调送进来，不解析事件流原文。

操作者类型是 agent，actor_id 是会话所属用户（Agent 替谁在做事），凭证类型是工具令牌，
session_id 是业务会话，detail 里带工具名、风险等级、动作回执 id、宿主侧发起调用的会话与参数摘要。
审批决定是人的操作，走 HTTP 中间件，动作名 approval.decide，detail 里带 allow。
"""

from ..tools.service import ToolAuditHook, ToolExecution
from .events import AuditEvent
from .sink import AuditSink, safe_record

_RESULTS = {"succeeded": "success", "failed": "failed", "denied": "denied"}


def tool_event(execution: ToolExecution) -> AuditEvent:
    detail = {"toolName": execution.tool_name, "risk": execution.risk, "actionId": execution.action_id,
              "agentSessionId": execution.agent_session_id, "argsDigest": execution.arguments_digest,
              "code": execution.code}
    return AuditEvent(
        workspace_id=execution.workspace_id, actor_type="agent", actor_id=execution.user_id,
        credential_type="tool_token", action="tool.execute", resource_type="tool", resource_id=execution.tool_name,
        result=_RESULTS[execution.outcome], session_id=execution.session_id,
        detail={key: value for key, value in detail.items() if value is not None},
    )


def tool_audit_hook(sink: AuditSink) -> ToolAuditHook:
    async def hook(execution: ToolExecution) -> None:
        await safe_record(sink, tool_event(execution))

    return hook
