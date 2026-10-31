/** 「技能」页（使用端）的查询与变更：我能用的、我的技能、提交到空间。 */
import { useQuery } from '@tanstack/react-query';
import {
  deleteSkill,
  getSkill,
  installSkill,
  listAvailableSkills,
  listSkills,
  replaceDraft,
  setSkillEnabled,
  setSkillEnv,
  submitSkill,
  withdrawSubmission,
  type Skill,
} from '../../api/skills';
import { useAction } from '../admin/useAction';

export const MY_SKILL_KEYS = {
  all: ['me', 'skills'],
  mine: ['me', 'skills', 'mine'],
  available: ['me', 'skills', 'available'],
  detail: (id: string) => ['me', 'skills', 'detail', id],
} as const;

export const useMySkills = () =>
  useQuery({ queryKey: MY_SKILL_KEYS.mine, queryFn: () => listSkills('personal') });

export const useAvailableSkills = () =>
  useQuery({ queryKey: MY_SKILL_KEYS.available, queryFn: listAvailableSkills });

export const useMySkillDetail = (id: string | undefined) =>
  useQuery({
    queryKey: MY_SKILL_KEYS.detail(id ?? ''),
    queryFn: () => getSkill(id!, 'personal'),
    enabled: id !== undefined,
  });

// 我的技能一变，「可以用的」也可能跟着变（启停、上传、删除）
const invalidate = [MY_SKILL_KEYS.all] as const;

export function useMySkillActions() {
  return {
    install: useAction((file: File) => installSkill(file, 'personal'), {
      invalidate,
      success: '已上传，新对话里就能用了',
      failure: '技能包没有上传成功',
    }),
    replace: useAction(({ skill, file }: { skill: Skill; file: File }) => replaceDraft(skill, file), {
      invalidate,
      success: '已换成新版本，新对话里生效',
      failure: '新版本没有上传成功',
    }),
    toggle: useAction(
      ({ skill, enabled }: { skill: Skill; enabled: boolean }) => setSkillEnabled(skill, enabled),
      { invalidate, failure: '没有切换成功' },
    ),
    remove: useAction((skill: Skill) => deleteSkill(skill), {
      invalidate,
      success: '已删除',
      failure: '没有删除成功',
    }),
    submit: useAction(({ skill, note }: { skill: Skill; note: string }) => submitSkill(skill, note), {
      invalidate,
      success: '已提交，空间管理员审核通过后大家就能用了',
      failure: '没有提交成功',
    }),
    withdraw: useAction((skill: Skill) => withdrawSubmission(skill), {
      invalidate,
      success: '已撤回提交',
      failure: '没有撤回成功',
    }),
    env: useAction(
      ({ skill, values }: { skill: Skill; values: Record<string, string | null> }) =>
        setSkillEnv(skill, values),
      { invalidate, success: '已保存，新对话里加载这个技能后生效', failure: '环境变量没有保存成功' },
    ),
  };
}
