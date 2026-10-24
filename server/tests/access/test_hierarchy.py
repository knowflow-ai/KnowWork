"""资源层级钩子：上级（目录）上的授权覆盖下面的资源；层级引入的类型能授权；钩子出错一律拒绝。"""

import pytest

from knowwork_server.access.vocab import InvalidInput

pytestmark = pytest.mark.anyio

# 目录树：f_root ⊃ f_legal ⊃ 知识库 lib.contracts；f_root ⊃ 知识库 lib.handbook；lib.loose 不在任何目录
PARENTS = {"knowledge_base:lib.contracts": ("knowledge_folder:f_legal", "knowledge_folder:f_root"),
           "knowledge_base:lib.handbook": ("knowledge_folder:f_root",)}


class Tree:
    resource_types = frozenset({"knowledge_folder"})

    def __init__(self) -> None:
        self.broken = False

    def ancestors(self, workspace_id, resource):
        if self.broken:
            raise RuntimeError("目录快照坏了")
        return PARENTS.get(resource, ())

    def descendants(self, workspace_id, ancestors, resource_type):
        if self.broken:
            raise RuntimeError("目录快照坏了")
        prefix = f"{resource_type}:"
        return frozenset(key[len(prefix):] for key, uppers in PARENTS.items()
                         if key.startswith(prefix) and (ancestors is None or set(uppers) & ancestors))


async def test_folder_types_are_unknown_until_a_hierarchy_is_installed(service, ws):
    with pytest.raises(InvalidInput):
        await service.grant("alice", ws, "user:dave", "knowledge_folder:f_legal", "use")
    assert not service.check("dave", ws, "knowledge_folder:f_legal", "use")


async def test_grant_on_a_folder_covers_bases_below(service, ws):
    tree = Tree()
    service.use_hierarchy(tree)
    await service.grant("alice", ws, "user:dave", "knowledge_folder:f_legal", "use")
    assert service.check("dave", ws, "knowledge_base:lib.contracts", "use")
    assert not service.check("dave", ws, "knowledge_base:lib.contracts", "edit"), "档位不超过上级授的"
    assert not service.check("dave", ws, "knowledge_base:lib.handbook", "use"), "兄弟目录不受影响"
    assert service.visible_resources("dave", ws, "knowledge_base", "use").ids == {"lib.contracts"}
    await service.grant("alice", ws, "user:dave", "knowledge_folder:f_root", "edit")
    assert service.check("dave", ws, "knowledge_base:lib.contracts", "edit"), "祖先任一层有权即可"
    assert service.visible_resources("dave", ws, "knowledge_base", "use").ids == {"lib.contracts", "lib.handbook"}
    assert not service.check("dave", ws, "knowledge_base:lib.loose", "use")


async def test_wildcard_folder_grant_and_direct_grants_add_up(service, ws):
    service.use_hierarchy(Tree())
    await service.grant("alice", ws, "user:dave", "knowledge_folder:*", "use")
    await service.grant("alice", ws, "user:dave", "knowledge_base:lib.loose", "use")
    visible = service.visible_resources("dave", ws, "knowledge_base", "use")
    assert not visible.all_ and visible.ids == {"lib.contracts", "lib.handbook", "lib.loose"}


async def test_hierarchy_failures_deny(service, ws):
    tree = Tree()
    service.use_hierarchy(tree)
    await service.grant("alice", ws, "user:dave", "knowledge_folder:f_legal", "use")
    await service.grant("alice", ws, "user:dave", "knowledge_base:lib.loose", "use")
    tree.broken = True
    assert not service.check("dave", ws, "knowledge_base:lib.contracts", "use")
    assert service.check("dave", ws, "knowledge_base:lib.loose", "use"), "直接授权不受影响"
    assert service.visible_resources("dave", ws, "knowledge_base", "use").ids == {"lib.loose"}


async def test_hierarchy_cannot_shadow_builtin_types_or_be_installed_twice(service):
    class Bad(Tree):
        resource_types = frozenset({"skill"})

    with pytest.raises(ValueError):
        service.use_hierarchy(Bad())
    service.use_hierarchy(Tree())
    with pytest.raises(ValueError):
        service.use_hierarchy(Tree())
