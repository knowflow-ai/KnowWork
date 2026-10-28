import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createApiKey,
  inviteUser,
  listApiKeys,
  revokeApiKey,
  searchUsers,
  validateApiKeyInput,
} from './identity';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

const KEY = {
  id: 'k1',
  name: '脚本',
  prefix: 'ak_abcd1234',
  scopes: ['conversations:use'],
  workspaceId: 'default',
  createdAt: '2026-10-19T00:00:00+00:00',
  expiresAt: null,
  lastUsedAt: null,
  revoked: false,
};

describe('API Key', () => {
  it('校验输入', () => {
    expect(validateApiKeyInput({ name: ' ', scopes: ['a'] })).toBe('请输入名称');
    expect(validateApiKeyInput({ name: 'x'.repeat(101), scopes: ['a'] })).toBe('名称不能超过 100 个字');
    expect(validateApiKeyInput({ name: 'k', scopes: [] })).toBe('至少选择一项权限范围');
    expect(validateApiKeyInput({ name: 'k', scopes: ['a'] })).toBeNull();
  });

  it('列表、创建（明文只回一次）、吊销', async () => {
    const api = routeFetch({
      'GET /api/auth/api-keys': jsonResponse([KEY, { id: 'bad' }]),
      'POST /api/auth/api-keys': jsonResponse({ ...KEY, key: 'ak_abcd1234_secret' }, 201),
      'DELETE /api/auth/api-keys/*': new Response(null, { status: 204 }),
    });
    expect(await listApiKeys()).toEqual([KEY]);
    const issued = await createApiKey({
      name: ' 脚本 ',
      scopes: ['conversations:use'],
      workspaceId: 'default',
    });
    expect(issued.key).toBe('ak_abcd1234_secret');
    expect(api.calls[1]?.body).toEqual({
      name: '脚本',
      scopes: ['conversations:use'],
      expiresAt: null,
      workspaceId: 'default',
    });
    await revokeApiKey('k/1');
    expect(api.calls[2]?.path).toBe('/api/auth/api-keys/k%2F1');
  });

  it('创建返回缺明文时报错', async () => {
    routeFetch({ 'POST /api/auth/api-keys': jsonResponse(KEY, 201) });
    await expect(createApiKey({ name: 'k', scopes: ['a'], workspaceId: 'default' })).rejects.toThrow(
      '服务返回的数据不完整',
    );
  });
});

describe('邀请与用户搜索', () => {
  it('邀请返回链接', async () => {
    routeFetch({
      'POST /api/workspaces/default/invitations': jsonResponse(
        { id: 'i', email: 'a@b.cn', url: 'https://kw/invite?token=x', expiresAt: 'e' },
        201,
      ),
    });
    await expect(inviteUser(' a@b.cn ')).resolves.toMatchObject({ url: 'https://kw/invite?token=x' });
    routeFetch({ 'POST /api/workspaces/default/invitations': jsonResponse({}, 201) });
    await expect(inviteUser('a@b.cn')).rejects.toThrow();
  });

  it('搜索：空关键词不请求，结果逐项解析', async () => {
    const api = routeFetch({
      'GET /api/workspaces/default/users': jsonResponse([{ id: 'u', email: 'a@b.cn', name: '甲' }, {}]),
    });
    await expect(searchUsers('  ')).resolves.toEqual([]);
    expect(api.calls).toHaveLength(0);
    await expect(searchUsers(' 甲 ')).resolves.toEqual([{ id: 'u', email: 'a@b.cn', name: '甲' }]);
    expect(api.calls[0]?.query.get('q')).toBe('甲');
  });
});
