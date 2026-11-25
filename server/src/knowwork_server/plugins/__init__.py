"""插件：DSH 宿主里的官方能力，按空间、按人开关（管理后台「能力接入 → 插件」、个人设置「我的插件」）。

预置插件是我们审过、随版本发布的；管理员给每个定档（关闭、全员启用、可选），成员在可选的里面自己开关。每次编运行配置时
算出这个人生效的插件键，写进开会话的合同，宿主按它装官方 Agent 预设。方案见插件方案文档。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from .api import build_me_router, build_router
from .catalog import CATALOG, CatalogPlugin
from .service import PluginService
from .uploads import RESOURCE_TYPE, Access, Blobs, UploadService
from .uploads_api import build_my_uploads_router, build_uploads_router

PLUGIN_RLS_TABLES = ("plugin_installation", "plugin_switch", "plugin_workspace_setting")


@dataclass(frozen=True)
class Plugins:
    service: PluginService  # 预置插件的档与个人开关
    uploads: UploadService  # 上传的插件：空间插件与个人插件


def build_plugins(sessionmaker: async_sessionmaker, access: Access, blobs: Blobs, *, cloud: bool = False) -> Plugins:
    """sessionmaker 要用按空间的工厂（行级安全）；blobs 是放插件压缩包的对象存储；cloud 是这个部署开没开个人专属宿主。
    空间插件「给谁用」走资源授权，这里登记它的资源类型 plugin（plugin:<插件编号>）。"""
    register = getattr(access, "add_resource_types", None)
    if register is not None:
        register(frozenset({RESOURCE_TYPE}))
    return Plugins(service=PluginService(sessionmaker, access), uploads=UploadService(sessionmaker, access, blobs, cloud=cloud))


__all__ = ["CATALOG", "PLUGIN_RLS_TABLES", "CatalogPlugin", "PluginService", "Plugins", "UploadService",
           "build_me_router", "build_my_uploads_router", "build_plugins", "build_router", "build_uploads_router"]
