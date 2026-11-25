"""宿主的去处：共享宿主之外，用了上传插件的人各有一个专属宿主（用到才建、空闲回收）。

没开（KNOWWORK_PLUGIN_HOSTS=off，默认）时这个包不参与：业务服务只连共享宿主，热路径不变；
上传的插件这时只在本机对话（桌面端）里生效。
"""

from .assembly import PluginHosts, build_plugin_hosts
from .dedicated import DedicatedHosts, HostStatus, HostUnavailable
from .launcher import HostKey, HostLauncher, HostPlugin, ProcessLauncher
from .placement import StoredPlacement
from .routed import MemoryPlacement, Placement, RoutedKernel

__all__ = ["DedicatedHosts", "HostKey", "HostLauncher", "HostPlugin", "HostStatus", "HostUnavailable",
           "MemoryPlacement", "Placement", "PluginHosts", "ProcessLauncher", "RoutedKernel", "StoredPlacement", "build_plugin_hosts"]
