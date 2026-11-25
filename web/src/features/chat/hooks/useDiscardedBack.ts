/**
 * 一轮还在跑时发的消息排在收件箱里，用户点停止时官方连它一起取消了（没进对话，events.v1 的 item.failed，见 reducer
 * 的 onDiscarded）：原文放回输入框（同编辑器「AI 修改」交话的那条路，editRequest.ts），提示一句，改了再发或直接重发。
 * 每条只放一次；页面重开后本页没有这条（它从没进对话），也就不放。
 */
import { useEffect, useRef } from 'react';
import { toast } from '../../../ui/toastStore';
import { useComposerInbox } from '../editRequest';
import { selectDiscardedSent } from '../events/selectors';
import type { ChatState } from '../events/types';

export function useDiscardedBack(conversationId: string, state: ChatState): void {
  const offer = useComposerInbox((inbox) => inbox.offer);
  const handled = useRef(new Set<string>());
  const discarded = selectDiscardedSent(state);
  const key = discarded.map((m) => m.localId).join('\n');
  useEffect(() => {
    const fresh = discarded.filter((m) => !handled.current.has(m.localId));
    if (fresh.length === 0) return;
    fresh.forEach((m) => handled.current.add(m.localId));
    offer(conversationId, fresh.map((m) => m.text).join('\n\n'), false);
    const files = fresh.some((m) => (m.attachments?.length ?? 0) > 0) ? '；附件要重新添加' : '';
    toast.info(`停止时这条消息还在排队，没有发出去，已放回输入框${files}`);
    // 只在被取消的那几条变了时处理；discarded 每次渲染都是新数组
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, conversationId, offer]);
}
