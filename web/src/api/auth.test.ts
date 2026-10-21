import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  acceptInvitation,
  fetchMe,
  fetchSetupStatus,
  initializeSystem,
  isPlausibleInviteToken,
  login,
  logout,
  parseUser,
  probeOidc,
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
  it('必需 id 与邮箱，姓名缺省取邮箱前缀', () => {
    expect(parseUser({ id: 'u1', email: 'li@corp.cn', name: '' })).toEqual({
      id: 'u1',
      email: 'li@corp.cn',
      name: 'li',
    });
    expect(parseUser({ email: 'a@b.c' })).toBeNull();
    expect(parseUser('x')).toBeNull();
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
    routeFetch({ 'GET /api/auth/me': jsonResponse({ id: 'u' }) });
    await expect(fetchMe()).rejects.toMatchObject({ status: 502 });
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

describe('probeOidc', () => {
  const as = (value: Partial<Response>) => vi.fn(async () => value as Response) as unknown as typeof fetch;

  it('要跳转身份提供方时可用，404 或出错时不可用', async () => {
    await expect(probeOidc(as({ type: 'opaqueredirect', status: 0 }))).resolves.toBe(true);
    await expect(probeOidc(as({ type: 'basic', status: 302 }))).resolves.toBe(true);
    await expect(probeOidc(as({ type: 'basic', status: 404 }))).resolves.toBe(false);
    await expect(probeOidc(as({ type: 'basic', status: 502 }))).resolves.toBe(false);
    await expect(
      probeOidc(vi.fn(async () => Promise.reject(new TypeError('x'))) as unknown as typeof fetch),
    ).resolves.toBe(false);
  });

  it('不跟随跳转', async () => {
    const fetchImpl = vi.fn(async () => ({ type: 'opaqueredirect', status: 0 }) as Response);
    await probeOidc(fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledWith('/api/auth/oidc/login', {
      redirect: 'manual',
      credentials: 'same-origin',
    });
  });
});
