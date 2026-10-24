/** 把授权里的 `类型:id` 翻成给人看的名称。 */
import {
  ACTIONS,
  flattenDepartments,
  memberLabel,
  roleLabel,
  splitKey,
  SUBJECT_KINDS,
  type Department,
  type Member,
  type UserGroup,
} from '../../../api/workspace';
import type { SourceBases } from '../../../api/knowledge';
import type { McpServer } from '../../../api/mcp';
import type { GrantTreeNode } from '../../../slots/registry';

/** 不再能授权、但旧数据里可能还有的资源类型，只用于显示。 */
const RETIRED_TYPES: Readonly<Record<string, string>> = { knowledge_source: '知识源' };

export interface Directory {
  readonly members: readonly Member[];
  readonly groups: readonly UserGroup[];
  readonly departments: readonly Department[];
  /** 资源名称：`类型:id` → 名称（知识库、MCP 工具这类有接口可查的）。 */
  readonly resources: Readonly<Record<string, string>>;
  /** 按知识源分组的知识库，给授权时的选择树。 */
  readonly knowledge: readonly SourceBases[];
  /** MCP 服务与它们的工具，给授权时的选择树。 */
  readonly tools: readonly McpServer[];
  /** 资源类型的中文名：内置的加上扩展树里出现的（插槽 grants.knowledge）。 */
  readonly typeLabels: Readonly<Record<string, string>>;
  /** 扩展给的知识源层级树（知识源 id → 树），授权时按树选。 */
  readonly trees: Readonly<Record<string, readonly GrantTreeNode[]>>;
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
  const type = dir.typeLabels[kind] ?? RETIRED_TYPES[kind] ?? kind;
  if (id === '*') return { type, name: `全部${type}` };
  return { type, name: dir.resources[key] ?? id };
}

export function actionLabel(action: string): string {
  return ACTIONS.find((a) => a.value === action)?.label ?? action;
}
