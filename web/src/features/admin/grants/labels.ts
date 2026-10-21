/** 把授权里的 `类型:id` 翻成给人看的名称。 */
import {
  ACTIONS,
  flattenDepartments,
  memberLabel,
  RESOURCE_TYPES,
  roleLabel,
  splitKey,
  SUBJECT_KINDS,
  type Department,
  type Member,
  type UserGroup,
} from '../../../api/workspace';
import type { SourceBases } from '../../../api/knowledge';

/** 不再能授权、但旧数据里可能还有的资源类型，只用于显示。 */
const RETIRED_TYPES: Readonly<Record<string, string>> = { knowledge_source: '知识源' };

export interface Directory {
  readonly members: readonly Member[];
  readonly groups: readonly UserGroup[];
  readonly departments: readonly Department[];
  /** 资源名称：`类型:id` → 名称（知识库、模型分组这类有接口可查的）。 */
  readonly resources: Readonly<Record<string, string>>;
  /** 按知识源分组的知识库，给授权时的选择树。 */
  readonly knowledge: readonly SourceBases[];
}

export function subjectLabel(key: string, dir: Directory): { readonly kind: string; readonly name: string } {
  const { kind, id } = splitKey(key);
  const kindText = SUBJECT_KINDS.find((k) => k.value === kind)?.label ?? kind;
  if (kind === 'user') {
    const member = dir.members.find((m) => m.userId === id);
    return { kind: kindText, name: member ? memberLabel(member) : id };
  }
  if (kind === 'group') return { kind: kindText, name: dir.groups.find((g) => g.id === id)?.name ?? id };
  if (kind === 'dept') {
    return { kind: kindText, name: flattenDepartments(dir.departments).find((d) => d.id === id)?.name ?? id };
  }
  if (kind === 'role') return { kind: kindText, name: roleLabel(id) };
  return { kind: kindText, name: id };
}

export function resourceLabel(key: string, dir: Directory): { readonly type: string; readonly name: string } {
  const { kind, id } = splitKey(key);
  const type = RESOURCE_TYPES.find((t) => t.value === kind)?.label ?? RETIRED_TYPES[kind] ?? kind;
  if (id === '*') return { type, name: `全部${type}` };
  return { type, name: dir.resources[key] ?? id };
}

export function actionLabel(action: string): string {
  return ACTIONS.find((a) => a.value === action)?.label ?? action;
}
