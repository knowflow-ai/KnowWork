/**
 * 工作空间成员、部门、用户组、资源授权与权限判定（server/src/knowwork_server/access/api.py）。
 * 开源版只有默认空间。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, strings, text } from './parse';

export const WORKSPACE_ID = 'default';

const base = (path = '') => `/api/workspaces/${seg(WORKSPACE_ID)}${path}`;

export type Role = 'owner' | 'admin' | 'editor' | 'viewer';
export const ROLES: readonly { readonly value: Role; readonly label: string }[] = [
  { value: 'owner', label: '所有者' },
  { value: 'admin', label: '管理员' },
  { value: 'editor', label: '成员' },
  { value: 'viewer', label: '只读成员' },
];

export function roleLabel(role: string): string {
  return ROLES.find((r) => r.value === role)?.label ?? role;
}

function isRole(value: unknown): value is Role {
  return ROLES.some((r) => r.value === value);
}

export interface Member {
  readonly userId: string;
  readonly role: Role;
  readonly name: string | null;
  readonly email: string | null;
}

function parseMember(value: unknown): Member | undefined {
  if (!isRecord(value)) return undefined;
  const userId = text(value.user_id);
  if (!userId || !isRole(value.role)) return undefined;
  return { userId, role: value.role, name: text(value.name) ?? null, email: text(value.email) ?? null };
}

/** 成员显示名：有姓名用姓名，否则邮箱，都没有用 id。 */
export function memberLabel(member: Pick<Member, 'userId' | 'name' | 'email'>): string {
  return member.name ?? member.email ?? member.userId;
}

export async function listMembers(): Promise<readonly Member[]> {
  return listOf(await request<unknown>(base('/members')), parseMember);
}

export function addMember(userId: string, role: Role): Promise<unknown> {
  return request(base('/members'), { method: 'POST', body: { user_id: userId, role } });
}

export function setMemberRole(userId: string, role: Role): Promise<unknown> {
  return request(base(`/members/${seg(userId)}`), { method: 'PATCH', body: { role } });
}

export function removeMember(userId: string): Promise<void> {
  return request(base(`/members/${seg(userId)}`), { method: 'DELETE' });
}

// ---- 部门 ----

export interface DepartmentMember {
  readonly userId: string;
  readonly isPrimary: boolean;
}

export interface Department {
  readonly id: string;
  readonly name: string;
  readonly parentId: string | null;
  readonly members: readonly DepartmentMember[];
  readonly children: readonly Department[];
}

function parseDepartmentMember(value: unknown): DepartmentMember | undefined {
  // 后端把 (user_id, is_primary) 元组序列化成二元数组
  if (!Array.isArray(value) || typeof value[0] !== 'string') return undefined;
  return { userId: value[0], isPrimary: value[1] === true };
}

export function parseDepartment(value: unknown): Department | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    parentId: text(value.parent_id) ?? null,
    members: listOf(value.members, parseDepartmentMember),
    children: listOf(value.children, parseDepartment),
  };
}

export async function fetchDepartments(): Promise<readonly Department[]> {
  return listOf(await request<unknown>(base('/departments')), parseDepartment);
}

/** 树里按 id 找部门；找不到返回 undefined。 */
export function findDepartment(tree: readonly Department[], id: string): Department | undefined {
  for (const node of tree) {
    if (node.id === id) return node;
    const found = findDepartment(node.children, id);
    if (found) return found;
  }
  return undefined;
}

/** 拍平成列表（深度优先），选择器与名称查找用。 */
export function flattenDepartments(tree: readonly Department[]): readonly Department[] {
  return tree.flatMap((node) => [node, ...flattenDepartments(node.children)]);
}

export function createDepartment(name: string, parentId: string | null): Promise<{ id: string }> {
  return request(base('/departments'), { method: 'POST', body: { name: name.trim(), parent_id: parentId } });
}

export function renameDepartment(id: string, name: string): Promise<void> {
  return request(base(`/departments/${seg(id)}`), { method: 'PATCH', body: { name: name.trim() } });
}

export function moveDepartment(id: string, parentId: string | null): Promise<void> {
  return request(base(`/departments/${seg(id)}`), {
    method: 'PATCH',
    body: { move: true, parent_id: parentId },
  });
}

