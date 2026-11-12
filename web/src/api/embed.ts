/**
 * 网页嵌入的公开接口（服务端 channels/web_api.py）：嵌入页与分享链接不登录就要读配置、换会话令牌。
 * 对话本身走普通的会话接口，带上换到的令牌（embedAuth.ts）。
 */
import { request, seg } from './http';
import { isRecord, listOf, str, text } from './parse';

export interface EmbedConfig {
  readonly title: string;
  readonly greeting: string;
  readonly starters: readonly string[];
  /** 悬浮按钮的颜色（#RRGGBB）；空着用默认 */
  readonly color: string;
  readonly position: 'right' | 'left';
  readonly answerMode: string;
  /** public：公开访问（访客不用登录）；member：认证访问（接入方系统告诉我们来的是谁） */
  readonly access: 'public' | 'member';
}

export interface EmbedSession {
  readonly token: string;
  /** 过期时间（Unix 秒） */
  readonly expiresAt: number;
  readonly workspaceId: string;
  readonly channelId: string;
}

const base = (key: string) => `/api/embed/v1/channels/${seg(key)}`;

export async function fetchEmbedConfig(key: string): Promise<EmbedConfig> {
  const body = await request<unknown>(base(key), { quietUnauthorized: true });
  const record = isRecord(body) ? body : {};
  return {
    title: str(record.title) ?? '',
    greeting: str(record.greeting) ?? '',
    starters: listOf(record.starters, (value) => text(value)),
    color: str(record.color) ?? '',
    position: record.position === 'left' ? 'left' : 'right',
    answerMode: text(record.answerMode) ?? 'agent',
    access: record.access === 'member' ? 'member' : 'public',
  };
}

function sessionOf(body: unknown): EmbedSession {
  const record = isRecord(body) ? body : {};
  const token = text(record.token);
  const workspaceId = text(record.workspaceId);
  if (!token || !workspaceId) throw new Error('服务返回的数据不完整，请稍后重试');
  return {
    token,
    expiresAt: typeof record.expiresAt === 'number' ? record.expiresAt : 0,
    workspaceId,
    channelId: text(record.channelId) ?? '',
  };
}

/**
 * 换会话令牌：公开访问带访客 id（只用来接着上一段对话，不是凭据）；认证访问带接入方后端签的 JWT（只能用一次）。
 */
export async function openEmbedSession(
  key: string,
  proof: { readonly visitor: string } | { readonly token: string },
): Promise<EmbedSession> {
  return sessionOf(
    await request<unknown>(`${base(key)}/sessions`, {
      method: 'POST',
      body: proof,
      quietUnauthorized: true,
    }),
  );
}

/** 用着就续：带着还没过期的令牌（embedAuth 里那个）换一个新的。 */
export async function renewEmbedSession(): Promise<EmbedSession> {
  return sessionOf(
    await request<unknown>('/api/embed/v1/sessions/renew', { method: 'POST', quietUnauthorized: true }),
  );
}
