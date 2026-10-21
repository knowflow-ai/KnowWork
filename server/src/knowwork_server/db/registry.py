"""全部持久化模型的登记处。

迁移与自动建表都要先导入所有模型模块，表才会挂到 Base.metadata 上；新增模型包时在这里登记一行。
RLS_TABLES 汇总各包声明的、需要按工作空间做行级安全的表。
"""

from importlib import import_module

from .base import Base

MODEL_MODULES = (
    "knowwork_server.identity.models",
    "knowwork_server.access.models",
    "knowwork_server.tools.models",
    "knowwork_server.tools.mcp.models",
    "knowwork_server.conversations.models",
    "knowwork_server.models.tables",
    "knowwork_server.knowledge.tables",
    "knowwork_server.setup.models",
    "knowwork_server.system.models",
    "knowwork_server.skills.models",
    "knowwork_server.audit.models",
    "knowwork_server.attachments.models",
    "knowwork_server.artifacts.models",
    "knowwork_server.profile.models",
    "knowwork_server.workflows.models",
    "knowwork_server.approvals.models",
)

# 带 workspace_id 的业务表。identity 的表是全局的（账号跨空间），不在此列。
RLS_TABLES = (
    "workspace_member",
    "department",
    "department_member",
    "user_group",
    "user_group_member",
    "casbin_rule",
    "action_receipt",
    "action_result_blob",
    "mcp_server",
    "conversation",
    "run_event",
    "model_provider",
    "model",
    "model_group",
    "model_group_item",
    "usage_record",
    "usage_daily",
    "knowledge_source",
    "citation_catalog",
    "citation",
    "skill",
    "skill_release",
    "skill_run",
    "skill_step_run",
    "audit_log",
    "attachment",
    "attachment_parse_job",
    "artifact",
    "runtime_profile",
    "kernel_binding",
    "workflow_run",
    "workflow_step",
    "workflow_step_cache",
    "approval_grant",
)


def load_all_models():
    for name in MODEL_MODULES:
        import_module(name)
    return Base.metadata
