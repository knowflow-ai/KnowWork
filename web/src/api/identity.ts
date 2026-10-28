/** 个人 API Key、邀请、用户搜索（server/src/knowwork_server/identity/api.py）。 */
import { request, seg } from './http';
import { bool, isRecord, listOf, str, strings, text } from './parse';
import type { CurrentUser } from './auth';
import { parseUser } from './auth';
import { workspacePath } from './workspace';

/** API Key 可选的权限范围，与 server/src/knowwork_server/principals.py 一致。 */
export const API_KEY_SCOPES = [
  { value: 'conversations:use', label: '使用对话' },
  { value: 'workspace:manage', label: '管理成员与授权' },
  // 范围名沿用旧的：模型配置改成只有系统管理员在网页上能改，它现在只管用量统计的两个接口
  { value: 'models:manage', label: '查看用量' },
  { value: 'knowledge:manage', label: '管理知识源' },
  { value: 'knowledge:read', label: '检索知识（对外接口与 MCP）' },
  { value: 'tools:manage', label: '管理工具与 MCP' },
  { value: 'audit:read', label: '查看审计日志' },
] as const;

export interface ApiKey {
  readonly id: string;
  readonly name: string;
  readonly prefix: string;
  readonly scopes: readonly string[];
  readonly workspaceId: string | null;
  readonly createdAt: string;
  readonly expiresAt: string | null;
  readonly lastUsedAt: string | null;
  readonly revoked: boolean;
}

export interface IssuedApiKey extends ApiKey {
  /** 明文只在创建时返回这一次。 */
  readonly key: string;
}

export function parseApiKey(value: unknown): ApiKey | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    prefix: str(value.prefix) ?? '',
    scopes: strings(value.scopes),
    workspaceId: text(value.workspaceId) ?? null,
    createdAt: str(value.createdAt) ?? '',
    expiresAt: text(value.expiresAt) ?? null,
    lastUsedAt: text(value.lastUsedAt) ?? null,
    revoked: bool(value.revoked),
  };
}

export async function listApiKeys(): Promise<readonly ApiKey[]> {
  return listOf(await request<unknown>('/api/auth/api-keys'), parseApiKey);
}

export interface CreateApiKeyInput {
  readonly name: string;
  readonly scopes: readonly string[];
  readonly expiresAt?: string | null;
  /** 只在这个空间里有效（必填） */
  readonly workspaceId: string;
}

export function validateApiKeyInput(input: Pick<CreateApiKeyInput, 'name' | 'scopes'>): string | null {
  const name = input.name.trim();
  if (!name) return '请输入名称';
  if (name.length > 100) return '名称不能超过 100 个字';
  if (input.scopes.length === 0) return '至少选择一项权限范围';
  return null;
}

export async function createApiKey(input: CreateApiKeyInput): Promise<IssuedApiKey> {
  const body = await request<unknown>('/api/auth/api-keys', {
    method: 'POST',
    body: {
      name: input.name.trim(),
      scopes: input.scopes,
      expiresAt: input.expiresAt ?? null,
      workspaceId: input.workspaceId,
    },
  });
  const info = parseApiKey(body);
  const key = isRecord(body) ? text(body.key) : undefined;
  if (!info || !key) throw new Error('服务返回的数据不完整，请到列表里确认后重试');
  return { ...info, key };
}

export function revokeApiKey(keyId: string): Promise<void> {
  return request(`/api/auth/api-keys/${seg(keyId)}`, { method: 'DELETE' });
}

export interface Invitation {
  readonly id: string;
  readonly email: string;
  readonly url: string;
  readonly expiresAt: string;
}

export type InviteRole = 'editor' | 'admin';

/** 邀请进当前空间；角色只能是成员或管理员（所有者由现任所有者在成员页任命）。 */
export async function inviteUser(email: string, role: InviteRole = 'editor'): Promise<Invitation> {
  const body = await request<unknown>(workspacePath('/invitations'), {
    method: 'POST',
    body: { email: email.trim(), role },
  });
  if (!isRecord(body) || !text(body.url)) throw new Error('服务返回的数据不完整，请稍后重试');
  return {
    id: str(body.id) ?? '',
    email: str(body.email) ?? email.trim(),
    url: str(body.url) ?? '',
    expiresAt: str(body.expiresAt) ?? '',
  };
}

/** 按邮箱或姓名搜索账号（只有当前空间的管理员能用），给加成员时选人。 */
export async function searchUsers(query: string, limit = 20): Promise<readonly CurrentUser[]> {
  const q = query.trim().slice(0, 100);
  if (!q) return [];
  const body = await request<unknown>(workspacePath('/users'), { query: { q, limit } });
  return listOf(body, (item) => parseUser(item) ?? undefined);
}
