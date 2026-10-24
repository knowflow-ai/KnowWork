/**
 * @ 菜单的候选，全是纯函数：从接口给的知识库、技能与搜到的文件，排成各组的候选；按输入的字筛；
 * 找出输入框里光标前正在打的 @。
 */
import type {
  Mention,
  MentionBase,
  MentionDocument,
  MentionOptions,
  MentionSkill,
} from '../../../api/mentions';
import type { MentionGroup } from './kinds';

/** 一个候选：选中后成为的 @，和名字下面那行小字（在哪里、是什么）。 */
export interface Candidate {
  readonly mention: Mention;
  readonly detail: string;
}

export interface Section {
  readonly group: MentionGroup;
  readonly items: readonly Candidate[];
  /** 筛完一共几项（直接输入时每组只露前几项） */
  readonly total: number;
}

/** 直接输入查找时每组露几项，其余点「查看全部」进组里看。 */
export const FLAT_LIMIT = 5;

/** 目录：知识库所在目录的每一级都能选，选了等于选下面全部的知识库。 */
function folderCandidates(bases: readonly MentionBase[]): Candidate[] {
  const folders = new Map<string, string[]>();
  for (const base of bases) {
    for (let depth = 1; depth <= base.path.length; depth += 1) {
      const path = base.path.slice(0, depth).join('/');
      folders.set(path, [...(folders.get(path) ?? []), base.id]);
    }
  }
  return [...folders.entries()]
    .sort(([a], [b]) => a.localeCompare(b, 'zh'))
    .map(([path, ids]) => ({
      mention: { type: 'folder', id: path, name: path, bases: ids },
      detail: `目录 · ${ids.length} 个知识库`,
    }));
}

function baseCandidates(bases: readonly MentionBase[]): Candidate[] {
  return [
    ...folderCandidates(bases),
    ...bases.map((base) => ({
      mention: { type: 'knowledge_base' as const, id: base.id, name: base.name },
      detail: [base.path.join('/'), base.description].filter(Boolean).join(' · '),
    })),
  ];
}

function tagCandidates(bases: readonly MentionBase[]): Candidate[] {
  return bases
    .filter((base) => base.pinnable)
    .flatMap((base) =>
      base.tags.map((tag) => ({
        mention: { type: 'tag' as const, id: tag, name: tag, base: base.id, baseName: base.name },
        detail: `在知识库「${base.name}」`,
      })),
    );
}

function documentCandidates(documents: readonly MentionDocument[]): Candidate[] {
  return documents.map((doc) => ({
    mention: { type: 'document', id: doc.id, name: doc.title, base: doc.base, baseName: doc.baseName },
    detail: `在知识库「${doc.baseName}」`,
  }));
}

function skillCandidates(skills: readonly MentionSkill[]): Candidate[] {
  return skills.map((skill) => ({
    mention: { type: 'skill', id: skill.name, name: skill.name },
    detail: skill.description,
  }));
}

export function groupCandidates(
  group: MentionGroup,
  options: MentionOptions,
  documents: readonly MentionDocument[],
): readonly Candidate[] {
  switch (group) {
    case 'bases':
      return baseCandidates(options.bases);
    case 'documents':
      return documentCandidates(documents);
    case 'tags':
      return tagCandidates(options.bases);
    case 'skills':
      return skillCandidates(options.skills);
  }
}

/** 名字或那行小字里有这几个字（不分大小写）。文件已经按名字在服务端搜过，不再筛。 */
export function matching(items: readonly Candidate[], query: string): readonly Candidate[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return items;
  return items.filter(
    (item) => item.mention.name.toLowerCase().includes(needle) || item.detail.toLowerCase().includes(needle),
  );
}

/** 菜单要显示的分组：进了某一组就是这一组的全部；直接输入时各组前几项。 */
export function sections(
  options: MentionOptions,
  documents: readonly MentionDocument[],
  query: string,
  group: MentionGroup | null,
): readonly Section[] {
  const groups: readonly MentionGroup[] = group ? [group] : ['bases', 'documents', 'tags', 'skills'];
  return groups.flatMap((name) => {
    const all = groupCandidates(name, options, documents);
    const found = name === 'documents' ? all : matching(all, query);
    if (found.length === 0) return [];
    return [{ group: name, items: group ? found : found.slice(0, FLAT_LIMIT), total: found.length }];
  });
}

/** 光标前正在打的 @：在开头或空白后面，到光标之间没有空白（邮箱里的 @ 不算，照官方 @file 的规则）。 */
export interface Trigger {
  readonly start: number;
  readonly query: string;
}

const MAX_QUERY = 50;

export function activeTrigger(value: string, caret: number): Trigger | null {
  const before = value.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at < 0 || (at > 0 && !/\s/.test(before[at - 1] ?? ''))) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query) || query.length > MAX_QUERY) return null;
  return { start: at, query };
}

/** 选中以后，把打出来的「@查找词」从输入框里去掉。 */
export function withoutTrigger(value: string, trigger: Trigger, caret: number): string {
  return value.slice(0, trigger.start) + value.slice(caret);
}

/** 重新生成时，事件流里的目录 @ 只有路径：按现在的知识库目录重新展开成下面的知识库。 */
export function expandFolder(path: string, bases: readonly MentionBase[]): readonly string[] {
  const parts = path.split('/');
  return bases
    .filter((base) => parts.every((part, index) => base.path[index] === part))
    .map((base) => base.id);
}
