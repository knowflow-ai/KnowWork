/**
 * 渠道：企业微信等机器人与网页嵌入（server/src/knowwork_server/channels/api.py）。Secret 只能写不能读，接口只回末四位。
 * 网页嵌入（web）：没有机器人与 Secret，嵌进别的网站或用分享链接打开；发布令牌（嵌入代码里那串）本来就公开，接口照回。
 * 网页嵌入的认证访问（audience 为 member）：接入方后端用认证密钥签 JWT 告诉我们来的是谁，密钥明文只在生成时回一次。
 * 回答方式：内置两种查知识库；扩展登记、写了查哪类资源的也能选（例如查数据源的），选的资料在 resources。
 * 不认人（public）：私聊、群聊都用渠道账号（和渠道同 id）回答，能查的就是选的资料。
 * 认人（member）：私聊按提问人本人的权限回答，用选的组织来源认人；选的资料只管群聊（群里仍用渠道账号）。
 * 网页嵌入可以挂一个扩展登记的对象（settings.target，例如 expert:<id>）：照它答，回答方式与资料都跟着它，不在渠道上选。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

export type ChannelPlatform = 'wecom' | 'feishu' | 'dingtalk' | 'web';
/** 回答方式的 id：内置 agent、quick，扩展登记的另有名字 */
export type AnswerMode = string;
export type ChannelStatus = 'stopped' | 'connecting' | 'connected' | 'error';
/** 提问的人：public 不认人，member 认出是谁 */
export type ChannelAudience = 'public' | 'member';

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
  {
    value: 'web',
    label: '网页嵌入',
    idLabel: '',
    secretLabel: '',
    guide:
      '把智能助手嵌进你们自己的网站（右下角小窗或整页），或者发分享链接；访客不用登录。保存后在「嵌入代码」里复制。',
  },
];

/** 网页嵌入的公开配置（嵌入页、嵌入脚本不登录就读得到，只放能公开的） */
export interface WebOptions {
  /** 允许嵌入的网站（https://bi.example.com，子域名可以写 https://*.example.com）；空着只能用分享链接 */
  readonly origins: readonly string[];
  readonly title: string;
  readonly greeting: string;
  readonly starters: readonly string[];
  /** 悬浮按钮的颜色（#RRGGBB），空着用默认 */
  readonly color: string;
  readonly position: 'right' | 'left';
  /** 每个访客每分钟、整个渠道每天最多问几次 */
  readonly perMinute: number;
  readonly perDay: number;
}

export interface WebChannel extends WebOptions {
  /** 发布令牌：嵌入代码、分享链接里那串 */
  readonly webKey: string;
}

export const DEFAULT_WEB_OPTIONS: WebOptions = {
  origins: [],
  title: '',
  greeting: '',
  starters: [],
  color: '',
  position: 'right',
  perMinute: 10,
  perDay: 1000,
};

export const platformOf = (value: ChannelPlatform): PlatformInfo =>
  PLATFORMS.find((p) => p.value === value) ?? PLATFORMS[0]!;

export interface ChannelSettings {
  readonly answerMode: AnswerMode;
  /** 机器人能查的知识库，保存时同步成渠道账号的授权；认人时只管群聊 */
  readonly bases: readonly string[];
  /** 回答方式查的不是知识库时（例如数据源），选的那一类资源的 id，同样同步成授权 */
  readonly resources: readonly string[];
  /** 认人用的组织来源（网页嵌入的认证访问可以不选：用接入方系统自己的用户编号） */
  readonly identitySource: string | null;
  /** 网页嵌入的认证访问：对不上 KnowWork 账号的人自动建外部用户 */
  readonly autoCreate: boolean;
  /** 网页嵌入的认证访问：接入方的组 → 用户组 id（外部用户每次进来按它同步这几个组） */
  readonly groupMap: Readonly<Record<string, string>>;
  /** 网页嵌入挂的对象（例如 expert:<id>）；没挂为 null */
  readonly target: string | null;
}

/** 渠道挂着的对象：哪一类（例如「专家」）、叫什么；对象删了 name 为 null */
export interface ChannelTargetRef {
  readonly ref: string;
  readonly label: string;
  readonly name: string | null;
}

