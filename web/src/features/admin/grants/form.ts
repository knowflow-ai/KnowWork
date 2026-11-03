/** 新增授权表单的选项与校验。 */
import { memberLabel, peopleOnly, ROLES, type Action, type SubjectKind } from '../../../api/workspace';
import { orgTree } from '../../../api/org';
import type { TreeSelectNode } from '../../../ui/TreeSelect';
import type { Directory } from './labels';
import { departmentNodes, orgNodes } from './users';

const ID_PATTERN = /^[A-Za-z0-9_.-]{1,64}$/;
/**
 * 可以写成「前缀*」的资源（与服务端 vocab.PREFIX_WILDCARD_TYPES 一致）：工具的 mcp__<服务名>__* 覆盖一个 MCP 服务，
 * 知识库的 <知识源id>.* 覆盖一个来源下的全部知识库。
 */
const PREFIX_TYPES = new Set(['tool', 'knowledge_base']);

type Option = { readonly value: string; readonly label: string };

const leaf = (key: string, label: string): TreeSelectNode => ({ key, label, children: [] });

/**
 * 一类授权对象的可选项，给树形多选用，键是完整的授权对象（`user:<id>`、`org:<id>`……）。
 * 部门、组织架构里的部门按层级成树（同名部门靠上级分清，选上级即含下级）；成员、用户组、角色是平铺的一层。
 */
export function subjectNodes(kind: SubjectKind, dir: Directory): readonly TreeSelectNode[] {
  switch (kind) {
    case 'user':
      return peopleOnly(dir.members).map((m) => leaf(`user:${m.userId}`, memberLabel(m)));
    case 'group':
      return dir.groups.map((g) => leaf(`group:${g.id}`, g.name));
    case 'dept':
      return departmentNodes(dir.departments);
    case 'org':
      return orgNodes(orgTree(dir.orgDepartments));
    default:
      return ROLES.map((r) => leaf(`role:${r.value}`, r.label));
  }
}

/** 搜索框里的提示：搜什么。 */
export function subjectSearchHint(kind: SubjectKind): string {
  return { user: '搜索成员', group: '搜索用户组', dept: '搜索部门', org: '搜索部门', role: '搜索角色' }[kind];
}

export function resourceOptions(type: string, dir: Directory): readonly Option[] {
  const prefix = `${type}:`;
  return Object.entries(dir.resources)
    .filter(([key]) => key.startsWith(prefix))
    .map(([key, name]) => ({ value: key.slice(prefix.length), label: name }));
}

export interface GrantInput {
  readonly subjectKind: SubjectKind;
  /** 选中的授权对象（同一类，可多个），完整写法 `类型:id` */
  readonly subjects: readonly string[];
  readonly resourceType: string;
  /** 选中的资源（同一类，可多个）：知识库、工具是完整资源（`类型:id`），其余类型是 id */
  readonly resourceIds: readonly string[];
  readonly all: boolean;
  readonly action: Action;
}

/** 按树选的资源类型：选中的值本身就是完整资源（`类型:id`）。 */
const TREE_TYPES: Readonly<Record<string, string>> = { knowledge_base: '知识库', tool: '工具' };

/**
 * 授权的资源（`类型:id`），可以几个。知识库、工具那一栏是树形选择，选中的值本身就是完整资源（知识库或按目录授权的目录、
 * 单个工具或一个 MCP 服务的全部工具），其余类型是「类型 + 选的或填的 id」。勾了「全部」就只有 `类型:*` 一条。
 */
export function resourceKeys(input: GrantInput): string[] {
  if (input.all) return [`${input.resourceType}:*`];
  const ids = input.resourceIds.map((id) => id.trim()).filter(Boolean);
  return input.resourceType in TREE_TYPES ? ids : ids.map((id) => `${input.resourceType}:${id}`);
}

function badId(key: string): boolean {
  const type = key.slice(0, key.indexOf(':'));
  const id = key.slice(key.indexOf(':') + 1);
  const prefix = PREFIX_TYPES.has(type) && id.endsWith('*') ? id.slice(0, -1) : id;
  return !ID_PATTERN.test(prefix);
}

export function validateGrant(input: GrantInput): string | null {
  if (input.subjects.length === 0) return '请选择授权对象';
  if (input.all) return null;
  const keys = resourceKeys(input);
  const tree = TREE_TYPES[input.resourceType];
  if (tree && keys.length === 0) return `请选择${tree}`;
  if (keys.length === 0 || keys.some(badId)) return '请填写资源 ID（字母、数字、下划线、点或短横线）';
  return null;
}
