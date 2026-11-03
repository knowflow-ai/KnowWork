import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  acceptInvitation,
  fetchMe,
  fetchSetupStatus,
  initializeSystem,
  isPlausibleInviteToken,
  login,
  logout,
  parseLoginMethods,
  parseUser,
  validateLogin,
  validateNewPassword,
} from './auth';
import { jsonResponse, routeFetch } from '../test/render';
import { useSessionStore } from '../stores/session';

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().reset();
});

describe('校验', () => {
  it.each([
    [{ email: '', password: 'x' }, '请输入邮箱'],
    [{ email: 'bad', password: 'x' }, '邮箱格式不正确'],
    [{ email: 'a@b.co', password: '' }, '请输入密码'],
    [{ email: 'a@b.co', password: 'x'.repeat(257) }, '密码过长'],
    [{ email: ' a@b.co ', password: 'x' }, null],
  ])('validateLogin %j → %s', (input, expected) => {
    expect(validateLogin(input)).toBe(expected);
  });

  it('新密码规则与两次一致', () => {
    expect(validateNewPassword('short', 'short')).toBe('密码至少 8 位');
    expect(validateNewPassword('x'.repeat(257), 'x'.repeat(257))).toBe('密码过长');
    expect(validateNewPassword('longenough', 'different')).toBe('两次输入的密码不一致');
    expect(validateNewPassword('longenough', 'longenough')).toBeNull();
  });

  it('邀请令牌只认合理长度与字符集', () => {
    expect(isPlausibleInviteToken(null)).toBe(false);
    expect(isPlausibleInviteToken('short')).toBe(false);
    expect(isPlausibleInviteToken('a'.repeat(16) + '<')).toBe(false);
    expect(isPlausibleInviteToken('abcdEFGH_1234-xyz')).toBe(true);
  });
});

describe('parseUser', () => {
  it('必需 id，姓名缺省取邮箱前缀', () => {
    expect(parseUser({ id: 'u1', email: 'li@corp.cn', name: '' })).toEqual({
      id: 'u1',
      email: 'li@corp.cn',
      name: 'li',
    });
    expect(parseUser({ email: 'a@b.c' })).toBeNull();
    expect(parseUser('x')).toBeNull();
  });

  it('目录来的人可以没有邮箱', () => {
    expect(parseUser({ id: 'u2', email: null, name: '张三' })).toEqual({
      id: 'u2',
      email: null,
      name: '张三',
    });
    expect(parseUser({ id: 'u3' })).toEqual({ id: 'u3', email: null, name: 'u3' });
  });
});

describe('parseLoginMethods', () => {
  const oidc = {
    id: 'oidc',
    kind: 'oidc',
    label: '企业账号',
    style: 'redirect',
    url: '/api/auth/oidc/login',
  };

  it('只收本站路径、认得的样式', () => {
    const parsed = parseLoginMethods({
      password: true,
      methods: [
        oidc,
        { ...oidc, id: 'evil', url: 'https://evil.example.com/login' },
        { ...oidc, id: 'evil2', url: '//evil.example.com/login' },
        { ...oidc, id: 'popup', style: 'popup' },
        { ...oidc, id: 'wx', kind: 'wecom', inClientAgents: ['wxwork'] },
      ],
    });
    expect(parsed.methods.map((m) => m.id)).toEqual(['oidc', 'wx']);
    expect(parsed.methods[0]?.inClientAgents).toEqual([]);
    expect(parsed.methods[1]?.inClientAgents).toEqual(['wxwork']);
  });

  it('账号密码默认开着；格式不对时只剩账号密码', () => {
    expect(parseLoginMethods({ methods: [] }).password).toBe(true);
    expect(parseLoginMethods({ password: false, methods: [] }).password).toBe(false);
    expect(parseLoginMethods('x')).toEqual({ password: true, methods: [] });
  });
});

