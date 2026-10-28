/**
 * 渠道：企业微信等机器人（server/src/knowwork_server/channels/api.py）。Secret 只能写不能读，接口只回末四位。
 * 机器人不认人：私聊、群聊都用渠道账号（和渠道同 id）回答，能查的就是选的知识库。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

export type ChannelPlatform = 'wecom' | 'feishu' | 'dingtalk';
export type AnswerMode = 'agent' | 'quick';
export type ChannelStatus = 'stopped' | 'connecting' | 'connected' | 'error';

export interface PlatformInfo {
  readonly value: ChannelPlatform;
  readonly label: string;
  /** 凭据字段在各平台叫什么 */
  readonly idLabel: string;
  readonly secretLabel: string;
  /** 到哪里拿凭据、要开哪些设置 */
  readonly guide: string;
}

export const PLATFORMS: readonly PlatformInfo[] = [
  {
    value: 'wecom',
    label: '企业微信',
    idLabel: 'Bot ID',
    secretLabel: 'Secret',
    guide:
      '企业微信管理后台 → 智能机器人 → 新建，选 API 模式（长连接），记下 Bot ID 与 Secret，设置可见范围。',
  },
  {
    value: 'feishu',
    label: '飞书',
    idLabel: 'App ID',
    secretLabel: 'App Secret',
    guide:
      '飞书开放平台 → 企业自建应用：开启机器人能力；事件订阅选「使用长连接接收事件」并订阅「接收消息」；开通单聊、群聊 @ 机器人消息的读取与发送权限，以及卡片（CardKit）权限；发布版本。',
  },
  {
    value: 'dingtalk',
    label: '钉钉',
    idLabel: 'Client ID（AppKey）',
    secretLabel: 'Client Secret（AppSecret）',
    guide:
      '钉钉开放平台 → 应用开发 → 企业内部应用：添加机器人能力，消息接收模式选 Stream 模式；开通互动卡片的写入与流式更新权限；发布。',
  },
];

export const platformOf = (value: ChannelPlatform): PlatformInfo =>
  PLATFORMS.find((p) => p.value === value) ?? PLATFORMS[0]!;

export interface ChannelSettings {
  readonly answerMode: AnswerMode;
  /** 机器人能查的知识库，保存时同步成渠道账号的授权 */
  readonly bases: readonly string[];
}

export interface Channel {
  readonly id: string;
  readonly platform: ChannelPlatform;
  readonly name: string;
  readonly enabled: boolean;
  readonly botId: string;
  readonly botName: string;
  readonly secretMask: string;
  readonly secretSet: boolean;
  readonly settings: ChannelSettings;
  readonly status: ChannelStatus;
  readonly lastError: string;
  readonly statusAt: string | null;
  /** 在用的对话位置数（私聊的人、群里的人） */
  readonly sessions: number;
}

export interface ChannelInput {
  readonly name: string;
  readonly platform: ChannelPlatform;
  readonly enabled: boolean;
  readonly botId: string;
  readonly botName: string;
  /** 修改时留空表示不改 */
  readonly secret: string;
  readonly settings: ChannelSettings;
}

/** 渠道里的一个对话位置：私聊的一个人，或某个群里的一个人。 */
export interface ChannelSession {
  readonly externalId: string;
  readonly displayName: string;
  readonly chatType: 'direct' | 'group';
  readonly chatId: string;
  readonly conversationId: string;
  readonly title: string;
  readonly updatedAt: string | null;
}

const STATUSES: readonly ChannelStatus[] = ['stopped', 'connecting', 'connected', 'error'];

function parseSettings(value: unknown): ChannelSettings {
  const r = isRecord(value) ? value : {};
  return {
    answerMode: r.answer_mode === 'quick' ? 'quick' : 'agent',
    bases: strings(r.bases),
  };
}

function parseChannel(value: unknown): Channel | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  const status = STATUSES.find((s) => s === value.status) ?? 'stopped';
  const platform = PLATFORMS.find((p) => p.value === value.platform)?.value ?? 'wecom';
  return {
    id,
    platform,
    name: str(value.name) ?? '',
    enabled: bool(value.enabled, true),
    botId: str(value.bot_id) ?? '',
    botName: str(value.bot_name) ?? '',
    secretMask: str(value.secret_mask) ?? '',
    secretSet: bool(value.secret_set),
    settings: parseSettings(value.settings),
    status,
    lastError: str(value.last_error) ?? '',
    statusAt: text(value.status_at) ?? null,
    sessions: num(value.sessions) ?? 0,
  };
}

function parseSession(value: unknown): ChannelSession | undefined {
  if (!isRecord(value)) return undefined;
  const externalId = text(value.external_id);
  const conversationId = text(value.conversation_id);
  if (!externalId || !conversationId) return undefined;
  return {
    externalId,
    displayName: str(value.display_name) ?? '',
    chatType: value.chat_type === 'group' ? 'group' : 'direct',
    chatId: str(value.chat_id) ?? '',
    conversationId,
    title: str(value.title) ?? '',
    updatedAt: text(value.updated_at) ?? null,
  };
}

const base = (path = '') => workspacePath(`/channels${path}`);

function body(input: ChannelInput) {
  return {
    name: input.name.trim(),
    platform: input.platform,
    enabled: input.enabled,
    bot_id: input.botId.trim(),
    bot_name: input.botName.trim(),
    secret: input.secret.trim() || null,
    settings: {
      answer_mode: input.settings.answerMode,
      bases: input.settings.bases,
    },
  };
}

export async function listChannels(): Promise<readonly Channel[]> {
  const page = await request<unknown>(base());
  return listOf(isRecord(page) ? page.items : [], parseChannel);
}

export async function createChannel(input: ChannelInput): Promise<Channel | undefined> {
  return parseChannel(await request<unknown>(base(), { method: 'POST', body: body(input) }));
}

export async function updateChannel(id: string, input: ChannelInput): Promise<Channel | undefined> {
  return parseChannel(await request<unknown>(base(`/${seg(id)}`), { method: 'PUT', body: body(input) }));
}

export async function setChannelEnabled(id: string, enabled: boolean): Promise<unknown> {
  return request(base(`/${seg(id)}/enabled`), { method: 'POST', body: { enabled } });
}

export async function deleteChannel(id: string): Promise<unknown> {
  return request(base(`/${seg(id)}`), { method: 'DELETE' });
}

export async function listChannelSessions(id: string): Promise<readonly ChannelSession[]> {
  const page = await request<unknown>(base(`/${seg(id)}/sessions`));
  return listOf(isRecord(page) ? page.items : [], parseSession);
}
