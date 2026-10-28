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
  /** 知识库列表取到了：只有取到了，才能说某个知识库已经不在了（取不到时照原样显示编号）。 */
  readonly knowledgeLoaded: boolean;
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

export interface ResourceLabel {
  readonly type: string;
  readonly name: string;
  /** 资源已经不在了（知识库删了、共享撤销了）：授权还留着，但不再起作用 */
  readonly stale?: boolean;
}

export function resourceLabel(key: string, dir: Directory): ResourceLabel {
  const { kind, id } = splitKey(key);
  const type = dir.typeLabels[kind] ?? RETIRED_TYPES[kind] ?? kind;
  if (id === '*') return { type, name: `全部${type}` };
  const known = dir.resources[key];
  if (known) return { type, name: known };
  // 知识库列表是管理员能看到的全部（含别的空间共享进来的）。知识源取到了、里面却没有这个库，说明库删了或共享撤销了；
  // 原来直接显示 builtin.kb_xxx 这样的编号，看不懂是什么。知识源取不到时不下结论，照原样显示编号。
  if (kind === 'knowledge_base' && dir.knowledgeLoaded) {
    const source = dir.knowledge.find((s) => id.startsWith(`${s.sourceId}.`));
    if (source && !source.error) return { type, name: `${source.sourceName} · 已失效的知识库`, stale: true };
  }
  return { type, name: id };
}

export function actionLabel(action: string): string {
  return ACTIONS.find((a) => a.value === action)?.label ?? action;
}
