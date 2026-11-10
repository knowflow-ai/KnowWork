/**
 * 新开一个会话并发出第一条：建会话（外层可以自己建，例如扩展先挂上范围再建）→ （可选）外层先做点事，例如把会话绑到
 * 某个范围 → 带着附件 id 发首条消息
 * （附件在输入框里就传好了，发消息时服务端把它们认领进这个会话）。返回会话 id 与刚发的那条
 * （进入会话视图时先摆上，回显到了按消息 id 合并）。
 */
import { chipsOf, type SentAttachment } from '../../api/attachments';
import { createConversation, sendMessage, type AnswerMode } from '../../api/conversations';
import type { Mention } from '../../api/mentions';
import { currentReplyLanguage } from '../../stores/replyLanguage';
import { truncate } from '../../utils/format';
import type { FirstMessage } from './hooks/useConversation';

interface Start {
  readonly text: string;
  readonly attachments: readonly SentAttachment[];
  readonly mentions: readonly Mention[];
  readonly answerMode: AnswerMode;
  /** 会话建好、首条发出之前调：外层在这里绑定范围，第一轮就按它跑 */
  readonly onCreated?: (conversationId: string) => Promise<void>;
  /** 外层自己建会话（拿到标题，返回会话 id）；不给就照常建 */
  readonly create?: (title: string) => Promise<string>;
}

export async function startConversation({
  text,
  attachments,
  mentions,
  answerMode,
  onCreated,
  create,
}: Start): Promise<{ readonly id: string; readonly firstMessage: FirstMessage }> {
  // @ 一起带给建会话：第一条就 @ 时不用先开再重开宿主会话
  const title = truncate(text, 30);
  const id = create ? await create(title) : (await createConversation(title, { answerMode, mentions })).id;
  await onCreated?.(id);
  const { messageId } = await sendMessage(id, text, 'followup', {
    language: currentReplyLanguage(),
    answerMode,
    attachments: attachments.map((item) => item.id),
    mentions,
  });
  return { id, firstMessage: { text, attachments: chipsOf(attachments), mentions, messageId } };
}
