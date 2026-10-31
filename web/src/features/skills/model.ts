/**
 * 「技能」页（使用端）的显示规则：状态只写文字不上色（界面规范 2.11），没生效的写明原因，提交到空间的进度。
 */
import {
  missingEnv,
  type AvailableSkill,
  type InactiveReason,
  type Skill,
  type SkillSource,
} from '../../api/skills';

const INACTIVE_TEXT: Record<InactiveReason, string> = {
  name_taken: '有同名的空间技能或企业技能，对话里用的是那一份',
  tool_claimed: '要解锁的工具已被空间技能或企业技能占用',
  limit: '能用的技能太多，超出了上限',
  invalid: '技能包内容不完整',
};

export interface StateText {
  readonly text: string;
  readonly note?: string;
}

/** 我的技能在对话里的状态：停用优先，其次是没生效的原因。 */
export function mySkillState(skill: Skill): StateText {
  if (!skill.enabled) return { text: '已停用' };
  if (skill.inactive) return { text: '没生效', note: INACTIVE_TEXT[skill.inactive] };
  const missing = missingEnv(skill);
  if (missing.length > 0)
    return { text: '已启用', note: `还没填环境变量 ${missing.join('、')}，用到时会出错` };
  return { text: '已启用' };
}

/** 提交到空间的进度；从没提交过返回 undefined。 */
export function submissionState(skill: Skill): StateText | undefined {
  const submission = skill.submission;
  if (!submission) return undefined;
  switch (submission.status) {
    case 'pending':
      return { text: '待审核', note: `提交的是 ${submission.version}` };
    case 'approved':
      return skill.publishedVersion && skill.publishedVersion !== submission.version
        ? { text: '已进空间', note: `空间里是 ${submission.version}，可以提交新版本` }
        : { text: '已进空间', note: submission.version };
    case 'rejected':
      return { text: '未通过', note: submission.reviewNote };
    default:
      return { text: '已撤回' };
  }
}

/** 有待审的提交：可以撤回。 */
export const hasPending = (skill: Skill): boolean => skill.submission?.status === 'pending';

/** 能提交到空间：已发布，且当前这一版没在待审、也没已经进了空间。 */
export function canSubmit(skill: Skill): boolean {
  if (skill.status !== 'published') return false;
  const submission = skill.submission;
  if (!submission) return true;
  const same = submission.version === skill.publishedVersion;
  return !(same && (submission.status === 'pending' || submission.status === 'approved'));
}

export const SOURCE_TEXT: Record<SkillSource, string> = {
  platform: '企业',
  workspace: '空间',
  personal: '我的',
  bundled: '内置',
};

/** 按名称或说明搜索。 */
export function filterByKeyword<T extends Pick<AvailableSkill, 'name' | 'description'>>(
  rows: readonly T[],
  keyword: string,
): readonly T[] {
  const k = keyword.trim().toLowerCase();
  if (!k) return rows;
  return rows.filter((r) => r.name.toLowerCase().includes(k) || r.description.toLowerCase().includes(k));
}
