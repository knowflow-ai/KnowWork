/**
 * 会话引用目录的拉取时机：进入会话时一次；此后每多一次知识检索、阅读返回，或每多一轮正常完成的回答，再拉一次。
 * 引用目录在工具返回时就写好了，工具一返回就拉，回答里的图（[IMG:n:j]）边输出边显示，不用等整轮答完。
 * 拉取失败不打扰用户，保留已有的出处；没拉到之前回答里的编号照常显示，不判断有效与否。
 */
import { useEffect, type Dispatch } from 'react';
import { listCitations } from '../../../api/citations';
import type { ChatAction, ChatState } from '../events/types';
import { ARTIFACT_REFRESH_DELAY_MS } from './useArtifacts';

const KNOWLEDGE_TOOLS = new Set(['knowledge_search', 'knowledge_read']);

/** 知识检索、阅读一共返回了几次 */
export function knowledgeResults(state: ChatState): number {
  return state.runOrder.reduce(
    (sum, runId) =>
      sum +
      (state.runs[runId]?.steps.filter(
        (step) => step.kind === 'tool' && KNOWLEDGE_TOOLS.has(step.name) && step.status === 'done',
      ).length ?? 0),
    0,
  );
}

/** revision 变了就重新拉（由完成的轮数和知识工具返回次数拼成） */
export function useCitations(
  conversationId: string | undefined,
  revision: string,
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
  }, [conversationId, revision, dispatch]);
}
