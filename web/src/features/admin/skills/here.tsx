/**
 * 企业技能、内置技能在本空间的设置：「给谁用」的说法、本空间的两个操作（改给谁用、停用或启用）与改给谁用的对话框。
 * 技能页「企业」页签的两种视图共用：空间管理员的（UpperSkills.tsx）与扩展给系统管理员的管理视图。
 */
import { useState, type ReactNode } from 'react';
import type { UpperSkill } from '../../../api/skills';
import type { Grant } from '../../../api/workspace';
import type { RowAction } from '../common';
import { EVERYONE, useGrantsList, usersOf, usersText } from '../grants/users';
import { UsersDialog } from '../grants/UsersSelect';
import { useUpperSkillActions } from './queries';

export const SOURCE_TEXT = { platform: '企业', bundled: '内置' } as const;

/** 在本空间给谁用：停用了、全体成员、收窄到几个对象（一个都没有就是只有管理员）。 */
export function hereText(skill: UpperSkill, grants: readonly Grant[] | undefined): string {
  if (skill.off) return '本空间已停用';
  return skill.narrowed ? usersText(usersOf(grants, skill.resource)) : '全体成员';
}

/** 本空间的两个操作（改给谁用、停用或启用）和改给谁用的对话框；企业页签的两种视图共用。 */
export function useHereActions(): {
  readonly actionsOf: (skill: UpperSkill) => readonly RowAction[];
  readonly dialog: ReactNode;
} {
  const { toggle, users } = useUpperSkillActions();
  const grants = useGrantsList();
  const [editing, setEditing] = useState<UpperSkill>();
  const actionsOf = (skill: UpperSkill): readonly RowAction[] => [
    { key: 'users', label: '给谁用', onClick: () => setEditing(skill) },
    {
      key: 'toggle',
      label: skill.off ? '在本空间启用' : '在本空间停用',
      disabled: toggle.pending,
      onClick: () => void toggle.run({ key: skill.key, off: !skill.off }),
    },
  ];
  const dialog = editing ? (
    <UsersDialog
      title={`「${editing.name}」在本空间给谁用`}
      initial={editing.narrowed ? usersOf(grants.data, editing.resource) : [EVERYONE]}
      pending={users.pending}
      onSave={(subjects) => users.run({ key: editing.key, subjects })}
      onClose={() => setEditing(undefined)}
    />
  ) : null;
  return { actionsOf, dialog };
}

export const HERE_NOTE =
  '企业技能由系统管理员维护，内置技能是产品自带的。默认本空间全体成员都能用；可以在本空间停用或改给谁用，不能改内容。';
