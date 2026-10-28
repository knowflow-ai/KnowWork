/**
 * 会话里每轮回答的赞、踩：进会话拉一次，评了、撤回后重新拉。失败给一句提示，按钮保持原样。
 */
import { useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteFeedback,
  listFeedback,
  putFeedback,
  type FeedbackInput,
  type RunFeedback,
} from '../../../api/feedback';
import { readableError } from '../../../api/http';
import { toast } from '../../../ui/toastStore';

export interface FeedbackControls {
  readonly byRun: Readonly<Record<string, RunFeedback>>;
  /** 返回是否成功（失败已提示）。 */
  readonly submit: (runId: string, input: FeedbackInput) => Promise<boolean>;
  readonly retract: (runId: string) => Promise<boolean>;
}

export function useFeedback(conversationId: string | undefined): FeedbackControls {
  const client = useQueryClient();
  const key = useMemo(() => ['conversations', conversationId, 'feedback'] as const, [conversationId]);
  const list = useQuery({
    queryKey: key,
    queryFn: () => listFeedback(conversationId ?? ''),
    enabled: Boolean(conversationId),
  });
  const byRun = useMemo(
    () => Object.fromEntries((list.data ?? []).map((item) => [item.runId, item])),
    [list.data],
  );
  const run = useCallback(
    async (action: () => Promise<unknown>, success: string, failure: string) => {
      try {
        await action();
        await client.invalidateQueries({ queryKey: key });
        toast.success(success);
        return true;
      } catch (error) {
        toast.error(readableError(error, failure));
        return false;
      }
    },
    [client, key],
  );
  const submit = useCallback(
    (runId: string, input: FeedbackInput) =>
      conversationId
        ? run(() => putFeedback(conversationId, runId, input), '感谢反馈', '反馈没有提交成功')
        : Promise.resolve(false),
    [conversationId, run],
  );
  const retract = useCallback(
    (runId: string) =>
      conversationId
        ? run(() => deleteFeedback(conversationId, runId), '已撤回反馈', '没有撤回成功')
        : Promise.resolve(false),
    [conversationId, run],
  );
  return { byRun, submit, retract };
}
