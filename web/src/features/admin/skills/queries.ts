/** 技能管理页的查询与变更：空间技能，以及成员提交上来待审核的个人技能。 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import {
  approveSubmission,
  deleteSkill,
  getSkill,
  getSubmission,
  installSkill,
  listPendingSubmissions,
  listSkills,
  listUpperSkills,
  publishSkill,
  rejectSubmission,
  replaceDraft,
  setSkillEnabled,
  setSkillEnv,
  setUpperSkillOff,
  setUpperSkillUsers,
  type Skill,
} from '../../../api/skills';
import { ApiError } from '../../../api/http';
import { setResourceUsers } from '../../../api/workspace';
import { listSkillRuns, skillRunSummary, type SkillRunStatus } from '../../../api/skillRuns';
import { KEYS } from '../queries';
import { useAction } from '../useAction';

export const SKILL_KEYS = {
  list: ['admin', 'skills'],
  detail: (id: string) => ['admin', 'skills', id],
  pending: ['admin', 'skill-submissions'],
  upper: ['admin', 'platform-skills'],
  submission: (id: string) => ['admin', 'skill-submissions', id],
} as const;

export const useSkills = () =>
  useQuery({ queryKey: SKILL_KEYS.list, queryFn: () => listSkills('workspace') });

export const useSkillDetail = (id: string | undefined) =>
  useQuery({
    queryKey: SKILL_KEYS.detail(id ?? ''),
    queryFn: () => getSkill(id!, 'workspace'),
    enabled: id !== undefined,
  });

const DAY_MS = 24 * 60 * 60 * 1000;
/** 时间段在取数时才按当下往前推（渲染时不读时钟）。 */
const sinceOf = (days: string) => new Date(Date.now() - Number(days) * DAY_MS).toISOString();

export interface SkillRunQuery {
  readonly days: string;
  readonly status?: SkillRunStatus;
  readonly version?: string;
}

export const useSkillRunSummary = (skillId: string, days: string) =>
  useQuery({
    queryKey: [...SKILL_KEYS.detail(skillId), 'runs', 'summary', days],
    queryFn: () => skillRunSummary(skillId, sinceOf(days)),
  });

export const useSkillRuns = (skillId: string, { days, status, version }: SkillRunQuery) =>
  useInfiniteQuery({
    queryKey: [...SKILL_KEYS.detail(skillId), 'runs', days, status, version],
    queryFn: ({ pageParam }) => listSkillRuns(skillId, { since: sinceOf(days), status, version }, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });

const invalidate = [SKILL_KEYS.list, ['admin', 'skills']] as const;

export function useSkillActions() {
  return {
    install: useAction((file: File) => installSkill(file, 'workspace'), {
      invalidate,
      success: '技能已上传为草稿，发布后成员才能使用',
      failure: '技能包没有上传成功',
    }),
    replace: useAction(({ skill, file }: { skill: Skill; file: File }) => replaceDraft(skill, file), {
      invalidate,
      success: '已更新草稿，发布后生效',
      failure: '新版本没有上传成功',
    }),
    publish: useAction(
      ({ skill, version }: { skill: Skill; version?: string }) => publishSkill(skill, version),
      {
        invalidate,
        success: '已发布，新对话里就能用了',
        failure: '没有发布成功',
      },
    ),
    toggle: useAction(
      ({ skill, enabled }: { skill: Skill; enabled: boolean }) => setSkillEnabled(skill, enabled),
      {
        invalidate,
        failure: '没有切换成功',
      },
    ),
    remove: useAction((skill: Skill) => deleteSkill(skill), {
      invalidate,
      success: '已删除',
      failure: '没有删除成功',
    }),
    env: useAction(
      ({ skill, values }: { skill: Skill; values: Record<string, string | null> }) =>
        setSkillEnv(skill, values),
      { invalidate, success: '已保存，新对话里加载这个技能后生效', failure: '环境变量没有保存成功' },
    ),
  };
}

/** 待审核的提交（技能页的标签与侧栏菜单上的数都用它）。 */
export const usePendingSubmissions = (enabled = true) =>
  useQuery({ queryKey: SKILL_KEYS.pending, queryFn: listPendingSubmissions, enabled });

export const useSubmission = (id: string | undefined) =>
  useQuery({
    queryKey: SKILL_KEYS.submission(id ?? ''),
    queryFn: () => getSubmission(id!),
    enabled: id !== undefined,
  });

export interface ApproveInput {
  readonly id: string;
  readonly publish: boolean;
  /** 第一次进空间时选的「给谁用」（授权主体，如 role:editor、dept:x）；null 是不改（以后的版本保留原来的） */
  readonly grantTo: readonly string[] | null;
}

/** 通过后按选的设置「给谁用」（第一次进空间时服务端已默认全体成员，这里换成选的）。失败不回滚通过，提示到技能详情里改。 */
async function approveAndGrant({ id, publish, grantTo }: ApproveInput): Promise<Skill> {
  const skill = await approveSubmission(id, publish);
  if (grantTo === null) return skill;
  try {
    await setResourceUsers(`skill:${skill.id}`, grantTo);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 0;
    throw new ApiError(status, '已通过，但「给谁用」没有保存成功，请到技能详情里改');
  }
  return skill;
}

const reviewed = [SKILL_KEYS.pending, SKILL_KEYS.list, ['admin', 'grants']] as const;

export function useSubmissionActions() {
  return {
    approve: useAction(approveAndGrant, {
      invalidate: reviewed,
      success: '已通过，技能已进空间',
      failure: '没有通过成功',
    }),
    reject: useAction(({ id, reason }: { id: string; reason: string }) => rejectSubmission(id, reason), {
      invalidate: reviewed,
      success: '已退回，作者能看到原因',
      failure: '没有退回成功',
    }),
  };
}

/** 到达本空间的企业技能与内置技能。 */
export const useUpperSkills = () => useQuery({ queryKey: SKILL_KEYS.upper, queryFn: listUpperSkills });

export function useUpperSkillActions() {
  return {
    toggle: useAction(({ key, off }: { key: string; off: boolean }) => setUpperSkillOff(key, off), {
      invalidate: [SKILL_KEYS.upper, SKILL_KEYS.list],
      success: '已切换，新对话里生效',
      failure: '没有切换成功',
    }),
    users: useAction(
      ({ key, subjects }: { key: string; subjects: readonly string[] }) => setUpperSkillUsers(key, subjects),
      { invalidate: [SKILL_KEYS.upper, KEYS.grants], success: '已保存给谁用', failure: '没有保存成功' },
    ),
  };
}
