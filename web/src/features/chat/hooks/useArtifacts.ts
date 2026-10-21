/**
 * 会话产物的拉取时机：进入会话时一次，此后每多一轮正常完成的回答再拉一次。
 *
 * 产物在回合结束时由后端收取，不走事件流；列表接口会等这个会话在收的产物落库后再返回。
 * 进入会话时事件流会重放历史，完成轮数短时间内连涨，用一个短延迟把这一串合成一次请求。
 * 拉取失败不打扰用户（不是用户发起的操作），保留已经显示的产物，下一轮完成时再拉。
 */
import { useEffect, type Dispatch } from 'react';
import { listArtifacts } from '../../../api/artifacts';
import type { ChatAction, ChatState } from '../events/types';

export const ARTIFACT_REFRESH_DELAY_MS = 300;

export function completedRunCount(state: ChatState): number {
  return state.runOrder.filter((runId) => state.runs[runId]?.status === 'completed').length;
}

export function useArtifacts(
  conversationId: string | undefined,
  completedRuns: number,
  dispatch: Dispatch<ChatAction>,
): void {
  useEffect(() => {
    if (!conversationId) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listArtifacts(conversationId, controller.signal)
        .then((artifacts) => dispatch({ type: 'artifacts/loaded', artifacts }))
        .catch(() => undefined);
    }, ARTIFACT_REFRESH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [conversationId, completedRuns, dispatch]);
}
