/**
 * 会话引用目录的拉取时机：同产物（useArtifacts），进入会话时一次，此后每多一轮正常完成的回答再拉一次。
 * 拉取失败不打扰用户，保留已有的出处；没拉到之前回答里的编号照常显示，不判断有效与否。
 */
import { useEffect, type Dispatch } from 'react';
import { listCitations } from '../../../api/citations';
import type { ChatAction } from '../events/types';
import { ARTIFACT_REFRESH_DELAY_MS } from './useArtifacts';

export function useCitations(
  conversationId: string | undefined,
  completedRuns: number,
  dispatch: Dispatch<ChatAction>,
): void {
  useEffect(() => {
    if (!conversationId) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listCitations(conversationId, controller.signal)
        .then((citations) => dispatch({ type: 'citations/loaded', citations }))
        .catch(() => undefined);
    }, ARTIFACT_REFRESH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [conversationId, completedRuns, dispatch]);
}
