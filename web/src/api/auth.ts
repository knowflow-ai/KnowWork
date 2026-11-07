/**
 * 登录、登出、当前用户、登录方式列表、邀请注册与初始化向导。
 * 接口：server/src/knowwork_server/identity/api.py、identity/oidc_routes.py、setup/api.py。
 */
import { ApiError, request } from './http';
import { isRecord, strings, text } from './parse';

export interface CurrentUser {
  readonly id: string;
  /** 企业微信等目录来的人多半没有邮箱。 */
  readonly email: string | null;
  readonly name: string;
}

export interface Me extends CurrentUser {
  readonly authMethod: string;
  /** API Key 身份才有范围；浏览器会话为 null。 */
  readonly scopes: readonly string[] | null;
  /** 本部署启用了的扩展（按授权）：前端只显示这些扩展的入口。服务端没给（或登录后先写进缓存的那份）为 undefined */
  readonly extensions?: readonly string[];
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
  if (!id) return null;
  const email = text(value.email) ?? null;
  return { id, email, name: text(value.name) ?? email?.split('@')[0] ?? id };
}

function parseMe(value: unknown): Me | null {
  const user = parseUser(value);
  if (!user || !isRecord(value)) return null;
  return {
    ...user,
    authMethod: text(value.authMethod) ?? 'session',
    scopes: Array.isArray(value.scopes) ? strings(value.scopes) : null,
    ...(Array.isArray(value.extensions) ? { extensions: strings(value.extensions) } : {}),
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

export interface DirectoryLoginInput {
  /** 域账号：zhangsan、zhangsan@corp.com 或 CORP\\zhangsan。 */
  readonly username: string;
  readonly password: string;
}

/** 域账号登录（LDAP / AD 等账号密码类的登录方式）；url 是登录方式列表里给的本站地址。 */
export async function directoryLogin(url: string, input: DirectoryLoginInput): Promise<CurrentUser> {
  let body: unknown;
  try {
    body = await request<unknown>(url, {
      method: 'POST',
      body: { username: input.username.trim(), password: input.password },
      quietUnauthorized: true,
    });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 422)) {
      throw new ApiError(error.status, '账号或密码不正确');
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

/** 跳转到对方登录（单点登录、企业微信等），或在登录页输账号密码（域账号）。 */
export type LoginMethodStyle = 'redirect' | 'password';

export interface LoginMethod {
  readonly id: string;
  /** oidc、oauth2、ldap、wecom……按它选图标。 */
  readonly kind: string;
  /** 按钮上写「使用{label}登录」。 */
  readonly label: string;
  readonly style: LoginMethodStyle;
  /** 本站路径：跳转入口，或账号密码的提交地址。 */
  readonly url: string;
  /** 在这些客户端的内置浏览器里自动走这个方式（按浏览器标识里的片段认）。 */
  readonly inClientAgents: readonly string[];
}

export interface LoginMethods {
  /** 账号密码登录是否开着。 */
  readonly password: boolean;
  readonly methods: readonly LoginMethod[];
}

/** 只认本站路径：登录页照着跳，不能被带去别的站点（服务端也校验过，这里再挡一道）。 */
function sitePath(value: unknown): string | undefined {
  const url = text(value);
  return url && url.startsWith('/') && !url.startsWith('//') ? url : undefined;
}

function parseLoginMethod(value: unknown): LoginMethod | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const label = text(value.label);
  const url = sitePath(value.url);
  const style = value.style === 'redirect' || value.style === 'password' ? value.style : undefined;
  if (!id || !label || !url || !style) return undefined;
  return {
    id,
    kind: text(value.kind) ?? 'other',
    label,
    style,
    url,
    inClientAgents: Array.isArray(value.inClientAgents) ? strings(value.inClientAgents) : [],
  };
}

export function parseLoginMethods(value: unknown): LoginMethods {
  if (!isRecord(value)) return { password: true, methods: [] };
  const methods = Array.isArray(value.methods) ? value.methods : [];
  return {
    password: value.password !== false,
    methods: methods.flatMap((item) => parseLoginMethod(item) ?? []),
  };
}

/** 登录页显示哪些入口。取不到时只显示账号密码，登录页照常能用。 */
export async function fetchLoginMethods(): Promise<LoginMethods> {
  try {
    return parseLoginMethods(await request<unknown>('/api/auth/methods', { quietUnauthorized: true }));
  } catch {
    return { password: true, methods: [] };
  }
}