/** 配网页嵌入时能挂的对象（扩展登记的，例如专家） */
export interface ChannelTarget {
  readonly ref: string;
  readonly label: string;
  readonly name: string;
  readonly answerMode: string;
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
  readonly audience: ChannelAudience;
  /** 网页嵌入的发布令牌与公开配置；别的平台为 null */
  readonly web: WebChannel | null;
  /** 网页嵌入挂着的对象；没挂为 null */
  readonly target: ChannelTargetRef | null;
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
  readonly audience: ChannelAudience;
  /** 网页嵌入的公开配置（platform 是 web 时才发） */
  readonly web: WebOptions;
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
  /** 认出的人：本人的用户 id 与姓名；渠道账号回答的为空 */
  readonly userId: string;
  readonly userName: string;
}

/** 认人的渠道里私聊了却没认出来的人 */
export type StrangerReason = 'unknown' | 'inactive' | 'not_member';

export interface ChannelStranger {
  readonly externalId: string;
  readonly displayName: string;
  readonly reason: StrangerReason;
  readonly attempts: number;
  readonly lastAt: string | null;
}

/** 渠道可选的回答方式 */
export interface ChannelAnswerMode {
  readonly id: AnswerMode;
  readonly label: string;
  readonly description: string;
  /** 群聊（不认人时私聊也是）按哪类资源选：knowledge_base 选知识库，别的按授权页登记的那一类（插槽 grants.resources） */
  readonly resourceType: string;
  readonly resourceLabel: string;
}

export const KNOWLEDGE_RESOURCE = 'knowledge_base';

/** 接口没取到时先显示内置的两种 */
export const BUILT_IN_CHANNEL_MODES: readonly ChannelAnswerMode[] = [
  {
    id: 'agent',
    label: '智能体',
    description: '能用技能、读文件、执行任务，适合复杂的事',
    resourceType: KNOWLEDGE_RESOURCE,
    resourceLabel: '知识库',
  },
  {
    id: 'quick',
    label: '快速问答',
    description: '只查知识库，回答更快',
    resourceType: KNOWLEDGE_RESOURCE,
    resourceLabel: '知识库',
  },
];

/** 认人可选的组织来源 */
export interface IdentitySource {
  readonly id: string;
  readonly name: string;
  /** 开了「用它登录」：没认出的人能点免登入口自己对上 */
  readonly login: boolean;
}

const STATUSES: readonly ChannelStatus[] = ['stopped', 'connecting', 'connected', 'error'];

function parseSettings(value: unknown): ChannelSettings {
  const r = isRecord(value) ? value : {};
  return {
    answerMode: text(r.answer_mode) ?? 'agent',
    bases: strings(r.bases),
    resources: strings(r.resources),
    identitySource: text(r.identity_source) ?? null,
    autoCreate: bool(r.auto_create, true),
    groupMap: groupMapOf(r.group_map),
    target: text(r.target) ?? null,
  };
}

function parseTargetRef(value: unknown): ChannelTargetRef | null {
  if (!isRecord(value)) return null;
  const ref = text(value.ref);
  if (!ref) return null;
  return { ref, label: str(value.label) ?? '', name: text(value.name) ?? null };
}

function parseTarget(value: unknown): ChannelTarget | undefined {
  if (!isRecord(value)) return undefined;
  const ref = text(value.ref);
  const name = text(value.name);
  if (!ref || !name) return undefined;
  return { ref, label: str(value.label) ?? '', name, answerMode: str(value.answer_mode) ?? '' };
}

function groupMapOf(value: unknown): Readonly<Record<string, string>> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([name, group]) => (typeof group === 'string' ? [[name, group]] : [])),
  );
}

function parseWeb(value: unknown): WebChannel | null {
  if (!isRecord(value)) return null;
  const webKey = text(value.web_key);
  if (!webKey) return null;
  return {
    webKey,
    origins: strings(value.origins),
    title: str(value.title) ?? '',
    greeting: str(value.greeting) ?? '',
    starters: strings(value.starters),
    color: str(value.color) ?? '',
    position: value.position === 'left' ? 'left' : 'right',
    perMinute: num(value.per_minute) ?? DEFAULT_WEB_OPTIONS.perMinute,
    perDay: num(value.per_day) ?? DEFAULT_WEB_OPTIONS.perDay,
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
    audience: value.audience === 'member' ? 'member' : 'public',
    web: parseWeb(value.web),
    target: parseTargetRef(value.target),
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
    userId: str(value.user_id) ?? '',
    userName: str(value.user_name) ?? '',
  };
}

const REASONS: readonly StrangerReason[] = ['unknown', 'inactive', 'not_member'];

