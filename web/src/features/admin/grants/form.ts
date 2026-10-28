/** 新增授权表单的选项与校验。 */
import {
  flattenDepartments,
  memberLabel,
  peopleOnly,
  ROLES,
  type Action,
  type SubjectKind,
} from '../../../api/workspace';
import type { Directory } from './labels';

const ID_PATTERN = /^[A-Za-z0-9_.-]{1,64}$/;
/**
 * 可以写成「前缀*」的资源（与服务端 vocab.PREFIX_WILDCARD_TYPES 一致）：工具的 mcp__<服务名>__* 覆盖一个 MCP 服务，
 * 知识库的 <知识源id>.* 覆盖一个来源下的全部知识库。
 */
const PREFIX_TYPES = new Set(['tool', 'knowledge_base']);

type Option = { readonly value: string; readonly label: string };

export function subjectOptions(kind: SubjectKind, dir: Directory): readonly Option[] {
  switch (kind) {
    case 'user':
      return peopleOnly(dir.members).map((m) => ({ value: m.userId, label: memberLabel(m) }));
    case 'group':
      return dir.groups.map((g) => ({ value: g.id, label: g.name }));
    case 'dept':
      return flattenDepartments(dir.departments).map((d) => ({ value: d.id, label: d.name }));
    default:
      return ROLES.map((r) => ({ value: r.value, label: r.label }));
  }
}

export function resourceOptions(type: string, dir: Directory): readonly Option[] {
  const prefix = `${type}:`;
  return Object.entries(dir.resources)
    .filter(([key]) => key.startsWith(prefix))
    .map(([key, name]) => ({ value: key.slice(prefix.length), label: name }));
}

export interface GrantInput {
  readonly subjectKind: SubjectKind;
  readonly subjectId?: string;
  readonly resourceType: string;
  readonly resourceId: string;
  readonly all: boolean;
  readonly action: Action;
}

/** 按树选的资源类型：选中的值本身就是完整资源（`类型:id`）。 */
const TREE_TYPES: Readonly<Record<string, string>> = { knowledge_base: '知识库', tool: '工具' };

/**
 * 授权的资源（`类型:id`）。知识库、工具那一栏是树形选择，选中的值本身就是完整资源（知识库或按目录授权的目录、
 * 单个工具或一个 MCP 服务的全部工具），其余类型是「类型 + 选的或填的 id」。
 */
export function resourceKey(input: GrantInput): string {
  if (input.all) return `${input.resourceType}:*`;
  const id = input.resourceId.trim();
  return input.resourceType in TREE_TYPES ? id : `${input.resourceType}:${id}`;
}

export function validateGrant(input: GrantInput): string | null {
  if (!input.subjectId) return '请选择授权对象';
  if (input.all) return null;
  const tree = TREE_TYPES[input.resourceType];
  if (tree && !input.resourceId.trim()) return `请选择${tree}`;
  const key = resourceKey(input);
  const type = key.slice(0, key.indexOf(':'));
  const id = key.slice(key.indexOf(':') + 1);
  const prefix = PREFIX_TYPES.has(type) && id.endsWith('*') ? id.slice(0, -1) : id;
  if (!ID_PATTERN.test(prefix)) return '请填写资源 ID（字母、数字、下划线、点或短横线）';
  return null;
}
