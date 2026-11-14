/** 会话接口封装（server/src/knowwork_server/conversations/api.py）。 */
import { request, seg } from './http';
import { mentionBody, type Mention } from './mentions';
import { isRecord, listOf, str, text } from './parse';
import { workspacePath } from './workspace';

export type SendMode = 'followup' | 'steer';

export const MAX_MESSAGE_LENGTH = 200_000;
export const MAX_TITLE_LENGTH = 200;
export const PAGE_SIZE = 30;

export function validateMessage(value: string): string | null {
  if (!value.trim()) return '请输入内容';
  if (value.length > MAX_MESSAGE_LENGTH) return '内容太长，请精简到 20 万字以内';
  return null;
}

export function validateTitle(value: string): string | null {
  if (!value.trim()) return '请输入名称';
  if (value.trim().length > MAX_TITLE_LENGTH) return `名称不能超过 ${MAX_TITLE_LENGTH} 个字`;
  return null;
}

/** 对话的来源之一：智能体在用户设备上执行（原生应用的本机宿主）。这种对话只能在那台设备上继续，别处只读 */
export const DEVICE_SOURCE = 'device';

export interface ConversationSummary {
  readonly id: string;
  readonly title: string;
  /** 从哪来：IM 机器人认人时私聊的会话为平台（wecom 等）、在用户设备上执行的为 device，侧栏打来源标记；网页上开的为空 */
  readonly source: string;
  readonly createdAt: string | null;
  readonly updatedAt: string | null;
}

export interface ConversationPage {
  readonly items: readonly ConversationSummary[];
  /** 下一页的游标（交回给 before）；没有下一页时为 null。 */
  readonly next: string | null;
}

function parseSummary(value: unknown): ConversationSummary | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  return {
    id,
    title: str(value.title) ?? '',
    source: str(value.source) ?? '',
    createdAt: text(value.createdAt) ?? null,
    updatedAt: text(value.updatedAt) ?? null,
  };
}

/** 一段对话的基本信息（对话页按来源决定操作交给谁）。 */
export async function getConversation(conversationId: string): Promise<ConversationSummary | null> {
  return parseSummary(await request<unknown>(workspacePath(`/conversations/${seg(conversationId)}`))) ?? null;
}

export async function listConversations(before?: string | null): Promise<ConversationPage> {
  const body = await request<unknown>(workspacePath('/conversations'), {
    query: { limit: PAGE_SIZE, before: before ?? undefined },
  });
  const record = isRecord(body) ? body : {};
  return { items: listOf(record.items, parseSummary), next: text(record.next) ?? null };
}

/** 按编号一次取几段本人的对话（最多 50 个，最近有动静的在前；别人的、不存在的不回）：侧栏按别的东西分组时取列表里还没翻到的 */
export async function listConversationsByIds(
  ids: readonly string[],
): Promise<readonly ConversationSummary[]> {
  if (ids.length === 0) return [];
  const body = await request<unknown>(workspacePath('/conversations'), { query: { ids: ids.join(',') } });
  return listOf(isRecord(body) ? body.items : undefined, parseSummary);
}

/** 搜索命中的对话：snippet 是命中的那句问话的一段，只有标题命中时为空。 */
export interface ConversationHit extends ConversationSummary {
  readonly snippet: string;
}

/** 侧栏的搜索：标题与每轮问的话里有 query 的对话，最近有动静的在前（回答正文不搜）。 */
export async function searchConversations(query: string): Promise<readonly ConversationHit[]> {
  const body = await request<unknown>(workspacePath('/conversations/search'), { query: { q: query } });
  const record = isRecord(body) ? body : {};
  return listOf(record.items, (value) => {
    const summary = parseSummary(value);
    return summary && isRecord(value) ? { ...summary, snippet: str(value.snippet) ?? '' } : undefined;
  });
}

/** 新建会话时带上第一条消息的 @ 与回答方式：服务端建会话就按它定运行配置，第一条消息不用先开再重开。 */
export interface FirstTurn {
  readonly answerMode?: AnswerMode;
  readonly mentions?: readonly Mention[];
}

/**
 * 建对话时要挂的东西（服务端 conversations/scopes.py 的 ScopeClaim）：扩展登记的类型（例如 expert）与那一项的 id。
 * 建出来就挂上，之后按它的范围与身份来；不管在哪执行都是这一个。
 */
export interface ScopeClaim {
  readonly kind: string;
  readonly id: string;
}

