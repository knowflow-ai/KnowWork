/**
 * 新开一个会话并发出第一条：建会话（可以带上要挂的东西，例如专家）→ （可选）外层先做点事，例如把会话绑到
 * 某个范围 → 带着附件 id 发首条消息（只建不发的：执行 / 命令，或输入框里的附件直接进编辑器）
 * （附件在输入框里就传好了，发消息时服务端把它们认领进这个会话）。返回会话 id 与刚发的那条
 * （进入会话视图时先摆上，回显到了按消息 id 合并）。
 */
import { chipsOf, type SentAttachment } from '../../api/attachments';
import {
  createConversation,
  runCommand,
  sendMessage,
  turnRequest,
  type AnswerMode,
  type ScopeClaim,
  type TurnRequest,
} from '../../api/conversations';
import type { Mention } from '../../api/mentions';
import { currentReplyLanguage } from '../../stores/replyLanguage';
import { truncate } from '../../utils/format';
import type { FirstMessage } from './hooks/useConversation';

interface Start {
  readonly text: string;
  readonly attachments: readonly SentAttachment[];
  readonly mentions: readonly Mention[];
  readonly answerMode: AnswerMode;
  /** 要挂的东西（例如专家）：建对话时就挂上，第一轮就按它的范围与身份跑 */
  readonly scope?: ScopeClaim;
  /** 会话建好、首条发出之前调：外层在这里绑定范围，第一轮就按它跑 */
  readonly onCreated?: (conversationId: string) => Promise<void>;
  /** 新对话页直接执行的一行 / 命令：建好对话后执行它，不发第一句 */
  readonly command?: string;
  /** 只建对话、不发第一句，标题用 text（输入框里的附件直接进编辑器） */
  readonly blank?: boolean;
}

/** 新对话页发出的第一句（云端和别的来源用同一个）：回答语言取聊天页选的。 */
export function firstTurn({
  text,
  attachments,
  mentions,
  answerMode,
}: Pick<Start, 'text' | 'attachments' | 'mentions' | 'answerMode'>): TurnRequest {
  return turnRequest(text, 'followup', {
    language: currentReplyLanguage(),
    answerMode,
    attachments: attachments.map((item) => item.id),
    mentions,
  });
}

export async function startConversation({
  text,
  attachments,
  mentions,
  answerMode,
  scope,
  onCreated,
  command,
  blank,
}: Start): Promise<{ readonly id: string; readonly firstMessage?: FirstMessage }> {
  // @ 与要挂的东西一起带给建会话：第一条就 @、就按专家来时不用先开再重开宿主会话。
  // 标题先用第一句话的前 30 个字占位，官方起的标题到了会换掉（对话标题以官方为准，业务服务从会话日志同步）
  const title = truncate(command ?? text, 30);
  const id = (await createConversation(title, { answerMode, mentions }, scope)).id;
  await onCreated?.(id);
  if (blank) return { id };
  // 直接执行命令：对话建好（宿主上开着）就执行，结果随事件进对话；没有第一句
  if (command) {
    await runCommand(id, command);
    return { id };
  }
  const { messageId } = await sendMessage(id, firstTurn({ text, attachments, mentions, answerMode }));
  return { id, firstMessage: { text, attachments: chipsOf(attachments), mentions, messageId } };
}