function parseStranger(value: unknown): ChannelStranger | undefined {
  if (!isRecord(value)) return undefined;
  const externalId = text(value.external_id);
  if (!externalId) return undefined;
  return {
    externalId,
    displayName: str(value.display_name) ?? '',
    reason: REASONS.find((r) => r === value.reason) ?? 'unknown',
    attempts: num(value.attempts) ?? 1,
    lastAt: text(value.last_at) ?? null,
  };
}

function parseAnswerMode(value: unknown): ChannelAnswerMode | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const resourceType = text(value.resource_type);
  if (!id || !resourceType) return undefined;
  return {
    id,
    label: str(value.label) ?? id,
    description: str(value.description) ?? '',
    resourceType,
    resourceLabel: str(value.resource_label) ?? resourceType,
  };
}

function parseIdentitySource(value: unknown): IdentitySource | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  return { id, name: str(value.name) ?? id, login: bool(value.login) };
}

const base = (path = '') => workspacePath(`/channels${path}`);

function webBody(web: WebOptions) {
  return {
    origins: web.origins,
    title: web.title.trim(),
    greeting: web.greeting.trim(),
    starters: web.starters,
    color: web.color.trim(),
    position: web.position,
    per_minute: web.perMinute,
    per_day: web.perDay,
  };
}

function body(input: ChannelInput) {
  return {
    ...(input.platform === 'web' ? { web: webBody(input.web) } : {}),
    name: input.name.trim(),
    platform: input.platform,
    enabled: input.enabled,
    bot_id: input.botId.trim(),
    bot_name: input.botName.trim(),
    secret: input.secret.trim() || null,
    audience: input.audience,
    settings: {
      answer_mode: input.settings.answerMode,
      bases: input.settings.bases,
      resources: input.settings.resources,
      identity_source: input.audience === 'member' ? input.settings.identitySource : null,
      ...(input.platform === 'web' && input.audience === 'member'
        ? { auto_create: input.settings.autoCreate, group_map: input.settings.groupMap }
        : {}),
      ...(input.platform === 'web' ? { target: input.settings.target } : {}),
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

/** 生成或换一个认证密钥（网页嵌入的认证访问，接入方后端用它签 JWT）：明文只在这一次返回 */
export async function resetWebSecret(
  id: string,
): Promise<{ readonly channel?: Channel; readonly secret: string }> {
  const body = await request<unknown>(base(`/${seg(id)}/web-secret`), { method: 'POST' });
  const record = isRecord(body) ? body : {};
  const secret = text(record.secret);
  if (!secret) throw new Error('服务返回的数据不完整，请稍后重试');
  return { channel: parseChannel(record.channel), secret };
}

/** 换一个发布令牌：旧的嵌入代码、分享链接与已经打开的嵌入页一起失效 */
export async function resetWebKey(id: string): Promise<Channel | undefined> {
  return parseChannel(await request<unknown>(base(`/${seg(id)}/web-key`), { method: 'POST' }));
}

export async function deleteChannel(id: string): Promise<unknown> {
  return request(base(`/${seg(id)}`), { method: 'DELETE' });
}

export async function listChannelSessions(id: string): Promise<readonly ChannelSession[]> {
  const page = await request<unknown>(base(`/${seg(id)}/sessions`));
  return listOf(isRecord(page) ? page.items : [], parseSession);
}

export async function listChannelStrangers(id: string): Promise<readonly ChannelStranger[]> {
  const page = await request<unknown>(base(`/${seg(id)}/strangers`));
  return listOf(isRecord(page) ? page.items : [], parseStranger);
}

/** 渠道可选的回答方式（带群聊按哪类资源选） */
export async function listChannelAnswerModes(): Promise<readonly ChannelAnswerMode[]> {
  const page = await request<unknown>(base('/answer-modes'));
  return listOf(isRecord(page) ? page.items : [], parseAnswerMode);
}

/** 网页嵌入能挂的对象（扩展登记的，例如专家）；没有扩展登记时为空 */
export async function listChannelTargets(): Promise<readonly ChannelTarget[]> {
  const page = await request<unknown>(base('/targets'));
  return listOf(isRecord(page) ? page.items : [], parseTarget);
}

/** 认人可选的组织来源（同平台）；没有能认人的组织来源时为空 */
export async function listIdentitySources(platform: ChannelPlatform): Promise<readonly IdentitySource[]> {
  const page = await request<unknown>(base('/identity-sources'), { query: { platform } });
  return listOf(isRecord(page) ? page.items : [], parseIdentitySource);
}
