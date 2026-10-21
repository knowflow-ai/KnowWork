/** 输入框技能入口的选项：已发布且启用的技能（个人与空间的合在一起）。选中后在消息前插入「/技能名」，宿主据此加载。 */
import { useQuery } from '@tanstack/react-query';
import { listUsableSkills } from '../../../api/skills';
import type { SkillOption } from '../components/Composer';

export function useSkillOptions(): readonly SkillOption[] {
  const query = useQuery({ queryKey: ['skills', 'usable'], queryFn: listUsableSkills, staleTime: 60_000 });
  return (query.data ?? []).map((skill) => ({
    key: skill.id,
    name: skill.name,
    description: skill.description,
  }));
}