export async function createConversation(
  title: string,
  first: FirstTurn = {},
  scope?: ScopeClaim,
): Promise<{ readonly id: string; readonly title: string }> {
  const { answerMode, mentions = [] } = first;
  // 第一轮的回答方式：服务端按它定进不进「最近」（只在扩展页面里用的方式，例如扩展页面右栏里的对话，不进）
  const firstTurn = {
    ...(answerMode ? { answer_mode: answerMode } : {}),
    ...(mentions.length > 0 ? { mentions: mentions.map(mentionBody) } : {}),
  };
  const body = await request<unknown>(workspacePath('/conversations'), {
    method: 'POST',
    body: {
      title: title.slice(0, MAX_TITLE_LENGTH),
      ...(Object.keys(firstTurn).length > 0 ? { first_turn: firstTurn } : {}),
      ...(scope ? { scope: { kind: scope.kind, id: scope.id } } : {}),
    },
  });
  const id = isRecord(body) ? text(body.id) : undefined;
  if (!id) throw new Error('服务返回的数据不完整，请稍后重试');
  return { id, title: isRecord(body) ? (str(body.title) ?? '') : '' };
}

export function renameConversation(conversationId: string, title: string): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}`), {
    method: 'PATCH',
    body: { title: title.trim().slice(0, MAX_TITLE_LENGTH) },
  });
}

export function deleteConversation(conversationId: string): Promise<void> {
  return request(workspacePath(`/conversations/${seg(conversationId)}`), { method: 'DELETE' });
}

/** 扩展给这段对话定的范围（服务端 conversations/scopes.py）：顶部显示的名字，和固定的回答方式（固定了就不让换）。 */
export interface ConversationScope {
  readonly label: string;
  readonly answerMode: AnswerMode | null;
}

export async function getConversationScope(conversationId: string): Promise<ConversationScope | null> {
  const body = await request<unknown>(workspacePath(`/conversations/${seg(conversationId)}/scope`));
  if (!isRecord(body)) return null;
  const label = text(body.label);
  return label ? { label, answerMode: text(body.answerMode) ?? null } : null;
}

/** 回答语言（服务端 kernel/locale.py）：不传时服务端按提问里有没有汉字判断。 */
export type ReplyLanguage = 'zh' | 'en';

export const REPLY_LANGUAGES: readonly { readonly value: ReplyLanguage; readonly label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
];

/**
 * 回答方式（服务端 conversations/answer_modes.py）：内置智能体、只查知识库（检索与阅读）的快速问答，扩展可以再登记别的。
 * 每条消息都带，会话中途可以换；快速问答不能带附件，@ 的技能不算。可选的项从服务端取（listAnswerModes）。
 */
export type AnswerMode = string;

export interface AnswerModeOption {
  readonly id: AnswerMode;
  readonly label: string;
  readonly description: string;
}

/** 内置的两种：可选项取到之前、取不到时用 */
export const BUILT_IN_ANSWER_MODES: readonly AnswerModeOption[] = [
  { id: 'agent', label: '智能体', description: '能用技能、读文件、执行任务，适合复杂的事' },
  { id: 'quick', label: '快速问答', description: '只查知识库，回答更快' },
];

/** 这个空间里我能选的回答方式；服务端没给出可用的就用内置两种。 */
export async function listAnswerModes(): Promise<readonly AnswerModeOption[]> {
  const body = await request<unknown>(workspacePath('/answer-modes'));
  const record = isRecord(body) ? body : {};
  const found = listOf(record.items, (value) => {
    if (!isRecord(value)) return undefined;
    const id = text(value.id);
    const label = text(value.label);
    return id && label ? { id, label, description: str(value.description) ?? '' } : undefined;
  });
  return found.length > 0 ? found : BUILT_IN_ANSWER_MODES;
}

/** 一条消息除了文字以外带的东西；不带的不发对应字段。 */
export interface SendExtras {
  /** 聊天页选的回答语言 */
  readonly language?: ReplyLanguage;
  /** 输入框里选的回答方式 */
  readonly answerMode?: AnswerMode;
  /** 本轮带的附件 id（输入框里已经传好暂存，发消息时服务端认领进会话） */
  readonly attachments?: readonly string[];
  /** 用户在上一轮回答上方撤掉的检索条件（照检索结果给的写法），这一轮检索时去掉 */
  readonly droppedConditions?: readonly string[];
  /** 输入框里 @ 的知识库、目录、文件、标签、技能 */
  readonly mentions?: readonly Mention[];
  /** 输入框右下角选的对话模型（别名）；不带 = 这段对话沿用上次选的 */
  readonly model?: string;
}

/** 浏览器的时区（IANA 名字）：随每条消息带上，模型按它算「今天」；取不到就不带，服务端用部署时区。 */
export function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/**
 * 发一句的请求（服务端 conversations/api.py 的 SendMessage）。云端发消息、在用户设备上执行的对话开始一轮收的是同一个：
 * 只由 turnRequest 拼，来源原样交出去（不在业务服务上执行的，由那台设备交给业务服务准备这一轮）。
 */
export interface TurnRequest {
  readonly text: string;
  readonly mode: SendMode;
  readonly language?: ReplyLanguage;
  readonly time_zone?: string;
  readonly answer_mode?: AnswerMode;
  readonly attachments?: readonly string[];
  readonly dropped_conditions?: readonly string[];
  readonly mentions?: readonly Record<string, unknown>[];
  readonly model?: string;
}

export function turnRequest(value: string, mode: SendMode, extras: SendExtras = {}): TurnRequest {
  const { language, answerMode, attachments = [], droppedConditions = [], mentions = [], model } = extras;
  const timeZone = browserTimeZone();
  return {
    text: value,
    mode,
    ...(language ? { language } : {}),
    ...(timeZone ? { time_zone: timeZone } : {}),
    ...(answerMode ? { answer_mode: answerMode } : {}),
    ...(attachments.length > 0 ? { attachments } : {}),
    ...(droppedConditions.length > 0 ? { dropped_conditions: droppedConditions } : {}),
    ...(mentions.length > 0 ? { mentions: mentions.map(mentionBody) } : {}),
    ...(model ? { model } : {}),
  };
}

export function sendMessage(conversationId: string, turn: TurnRequest): Promise<{ messageId: string }> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/messages`), {
    method: 'POST',
    body: turn,
  });
}

