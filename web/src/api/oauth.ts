/** 应用登录（桌面端等经系统浏览器登录，OAuth 授权码 + PKCE）：确认页与「已登录的设备」。 */
import { request } from './http';
import { isRecord, listOf, str, text } from './parse';

/** 授权请求带的参数，确认页原样转给后端核对、批准。 */
const REQUEST_FIELDS = [
  'response_type',
  'client_id',
  'redirect_uri',
  'code_challenge',
  'code_challenge_method',
  'state',
  'device_name',
  'device_id',
] as const;

export type AuthorizationCheck =
  | { readonly kind: 'ok'; readonly clientName: string; readonly deviceName: string }
  | { readonly kind: 'error'; readonly message: string; readonly redirectTo: string | null };

function requestFields(search: string): Record<string, string> {
  const params = new URLSearchParams(search);
  return Object.fromEntries(REQUEST_FIELDS.map((name) => [name, params.get(name) ?? '']));
}

export function parseAuthorizationCheck(value: unknown): AuthorizationCheck {
  if (isRecord(value) && isRecord(value.error)) {
    return {
      kind: 'error',
      message: text(value.error.message) ?? '请求参数不对',
      redirectTo: text(value.error.redirectTo) ?? null,
    };
  }
  const client = isRecord(value) && isRecord(value.client) ? value.client : undefined;
  const clientName = text(client?.name);
  if (!clientName) return { kind: 'error', message: '请求参数不对', redirectTo: null };
  return { kind: 'ok', clientName, deviceName: str(isRecord(value) ? value.deviceName : undefined) ?? '' };
}

export async function checkAuthorization(search: string): Promise<AuthorizationCheck> {
  const query = new URLSearchParams(requestFields(search));
  return parseAuthorizationCheck(await request<unknown>(`/api/auth/oauth/authorize?${query.toString()}`));
}

/** 允许或拒绝；返回要回到的应用地址（带着授权码或拒绝的结果）。 */
export async function decideAuthorization(search: string, approve: boolean): Promise<string> {
  const result = await request<unknown>('/api/auth/oauth/authorize', {
    method: 'POST',
    body: { ...requestFields(search), approve },
  });
  const target = isRecord(result) ? text(result.redirectTo) : undefined;
  if (!target) throw new Error('没有拿到返回应用的地址');
  return target;
}

export interface DeviceLogin {
  readonly id: string;
  readonly clientName: string;
  readonly deviceName: string;
  readonly createdAt: string;
  readonly lastUsedAt: string;
}

export function parseDeviceLogin(value: unknown): DeviceLogin | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  return {
    id,
    clientName: str(value.clientName) ?? '',
    deviceName: str(value.deviceName) ?? '',
    createdAt: str(value.createdAt) ?? '',
    lastUsedAt: str(value.lastUsedAt) ?? '',
  };
}

export async function listDevices(): Promise<readonly DeviceLogin[]> {
  return listOf(await request<unknown>('/api/auth/devices'), parseDeviceLogin);
}

export function signOutDevice(id: string): Promise<void> {
  return request(`/api/auth/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
