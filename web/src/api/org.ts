/**
 * 组织架构与空间成员规则（server/src/knowwork_server/access/api.py 的 org-departments、member-rules）。
 * 组织架构是目录来源（AD、企业微信等）同步来的部门树，全系统一份、只读；没接目录时为空，界面上不出现。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, text } from './parse';
import { workspacePath } from './workspace';

const base = (path: string) => workspacePath(path);

export interface OrgDepartment {
  readonly id: string;
  readonly sourceId: string;
  readonly parentId: string | null;
  readonly name: string;
  readonly sort: number;
  readonly memberCount: number;
}

function parseOrgDepartment(value: unknown): OrgDepartment | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    sourceId: text(value.source_id) ?? '',
    parentId: text(value.parent_id) ?? null,
    name,
    sort: num(value.sort) ?? 0,
    memberCount: num(value.member_count) ?? 0,
  };
}

export async function fetchOrgDepartments(): Promise<readonly OrgDepartment[]> {
  return listOf(await request<unknown>(base('/org-departments')), parseOrgDepartment);
}

/** 组织架构里一个部门的人（只这一级）。role 是在本空间的角色，不在本空间为 null。 */
export interface OrgMember {
  readonly userId: string;
  readonly name: string;
  readonly isPrimary: boolean;
  readonly role: string | null;
}

function parseOrgMember(value: unknown): OrgMember | undefined {
  if (!isRecord(value)) return undefined;
  const userId = text(value.user_id);
  if (!userId) return undefined;
  return {
    userId,
    name: text(value.name) ?? userId,
    isPrimary: bool(value.is_primary),
    role: text(value.role) ?? null,
  };
}

/** 只给本空间管理员；只有姓名，没有邮箱。 */
export async function fetchOrgDepartmentMembers(id: string): Promise<readonly OrgMember[]> {
  return listOf(await request<unknown>(base(`/org-departments/${seg(id)}/members`)), parseOrgMember);
}

export interface OrgNode {
  readonly department: OrgDepartment;
  readonly children: readonly OrgNode[];
}

/** 平铺的部门按上级成树；上级不在列表里（已移除）的当顶层。同级按 sort、名称排。 */
export function orgTree(departments: readonly OrgDepartment[]): readonly OrgNode[] {
  const ids = new Set(departments.map((d) => d.id));
  const byParent = new Map<string | null, OrgDepartment[]>();
  for (const dept of departments) {
    const parent = dept.parentId && ids.has(dept.parentId) ? dept.parentId : null;
    byParent.set(parent, [...(byParent.get(parent) ?? []), dept]);
  }
  const build = (parent: string | null): readonly OrgNode[] =>
    [...(byParent.get(parent) ?? [])]
      .sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name, 'zh-CN'))
      .map((department) => ({ department, children: build(department.id) }));
  return build(null);
}

/** 部门的完整路径「集团 / 总部 / 法务部」：同名部门（两个「财务部」）靠它分清。 */
export function orgPath(id: string, departments: readonly OrgDepartment[]): string {
  const byId = new Map(departments.map((d) => [d.id, d]));
  const names: string[] = [];
  let current = byId.get(id);
  while (current && names.length < 40) {
    names.unshift(current.name);
    current = current.parentId ? byId.get(current.parentId) : undefined;
  }
  return names.join(' / ');
}

export type RuleRole = 'editor' | 'admin';

export interface MemberRule {
  readonly id: string;
  readonly departmentId: string;
  readonly departmentName: string;
  /** 部门已从目录移除：规则留着，不再加人。 */
  readonly departmentActive: boolean;
  readonly includeChildren: boolean;
  readonly role: RuleRole;
}

function parseRule(value: unknown): MemberRule | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const departmentId = text(value.department_id);
  if (!id || !departmentId) return undefined;
  return {
    id,
    departmentId,
    departmentName: text(value.department_name) ?? departmentId,
    departmentActive: bool(value.department_active, true),
    includeChildren: bool(value.include_children, true),
    role: value.role === 'admin' ? 'admin' : 'editor',
  };
}

export async function listMemberRules(): Promise<readonly MemberRule[]> {
  return listOf(await request<unknown>(base('/member-rules')), parseRule);
}

export interface MemberRuleInput {
  readonly departmentId: string;
  readonly includeChildren: boolean;
  readonly role: RuleRole;
}

export function createMemberRule(input: MemberRuleInput): Promise<unknown> {
  return request(base('/member-rules'), {
    method: 'POST',
    body: { department_id: input.departmentId, include_children: input.includeChildren, role: input.role },
  });
}

export function deleteMemberRule(id: string): Promise<void> {
  return request(base(`/member-rules/${seg(id)}`), { method: 'DELETE' });
}
