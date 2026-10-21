/** 部门与用户组页面的纯函数。 */
import { memberLabel, type Department, type Member } from '../../../api/workspace';
import type { TreeNode } from '../../../ui/Tree';

export function toTreeData(nodes: readonly Department[]): TreeNode[] {
  return nodes.map((node) => ({
    key: node.id,
    title: `${node.name}（${node.members.length}）`,
    children: toTreeData(node.children),
  }));
}

/** 按 id 找成员显示名，找不到时显示 id。 */
export function nameOf(members: readonly Member[], userId: string): string {
  const member = members.find((m) => m.userId === userId);
  return member ? memberLabel(member) : userId;
}
