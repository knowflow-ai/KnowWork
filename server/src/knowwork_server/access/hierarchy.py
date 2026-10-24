"""资源层级钩子：资源可以有上级，在上级上的授权对下面的资源也成立（例如目录上的 use 覆盖目录里的知识库）。

开源版没有层级（NO_HIERARCHY）。扩展提供实现，启动时用 AccessCore.use_hierarchy 装上；它声明自己引入的上级资源
类型（例如目录），这些类型随之可以授权。不把上级的路径编进资源 id：资源移动时授权不用改写。

判定和 Casbin 判定器一样是同步的、在内存里做：实现方自己维护一份内存快照，数据变了就刷新。
实现抛出的任何异常都按「没有权限」处理（判权出错一律拒绝）。
"""

from typing import Protocol


class ResourceHierarchy(Protocol):
    resource_types: frozenset[str]  # 引入的上级资源类型，只能是小写字母、数字、下划线

    def ancestors(self, workspace_id: str, resource: str) -> tuple[str, ...]:
        """resource（形如 <类型>:<id>）的全部上级资源，由近到远；没有上级返回空。"""
        ...

    def descendants(self, workspace_id: str, ancestors: frozenset[str] | None, resource_type: str) -> frozenset[str]:
        """这些上级资源下面（含多层）resource_type 类型的资源 id。ancestors 为 None 表示全部上级（授了 <类型>:*）。"""
        ...


class _NoHierarchy:
    resource_types: frozenset[str] = frozenset()

    def ancestors(self, workspace_id: str, resource: str) -> tuple[str, ...]:
        return ()

    def descendants(self, workspace_id: str, ancestors: frozenset[str] | None, resource_type: str) -> frozenset[str]:
        return frozenset()


NO_HIERARCHY: ResourceHierarchy = _NoHierarchy()
