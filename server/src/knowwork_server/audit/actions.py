"""写接口到审计动作的映射：显式登记，不靠路径猜语义。

键是（方法, 路由模板）；值给出动作名、资源类型与取资源 id 的路径参数。没登记的写接口仍会记一条，
动作名按路由的最后一个固定段与方法推出（例如 `widgets.create`），保证新接口不会漏审计；
新增接口时在这里补一行，让动作名稳定可查。
"""

from dataclasses import dataclass

WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_VERBS = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}


@dataclass(frozen=True)
class ActionSpec:
    action: str
    resource_type: str | None = None
    id_param: str | None = None
    query_detail: tuple[str, ...] = ()  # 记进 detail 的查询参数（只登记非敏感的定位参数）


W = "/api/workspaces/{workspace_id}"
C = W + "/conversations/{conversation_id}"
K = W + "/knowledge-sources"
M = W + "/mcp-servers"

ROUTE_ACTIONS: dict[tuple[str, str], ActionSpec] = {
    ("POST", "/api/auth/login"): ActionSpec("auth.login", "user"),
    ("POST", "/api/auth/logout"): ActionSpec("auth.logout", "user"),
    ("GET", "/api/auth/oidc/callback"): ActionSpec("auth.oidc_login", "user"),
    ("POST", W + "/invitations"): ActionSpec("invitation.create", "invitation"),
    ("DELETE", W + "/invitations/{invitation_id}"): ActionSpec("invitation.revoke", "invitation", "invitation_id"),
    ("POST", "/api/auth/invitations/accept"): ActionSpec("invitation.accept", "invitation"),
    ("POST", "/api/auth/api-keys"): ActionSpec("api_key.create", "api_key"),
    ("DELETE", "/api/auth/api-keys/{key_id}"): ActionSpec("api_key.revoke", "api_key", "key_id"),
    ("POST", W + "/members"): ActionSpec("member.add", "member"),
    ("PATCH", W + "/members/{user_id}"): ActionSpec("member.update_role", "member", "user_id"),
    ("DELETE", W + "/members/{user_id}"): ActionSpec("member.remove", "member", "user_id"),
    ("POST", W + "/departments"): ActionSpec("department.create", "department"),
    ("PATCH", W + "/departments/{dept_id}"): ActionSpec("department.update", "department", "dept_id"),
    ("DELETE", W + "/departments/{dept_id}"): ActionSpec("department.delete", "department", "dept_id"),
    ("POST", W + "/departments/{dept_id}/members"): ActionSpec("department.member_add", "department", "dept_id"),
    ("DELETE", W + "/departments/{dept_id}/members/{user_id}"):
        ActionSpec("department.member_remove", "department", "dept_id"),
    ("POST", W + "/groups"): ActionSpec("group.create", "group"),
    ("PATCH", W + "/groups/{group_id}"): ActionSpec("group.rename", "group", "group_id"),
    ("DELETE", W + "/groups/{group_id}"): ActionSpec("group.delete", "group", "group_id"),
    ("POST", W + "/groups/{group_id}/members"): ActionSpec("group.member_add", "group", "group_id"),
    ("DELETE", W + "/groups/{group_id}/members/{user_id}"): ActionSpec("group.member_remove", "group", "group_id"),
    ("POST", W + "/grants"): ActionSpec("grant.create", "grant"),
    ("DELETE", W + "/grants"): ActionSpec("grant.revoke", "grant", query_detail=("subject", "resource")),
    ("POST", W + "/model-providers"): ActionSpec("model_provider.create", "model_provider"),
    ("PATCH", W + "/model-providers/{provider_id}"): ActionSpec("model_provider.update", "model_provider", "provider_id"),
    ("DELETE", W + "/model-providers/{provider_id}"):
        ActionSpec("model_provider.delete", "model_provider", "provider_id"),
    ("POST", W + "/models"): ActionSpec("model.create", "model"),
    ("PATCH", W + "/models/{model_id}"): ActionSpec("model.update", "model", "model_id"),
    ("DELETE", W + "/models/{model_id}"): ActionSpec("model.delete", "model", "model_id"),
    ("POST", W + "/models/{model_id}/test"): ActionSpec("model.test", "model", "model_id"),
    ("POST", K): ActionSpec("knowledge_source.create", "knowledge_source"),
    ("PATCH", K + "/{source_id}"): ActionSpec("knowledge_source.update", "knowledge_source", "source_id"),
    ("DELETE", K + "/{source_id}"): ActionSpec("knowledge_source.delete", "knowledge_source", "source_id"),
    ("POST", K + "/{source_id}/test"): ActionSpec("knowledge_source.test", "knowledge_source", "source_id"),
    ("POST", M): ActionSpec("mcp_server.create", "mcp_server"),
    ("PATCH", M + "/{server_id}"): ActionSpec("mcp_server.update", "mcp_server", "server_id"),
    ("DELETE", M + "/{server_id}"): ActionSpec("mcp_server.delete", "mcp_server", "server_id"),
    ("POST", M + "/{server_id}/sync"): ActionSpec("mcp_server.sync", "mcp_server", "server_id"),
    ("PATCH", M + "/{server_id}/tools/{tool_name}"): ActionSpec("mcp_tool.update", "mcp_server", "server_id"),
    ("POST", W + "/conversations"): ActionSpec("conversation.create", "conversation"),
    ("PATCH", C): ActionSpec("conversation.rename", "conversation", "conversation_id"),
    ("DELETE", C): ActionSpec("conversation.delete", "conversation", "conversation_id"),
    ("POST", C + "/messages"): ActionSpec("conversation.send", "conversation", "conversation_id"),
    ("POST", C + "/cancel"): ActionSpec("conversation.cancel", "conversation", "conversation_id"),
    ("POST", C + "/approvals/{approval_id}"): ActionSpec("approval.decide", "approval", "approval_id"),
    ("POST", C + "/questions/{question_id}"): ActionSpec("question.answer", "question", "question_id"),
    ("POST", "/api/setup"): ActionSpec("setup.initialize", "workspace"),
    ("PUT", "/api/system/brand"): ActionSpec("system.brand_update", "system"),
    ("PUT", "/api/system/brand/logo"): ActionSpec("system.logo_update", "system"),
    ("DELETE", "/api/system/brand/logo"): ActionSpec("system.logo_reset", "system"),
}


def _derived(method: str, route: str) -> ActionSpec:
    segments = [s for s in route.split("/") if s and s != "api"]
    fixed = [s for s in segments if not s.startswith("{")]
    params = [s[1:-1] for s in segments if s.startswith("{") and s != "{workspace_id}"]
    noun = (fixed[-1] if fixed else "unknown").replace("-", "_")
    trailing_param = bool(segments) and segments[-1].startswith("{")
    id_param = params[-1] if params and trailing_param else None
    return ActionSpec(f"{noun}.{_VERBS.get(method, method.lower())}", noun, id_param)


def action_for(method: str, route: str) -> ActionSpec:
    return ROUTE_ACTIONS.get((method, route)) or _derived(method, route)
