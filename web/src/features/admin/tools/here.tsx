/**
 * 企业 MCP 在本空间的设置：「给谁用」的说法、本空间的两个操作（改给谁用、停用或启用）与改给谁用的对话框。
 * 空间管理员的「企业」页签（HereMcp.tsx）与扩展给系统管理员的管理视图共用。
 */
import { useState, type ReactNode } from 'react';
import type { EnterpriseHere } from '../../../api/mcp';
import type { Grant } from '../../../api/workspace';
import type { RowAction } from '../common';
import { EVERYONE, useGrantsList, usersOf, usersText } from '../grants/users';
import { UsersDialog } from '../grants/UsersSelect';
import { useEnterpriseHereActions } from './queries';

/** 在本空间给谁用：停用了、全体成员、收窄到几个对象（一个都没有就是只有管理员）。 */
export function hereText(here: EnterpriseHere, grants: readonly Grant[] | undefined): string {
  if (here.off) return '本空间已停用';
  return here.narrowed ? usersText(usersOf(grants, here.resource)) : '全体成员';
}

export function useHereActions(): {
  readonly actionsOf: (here: EnterpriseHere) => readonly RowAction[];
  readonly dialog: ReactNode;
} {
  const { toggle, users } = useEnterpriseHereActions();
  const grants = useGrantsList();
  const [editing, setEditing] = useState<EnterpriseHere>();
  const actionsOf = (here: EnterpriseHere): readonly RowAction[] => [
    { key: 'users', label: '给谁用', onClick: () => setEditing(here) },
    {
      key: 'toggle-here',
      label: here.off ? '在本空间启用' : '在本空间停用',
      disabled: toggle.pending,
      onClick: () => void toggle.run({ serverId: here.serverId, off: !here.off }),
    },
  ];
  const dialog = editing ? (
    <UsersDialog
      title={`「${editing.title}」在本空间给谁用`}
      initial={editing.narrowed ? usersOf(grants.data, editing.resource) : [EVERYONE]}
      pending={users.pending}
      onSave={(subjects) => users.run({ serverId: editing.serverId, subjects })}
      onClose={() => setEditing(undefined)}
    />
  ) : null;
  return { actionsOf, dialog };
}

export const HERE_NOTE =
  '企业 MCP 由系统管理员登记、发到本空间。默认本空间全体成员都能用；可以在本空间停用或改给谁用，不能改服务本身与工具的设置。';