describe('登录与当前用户', () => {
  it('登录成功返回用户；密码错误不标记会话失效', async () => {
    routeFetch({ 'POST /api/auth/login': jsonResponse({ id: 'u', email: 'a@b.cn', name: '李' }) });
    await expect(login({ email: ' a@b.cn ', password: 'p' })).resolves.toEqual({
      id: 'u',
      email: 'a@b.cn',
      name: '李',
    });
    routeFetch({ 'POST /api/auth/login': jsonResponse({ detail: '邮箱或密码错误' }, 401) });
    await expect(login({ email: 'a@b.cn', password: 'p' })).rejects.toMatchObject({
      message: '邮箱或密码不正确',
    });
    expect(useSessionStore.getState().expired).toBe(false);
  });

  it.each([
    [429, '尝试次数过多，请稍后再试'],
    [500, '服务暂时不可用，请稍后重试'],
  ])('状态 %i 的提示', async (status, message) => {
    routeFetch({ 'POST /api/auth/login': jsonResponse(status === 429 ? { detail: message } : {}, status) });
    await expect(login({ email: 'a@b.c', password: 'p' })).rejects.toMatchObject({ message });
  });

  it('登录返回不完整时报错', async () => {
    routeFetch({ 'POST /api/auth/login': jsonResponse({ email: 'x' }) });
    await expect(login({ email: 'a@b.c', password: 'p' })).rejects.toMatchObject({ status: 502 });
  });

  it('fetchMe：已登录返回用户，未登录返回 null，其他失败抛出', async () => {
    routeFetch({
      'GET /api/auth/me': jsonResponse({
        id: 'u',
        email: 'x@y.z',
        name: '张三',
        authMethod: 'api_key',
        scopes: ['a'],
      }),
    });
    await expect(fetchMe()).resolves.toEqual({
      id: 'u',
      email: 'x@y.z',
      name: '张三',
      authMethod: 'api_key',
      scopes: ['a'],
    });
    routeFetch({ 'GET /api/auth/me': jsonResponse({ detail: '未登录' }, 401) });
    await expect(fetchMe()).resolves.toBeNull();
    routeFetch({ 'GET /api/auth/me': jsonResponse({}, 503) });
    await expect(fetchMe()).rejects.toMatchObject({ status: 503 });
    // 没有 id 才算不完整；没有邮箱是目录来的人，正常
    routeFetch({ 'GET /api/auth/me': jsonResponse({ email: 'x@y.z' }) });
    await expect(fetchMe()).rejects.toMatchObject({ status: 502 });
    routeFetch({
      'GET /api/auth/me': jsonResponse({ id: 'u', email: null, name: '张三', authMethod: 'wecom' }),
    });
    await expect(fetchMe()).resolves.toMatchObject({ id: 'u', email: null, authMethod: 'wecom' });
  });

  it('登出、接受邀请、初始化按约定调用', async () => {
    const api = routeFetch({
      'POST /api/auth/logout': new Response(null, { status: 204 }),
      'POST /api/auth/invitations/accept': jsonResponse({ id: 'u2', email: 'c@d.cn', name: '韩梅梅' }, 201),
      'GET /api/setup': jsonResponse({ initialized: false }),
      'POST /api/setup': jsonResponse({ admin_user_id: 'u', workspace_id: 'default' }, 201),
    });
    await logout();
    await expect(
      acceptInvitation({ token: 't'.repeat(20), name: ' 韩梅梅 ', password: 'p'.repeat(8) }),
    ).resolves.toMatchObject({ id: 'u2' });
    await expect(fetchSetupStatus()).resolves.toEqual({ initialized: false });
    await initializeSystem({ email: ' a@b.cn ', name: ' 管理员 ', password: 'p'.repeat(8) });
    expect(api.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /api/auth/logout',
      'POST /api/auth/invitations/accept',
      'GET /api/setup',
      'POST /api/setup',
    ]);
    expect(api.calls[1]?.body).toEqual({ token: 't'.repeat(20), name: '韩梅梅', password: 'p'.repeat(8) });
    expect(api.calls[3]?.body).toEqual({ email: 'a@b.cn', name: '管理员', password: 'p'.repeat(8) });
  });

  it('初始化状态与邀请返回不完整时报错', async () => {
    routeFetch({
      'GET /api/setup': jsonResponse({}),
      'POST /api/auth/invitations/accept': jsonResponse({}, 201),
    });
    await expect(fetchSetupStatus()).rejects.toMatchObject({ status: 502 });
    await expect(
      acceptInvitation({ token: 't'.repeat(20), name: '', password: 'p'.repeat(8) }),
    ).rejects.toMatchObject({ status: 502 });
  });
});