export function deleteDepartment(id: string): Promise<void> {
  return request(base(`/departments/${seg(id)}`), { method: 'DELETE' });
}

export function addDepartmentMember(id: string, userId: string, isPrimary: boolean): Promise<void> {
  return request(base(`/departments/${seg(id)}/members`), {
    method: 'POST',
    body: { user_id: userId, is_primary: isPrimary },
  });
}

export function removeDepartmentMember(id: string, userId: string): Promise<void> {
  return request(base(`/departments/${seg(id)}/members/${seg(userId)}`), { method: 'DELETE' });
}

// ---- 用户组 ----

export interface UserGroup {
  readonly id: string;
  readonly name: string;
  readonly members: readonly string[];
}

function parseGroup(value: unknown): UserGroup | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return { id, name, members: strings(value.members) };
}

export async function listGroups(): Promise<readonly UserGroup[]> {
  return listOf(await request<unknown>(base('/groups')), parseGroup);
}

export function createGroup(name: string): Promise<{ id: string }> {
  return request(base('/groups'), { method: 'POST', body: { name: name.trim() } });
}

export function renameGroup(id: string, name: string): Promise<void> {
  return request(base(`/groups/${seg(id)}`), { method: 'PATCH', body: { name: name.trim() } });
}

export function deleteGroup(id: string): Promise<void> {
  return request(base(`/groups/${seg(id)}`), { method: 'DELETE' });
}

export function addGroupMember(id: string, userId: string): Promise<void> {
  return request(base(`/groups/${seg(id)}/members`), { method: 'POST', body: { user_id: userId } });
}

export function removeGroupMember(id: string, userId: string): Promise<void> {
  return request(base(`/groups/${seg(id)}/members/${seg(userId)}`), { method: 'DELETE' });
}

// ---- 资源授权 ----

export type Action = 'use' | 'edit' | 'owner';
export const ACTIONS: readonly { readonly value: Action; readonly label: string }[] = [
  { value: 'use', label: '使用' },
  { value: 'edit', label: '编辑' },
  { value: 'owner', label: '完全管理' },
];

/** 能授权的资源类型。知识源（连接）不单独授权，谁能查授在它下面的知识库上（技术方案 5.2 节）。 */
export const RESOURCE_TYPES = [
  { value: 'skill', label: '技能' },
  { value: 'tool', label: '工具' },
  { value: 'knowledge_base', label: '知识库' },
  { value: 'model_group', label: '模型分组' },
] as const;

export type SubjectKind = 'user' | 'group' | 'dept' | 'role';
export const SUBJECT_KINDS: readonly { readonly value: SubjectKind; readonly label: string }[] = [
  { value: 'user', label: '成员' },
  { value: 'group', label: '用户组' },
  { value: 'dept', label: '部门' },
  { value: 'role', label: '角色' },
];

export interface Grant {
  readonly subject: string;
  readonly resource: string;
  readonly action: string;
}

function parseGrant(value: unknown): Grant | undefined {
  if (!isRecord(value)) return undefined;
  const subject = text(value.subject);
  const resource = text(value.resource);
  const action = text(value.action);
  return subject && resource && action ? { subject, resource, action } : undefined;
}

export async function listGrants(): Promise<readonly Grant[]> {
  return listOf(await request<unknown>(base('/grants')), parseGrant);
}

export function grant(subject: string, resource: string, action: Action): Promise<unknown> {
  return request(base('/grants'), { method: 'POST', body: { subject, resource, action } });
}

export function revokeGrant(subject: string, resource: string): Promise<void> {
  return request(base('/grants'), { method: 'DELETE', query: { subject, resource } });
}

export function splitKey(key: string): { readonly kind: string; readonly id: string } {
  const index = key.indexOf(':');
  return index < 0 ? { kind: key, id: '' } : { kind: key.slice(0, index), id: key.slice(index + 1) };
}

// ---- 判定 ----

/** 调用者是否是空间管理员（owner 或 admin，即对工作空间有 edit）。判定出错一律为否。 */
export async function checkWorkspaceAdmin(): Promise<boolean> {
  try {
    const body = await request<unknown>(base('/me/check'), {
      query: { resource: 'workspace', action: 'edit' },
    });
    return isRecord(body) && bool(body.allowed);
  } catch {
    return false;
  }
}
