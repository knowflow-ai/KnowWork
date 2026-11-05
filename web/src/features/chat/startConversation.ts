/**
 * 新开一个会话并发出第一条：建会话 → （可选）外层先做点事，例如把会话绑到某个范围 → 把文件放进会话工作区 →
 * 带着附件 id 发首条消息。返回会话 id 与刚发的那条（进入会话视图时先摆上，回显到了按消息 id 合并）。
 */
import { chipsOf, uploadForTurn } from '../../api/attachments';
import { createConversation, sendMessage, type AnswerMode } from '../../api/conversations';
import type { Mention } from '../../api/mentions';
import { currentReplyLanguage } from '../../stores/replyLanguage';
import { truncate } from '../../utils/format';
import type { FirstMessage } from './hooks/useConversation';

interface Start {
  readonly text: string;
  readonly files: readonly File[];
  readonly mentions: readonly Mention[];
  readonly answerMode: AnswerMode;
  /** 会话建好、首条发出之前调：外层在这里绑定范围，第一轮就按它跑 */
  readonly onCreated?: (conversationId: string) => Promise<void>;
}

export async function startConversation({
  text,
  files,
  mentions,
  answerMode,
  onCreated,
}: Start): Promise<{ readonly id: string; readonly firstMessage: FirstMessage }> {
  // @ 一起带给建会话：第一条就 @ 时不用先开再重开宿主会话
  const { id } = await createConversation(truncate(text, 30), { answerMode, mentions });
  await onCreated?.(id);
  // 附件要先有会话才能传
  const attachments = await uploadForTurn(id, files);
  const { messageId } = await sendMessage(id, text, 'followup', {
    language: currentReplyLanguage(),
    answerMode,
    attachments,
    mentions,
  });
  return { id, firstMessage: { text, attachments: chipsOf(files), mentions, messageId } };
}
