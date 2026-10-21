/** 技能管理页的查询与变更。 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import {
  deleteSkill,
  getSkill,
  installSkill,
  listSkills,
  publishSkill,
  replaceDraft,
  setSkillEnabled,
  type Skill,
} from '../../../api/skills';
import { listSkillRuns, skillRunSummary, type SkillRunStatus } from '../../../api/skillRuns';
import { useAction } from '../useAction';

export const SKILL_KEYS = {
  list: ['admin', 'skills'],
  detail: (id: string) => ['admin', 'skills', id],
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
  };
}
