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

export interface ConversationSummary {
  readonly id: string;
  readonly title: string;
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
    createdAt: text(value.createdAt) ?? null,
    updatedAt: text(value.updatedAt) ?? null,
  };
}

export async function listConversations(before?: string | null): Promise<ConversationPage> {
  const body = await request<unknown>(workspacePath('/conversations'), {
    query: { limit: PAGE_SIZE, before: before ?? undefined },
  });
  const record = isRecord(body) ? body : {};
  return { items: listOf(record.items, parseSummary), next: text(record.next) ?? null };
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

export async function createConversation(
  title: string,
  first: FirstTurn = {},
): Promise<{ readonly id: string; readonly title: string }> {
  const { answerMode, mentions = [] } = first;
  const body = await request<unknown>(workspacePath('/conversations'), {
    method: 'POST',
    body: {
      title: title.slice(0, MAX_TITLE_LENGTH),
      ...(mentions.length > 0
        ? {
            first_turn: {
              ...(answerMode ? { answer_mode: answerMode } : {}),
              mentions: mentions.map(mentionBody),
            },
          }
        : {}),
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

/** 回答语言（服务端 kernel/locale.py）：不传时服务端按提问里有没有汉字判断。 */
export type ReplyLanguage = 'zh' | 'en';

export const REPLY_LANGUAGES: readonly { readonly value: ReplyLanguage; readonly label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
];

/**
 * 回答方式（服务端 conversations/api.py 的 answer_mode）：智能体，或只查知识库（检索与阅读）的快速问答。
 * 每条消息都带，会话中途可以换；快速问答不能带附件，@ 的技能不算。
 */
export type AnswerMode = 'agent' | 'quick';

export const ANSWER_MODES: readonly { readonly value: AnswerMode; readonly label: string }[] = [
  { value: 'agent', label: '智能体' },
  { value: 'quick', label: '快速问答' },
];

/** 一条消息除了文字以外带的东西；不带的不发对应字段。 */
export interface SendExtras {
  /** 聊天页选的回答语言 */
  readonly language?: ReplyLanguage;
  /** 输入框里选的回答方式 */
  readonly answerMode?: AnswerMode;
  /** 本轮带的附件 id（先经 uploadForTurn 上传） */
  readonly attachments?: readonly string[];
  /** 用户在上一轮回答上方撤掉的检索条件（照检索结果给的写法），这一轮检索时去掉 */
  readonly droppedConditions?: readonly string[];
  /** 输入框里 @ 的知识库、目录、文件、标签、技能 */
  readonly mentions?: readonly Mention[];
}

export function sendMessage(
  conversationId: string,
  value: string,
  mode: SendMode,
  extras: SendExtras = {},
): Promise<{ messageId: string }> {
  const { language, answerMode, attachments = [], droppedConditions = [], mentions = [] } = extras;
  return request(workspacePath(`/conversations/${seg(conversationId)}/messages`), {
    method: 'POST',
    body: {
      text: value,
      mode,
      ...(language ? { language } : {}),
      ...(answerMode ? { answer_mode: answerMode } : {}),
      ...(attachments.length > 0 ? { attachments } : {}),
      ...(droppedConditions.length > 0 ? { dropped_conditions: droppedConditions } : {}),
      ...(mentions.length > 0 ? { mentions: mentions.map(mentionBody) } : {}),
    },
  });
}

export function cancelRun(conversationId: string): Promise<unknown> {
  return request(workspacePath(`/conversations/${seg(conversationId)}/cancel`), { method: 'POST' });
}

/** 审批只有批准与拒绝；「本会话内不再询问」后端暂不支持。 */
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
