/**
 * 登录、登出、当前用户、邀请注册、初始化向导与单点登录探测。
 * 接口：server/src/knowwork_server/identity/api.py、identity/oidc_routes.py、setup/api.py。
 */
import { ApiError, request } from './http';
import { isRecord, strings, text } from './parse';

export interface CurrentUser {
  readonly id: string;
  readonly email: string;
  readonly name: string;
}

export interface Me extends CurrentUser {
  readonly authMethod: string;
  /** API Key 身份才有范围；浏览器会话为 null。 */
  readonly scopes: readonly string[] | null;
}

export interface LoginInput {
  readonly email: string;
  readonly password: string;
}

export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_LENGTH = 256;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(value: string): string | null {
  const email = value.trim();
  if (!email) return '请输入邮箱';
  if (email.length > 320 || !EMAIL_PATTERN.test(email)) return '邮箱格式不正确';
  return null;
}

/** 前端先校验一遍，后端仍会再校验。返回错误提示，合法时返回 null。 */
export function validateLogin(input: LoginInput): string | null {
  const problem = validateEmail(input.email);
  if (problem) return problem;
  if (!input.password) return '请输入密码';
  if (input.password.length > MAX_PASSWORD_LENGTH) return '密码过长';
  return null;
}

/** 设置新密码时的规则，与后端 passwords.py 一致。 */
export function validateNewPassword(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return `密码至少 ${MIN_PASSWORD_LENGTH} 位`;
  if (password.length > MAX_PASSWORD_LENGTH) return '密码过长';
  if (password !== confirm) return '两次输入的密码不一致';
  return null;
}

export function parseUser(value: unknown): CurrentUser | null {
  if (!isRecord(value)) return null;
  const id = text(value.id);
  const email = text(value.email);
  if (!id || !email) return null;
  return { id, email, name: text(value.name) ?? email.split('@')[0] ?? email };
}

function parseMe(value: unknown): Me | null {
  const user = parseUser(value);
  if (!user || !isRecord(value)) return null;
  return {
    ...user,
    authMethod: text(value.authMethod) ?? 'session',
    scopes: Array.isArray(value.scopes) ? strings(value.scopes) : null,
  };
}

function incomplete(): ApiError {
  return new ApiError(502, '服务返回的数据不完整，请稍后重试');
}

export async function login(input: LoginInput): Promise<CurrentUser> {
  let body: unknown;
  try {
    body = await request<unknown>('/api/auth/login', {
      method: 'POST',
      body: { email: input.email.trim(), password: input.password },
      quietUnauthorized: true,
    });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 422)) {
      throw new ApiError(error.status, '邮箱或密码不正确');
    }
    throw error;
  }
  const user = parseUser(body);
  if (!user) throw incomplete();
  return user;
}

export async function logout(): Promise<void> {
  await request('/api/auth/logout', { method: 'POST', quietUnauthorized: true });
}

/** 当前用户；未登录返回 null，其它失败照常抛出。 */
export async function fetchMe(): Promise<Me | null> {
  try {
    const me = parseMe(await request<unknown>('/api/auth/me', { quietUnauthorized: true }));
    if (!me) throw incomplete();
    return me;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export interface AcceptInput {
  readonly token: string;
  readonly name: string;
  readonly password: string;
}

export async function acceptInvitation(input: AcceptInput): Promise<CurrentUser> {
  const body = await request<unknown>('/api/auth/invitations/accept', {
    method: 'POST',
    body: { token: input.token, name: input.name.trim(), password: input.password },
    quietUnauthorized: true,
  });
  const user = parseUser(body);
  if (!user) throw incomplete();
  return user;
}

/** 邀请链接里的令牌：长度与字符集和后端一致，不合法时直接提示，不去请求。 */
export function isPlausibleInviteToken(token: string | null): token is string {
  return !!token && token.length >= 16 && token.length <= 128 && /^[A-Za-z0-9_-]+$/.test(token);
}

export async function fetchSetupStatus(): Promise<{ readonly initialized: boolean }> {
  const body = await request<unknown>('/api/setup', { quietUnauthorized: true });
  if (!isRecord(body) || typeof body.initialized !== 'boolean') throw incomplete();
  return { initialized: body.initialized };
}

export interface SetupInput {
  readonly email: string;
  readonly name: string;
  readonly password: string;
}

export async function initializeSystem(input: SetupInput): Promise<void> {
  await request('/api/setup', {
    method: 'POST',
    body: { email: input.email.trim(), name: input.name.trim(), password: input.password },
    quietUnauthorized: true,
  });
}

export const OIDC_LOGIN_URL = '/api/auth/oidc/login';

/**
 * 单点登录是否可用：后端没配 OIDC 时这个地址返回 404；配了时返回 302 跳去身份提供方。
 * 用 redirect: 'manual' 只看是否要跳转，不真的跟过去。其它失败（提供方不可达等）按不可用处理。
 */
export async function probeOidc(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetchImpl(OIDC_LOGIN_URL, { redirect: 'manual', credentials: 'same-origin' });
    return response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400);
  } catch {
    return false;
  }
}
