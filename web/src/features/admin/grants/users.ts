/** 「给谁用」的选项与显示规则（UsersSelect 用；技能、MCP 服务共用）。 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  listGrants,
  memberLabel,
  peopleOnly,
  setResourceUsers,
  type Department,
  type Grant,
} from '../../../api/workspace';
import type { TreeSelectNode } from '../../../ui/TreeSelect';
import { KEYS, useDepartments, useGroups, useMembers } from '../queries';
import { useAction } from '../useAction';

/** 「全体成员」：成员角色（所有者、管理员本来就有全部资源的权限）。 */
export const EVERYONE = 'role:editor';

/** 部门按层级成树：平铺时看不出下级部门属于谁，同名部门也分不清。 */
function departmentNodes(tree: readonly Department[]): TreeSelectNode[] {
  return tree.map((d) => ({ key: `dept:${d.id}`, label: d.name, children: departmentNodes(d.children) }));
}

/** 「给谁用」的选择树：全体成员一项，部门、用户组、成员三组（组本身不能选，只展开收起），空的组不出现。 */
export function useSubjectTree(): readonly TreeSelectNode[] {
  const members = useMembers();
  const departments = useDepartments();
  const groups = useGroups();
  return useMemo(() => {
    const kind = (key: string, label: string, children: readonly TreeSelectNode[]): TreeSelectNode => ({
      key,
      label,
      selectable: false,
      children,
    });
    return [
      { key: EVERYONE, label: '全体成员', children: [] },
      kind('kind:dept', '部门', departmentNodes(departments.data ?? [])),
      kind(
        'kind:group',
        '用户组',
        (groups.data ?? []).map((g) => ({ key: `group:${g.id}`, label: g.name, children: [] })),
      ),
      kind(
        'kind:user',
        '成员',
        peopleOnly(members.data ?? []).map((m) => ({
          key: `user:${m.userId}`,
          label: memberLabel(m),
          children: [],
        })),
      ),
    ].filter((node) => node.selectable !== false || node.children.length > 0);
  }, [members.data, departments.data, groups.data]);
}

/** 一个资源现在「可用」一档给了谁（从全部授权里挑）。 */
export function usersOf(grants: readonly Grant[] | undefined, resource: string): readonly string[] {
  return (grants ?? []).filter((g) => g.resource === resource && g.action === 'use').map((g) => g.subject);
}

/** 列表里一句话说清给谁用。 */
export function usersText(subjects: readonly string[]): string {
  if (subjects.includes(EVERYONE)) return '全体成员';
  if (subjects.length === 0) return '只有管理员';
  return `${subjects.length} 个对象`;
}

/** 本空间全部授权（列表里「给谁用」一列、详情里改「给谁用」都从这里取）。 */
export const useGrantsList = () => useQuery({ queryKey: KEYS.grants, queryFn: listGrants });

/** 保存一个资源「给谁用」（PUT /grants/users，只换「可用」一档）。 */
export function useUsersAction() {
  return useAction(
    ({ resource, subjects }: { resource: string; subjects: readonly string[] }) =>
      setResourceUsers(resource, subjects),
    { invalidate: [KEYS.grants], success: '已保存给谁用', failure: '没有保存成功' },
  );
}
