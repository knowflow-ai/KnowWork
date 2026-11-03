/**
 * 系统设置「登录方式」：后台配置的 OIDC 与通用 OAuth2（server/src/knowwork_server/identity/providers_api.py）。
 * 只给系统管理员；字段名用下划线，同系统设置的其他接口。密钥只能写不能读，接口只给末四位。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, str, strings, text } from './parse';

const BASE = '/api/system/login-providers';

export type LoginProviderKind = 'oidc' | 'oauth2';
/** known_only：只允许已有账号与被邀请的人；auto：首次登录自动建号。 */
export type Signup = 'known_only' | 'auto';

export const KIND_LABEL: Record<LoginProviderKind, string> = { oidc: 'OIDC', oauth2: 'OAuth2' };
export const SIGNUP_LABEL: Record<Signup, string> = {
  known_only: '只允许已有账号',
  auto: '首次登录自动建号',
};

export interface LoginProvider {
  readonly id: string;
  readonly kind: LoginProviderKind;
  readonly name: string;
  readonly enabled: boolean;
  readonly signup: Signup;
  /** 各类型自己的设置，原样是接口里的下划线字段（不含密钥）。 */
  readonly config: Readonly<Record<string, unknown>>;
  readonly hasSecret: boolean;
  readonly secretLast4: string;
  /** 要登记到对方的回调地址。 */
  readonly callbackUrl: string;
  /** 环境变量配的 OIDC：只能看，要改请改环境变量。 */
  readonly readonly: boolean;
}

function parseProvider(value: unknown): LoginProvider | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.name);
  const kind = value.kind === 'oidc' || value.kind === 'oauth2' ? value.kind : undefined;
  if (!id || !name || !kind) return undefined;
  return {
    id,
    kind,
    name,
    enabled: bool(value.enabled, true),
    signup: value.signup === 'auto' ? 'auto' : 'known_only',
    config: isRecord(value.config) ? value.config : {},
    hasSecret: bool(value.has_secret),
    secretLast4: str(value.secret_last4) ?? '',
    callbackUrl: str(value.callback_url) ?? '',
    readonly: bool(value.readonly),
  };
}

export async function listLoginProviders(): Promise<readonly LoginProvider[]> {
  const body = await request<unknown>(BASE);
  return listOf(isRecord(body) ? body.providers : [], parseProvider);
}

export interface LoginProviderInput {
  readonly name: string;
  readonly enabled: boolean;
  readonly signup: Signup;
  readonly config: Readonly<Record<string, unknown>>;
  /** 新建时必填；修改时不传表示保持原密钥。 */
  readonly clientSecret?: string;
}

function body(input: LoginProviderInput): Record<string, unknown> {
  return {
    name: input.name.trim(),
    enabled: input.enabled,
    signup: input.signup,
    config: input.config,
    ...(input.clientSecret ? { client_secret: input.clientSecret } : {}),
  };
}

function parsed(value: unknown): LoginProvider {
  const provider = parseProvider(value);
  if (!provider) throw new Error('登录方式的返回不完整');
  return provider;
}

export async function createLoginProvider(
  kind: LoginProviderKind,
  input: LoginProviderInput,
): Promise<LoginProvider> {
  return parsed(await request<unknown>(BASE, { method: 'POST', body: { kind, ...body(input) } }));
}

export async function updateLoginProvider(id: string, input: LoginProviderInput): Promise<LoginProvider> {
  return parsed(await request<unknown>(`${BASE}/${seg(id)}`, { method: 'PATCH', body: body(input) }));
}

export function deleteLoginProvider(id: string): Promise<void> {
  return request(`${BASE}/${seg(id)}`, { method: 'DELETE' });
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/** 同服务端：https，或本机地址的 http；不带账号密码与 #。返回错误提示，合法时返回 null。 */
export function validateProviderUrl(value: string): string | null {
  const raw = value.trim();
  if (!raw) return '请输入地址';
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return '地址格式不正确';
  }
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCAL_HOSTS.has(url.hostname))) {
    return '地址必须是 https（本机地址除外）';
  }
  if (url.username || url.password || url.hash) return '地址里不能带账号密码或 #';
  return null;
}

const PATH = /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/;

/** 用户信息里取值的点分路径：data.user.id，数字段取数组下标。 */
export function validateFieldPath(value: string): string | null {
  if (!value.trim()) return '请输入字段路径';
  return PATH.test(value.trim()) ? null : '写成 data.user.id 这样，用点分开';
}

export function configStrings(config: Readonly<Record<string, unknown>>, key: string): readonly string[] {
  const value = config[key];
  return Array.isArray(value) ? strings(value) : [];
}
