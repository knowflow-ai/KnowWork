/** 新增授权表单的选项与校验。 */
import {
  flattenDepartments,
  memberLabel,
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
      return dir.members.map((m) => ({ value: m.userId, label: memberLabel(m) }));
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

export function validateGrant(input: GrantInput): string | null {
  if (!input.subjectId) return '请选择授权对象';
  const id = input.resourceId.trim();
  if (input.resourceType === 'knowledge_base' && !input.all && !id) return '请选择知识库';
  const prefix = PREFIX_TYPES.has(input.resourceType) && id.endsWith('*') ? id.slice(0, -1) : id;
  if (!input.all && !ID_PATTERN.test(prefix)) return '请填写资源 ID（字母、数字、下划线、点或短横线）';
  return null;
}
