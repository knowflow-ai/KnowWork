/** 技能列表的显示规则：状态（同一含义全站同一颜色）与搜索。 */
import type { Skill } from '../../../api/skills';

export type SkillState = 'published' | 'draft' | 'disabled';

/** 管理表格里的状态圆点（界面规范 3.3）。 */
export const SKILL_STATE: Record<SkillState, { status: 'success' | 'default' | 'warning'; text: string }> = {
  published: { status: 'success', text: '已发布' },
  draft: { status: 'warning', text: '草稿' },
  disabled: { status: 'default', text: '已停用' },
};

/** 停用优先：停用的技能不管有没有发布，对话里都用不了。 */
export function skillState(skill: Skill): SkillState {
  if (!skill.enabled) return 'disabled';
  return skill.status === 'published' ? 'published' : 'draft';
}

/** 草稿里有还没发布的改动（包括从没发布过）。 */
export const canPublish = (skill: Skill): boolean => skill.status === 'draft' || skill.hasUnpublishedChanges;

/** 按名称或说明搜索。 */
export function filterSkills(rows: readonly Skill[], keyword: string): readonly Skill[] {
  const k = keyword.trim().toLowerCase();
  if (!k) return rows;
  return rows.filter((r) => r.name.toLowerCase().includes(k) || r.description.toLowerCase().includes(k));
}

/** 发布时默认用技能包里写的版本号，没写就让服务端自动编号。 */
export const defaultVersion = (skill: Skill): string => skill.draft?.version ?? '';