/** 输入框能选的一个对话模型（服务端 models/api.py 的 chat-models）。 */
export interface ChatModelOption {
  readonly alias: string;
  readonly name: string;
  /** 能看图 */
  readonly vision: boolean;
  /** 平台的默认模型 */
  readonly isDefault: boolean;
}

export interface ChatModels {
  readonly items: readonly ChatModelOption[];
  /** 默认选哪个：这段对话上次用的；新对话用这个人上次用的；都没有用默认模型 */
  readonly selected: string | null;
}

export async function listChatModels(conversationId?: string): Promise<ChatModels> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
  const body = await request<unknown>(workspacePath(`/chat-models${query}`));
  const record = isRecord(body) ? body : {};
  const items = listOf(record.items, (value) => {
    if (!isRecord(value)) return undefined;
    const alias = text(value.alias);
    if (!alias) return undefined;
    return {
      alias,
      name: str(value.name) ?? alias,
      vision: value.vision === true,
      isDefault: value.default === true,
    };
  });
  const selected = text(record.selected);
  return { items, selected: selected && items.some((m) => m.alias === selected) ? selected : null };
}

/**
 * 预热对话：输入框里加了附件、快要发消息时调，服务端先把宿主上的对话和沙箱容器备好（空闲半小时会被回收，冷着发要多等
 * 两三秒）。尽力而为，结果不用管。
 */
export function warmConversation(conversationId: string): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/warm`), { method: 'POST' });
}

export function cancelRun(conversationId: string): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/cancel`), { method: 'POST' });
}

/** remember：批准并勾了「本会话内同类操作不再询问」，服务端记会话授权，之后同一工具不再弹审批。 */
export function decideApproval(
  conversationId: string,
  approvalId: string,
  allow: boolean,
  remember = false,
): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/approvals/${seg(approvalId)}`), {
    method: 'POST',
    body: allow && remember ? { allow, scope: 'session' } : { allow },
  });
}

/** 一题的回答：selected 是选项原标签；单选题填了 custom 时 selected 留空；两者都空表示跳过。 */
export interface QuestionAnswer {
  readonly id: string;
  readonly selected: readonly string[];
  readonly custom?: string;
}

export const MAX_ANSWER_LENGTH = 4000;

/** 回答 Agent 的一次提问，每题一条。问题已结束时报 ApiError，code 为 question_not_found。 */
export function answerQuestion(
  conversationId: string,
  questionId: string,
  answers: readonly QuestionAnswer[],
): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/questions/${seg(questionId)}`), {
    method: 'POST',
    body: { answers },
  });
}

export function eventsUrl(conversationId: string): string {
  return workspacePath(`/conversations/${seg(conversationId)}/events`);
}

/**
 * 对话里的文件按路径读（方案 7.7.3）：content 是原件，preview 是给 PDF 阅读器的 PDF（Word、PPT 转换后的，PDF 原样）。
 * 路径照模型写的样子交出去，服务端换算与核对。
 */
export function conversationFileUrl(
  conversationId: string,
  path: string,
  kind: 'content' | 'preview' = 'content',
): string {
  return `${workspacePath(`/conversations/${seg(conversationId)}/files/${kind}`)}?path=${encodeURIComponent(path)}`;
}
