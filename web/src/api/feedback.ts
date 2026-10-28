/**
 * 对一轮回答的赞或踩（server/src/knowwork_server/conversations/feedback.py，照 DSH 官方的消息反馈）：
 * 评分必填，分类与说明可选；再点一次已选的评分就撤回。分类的键与顺序照官方的固定分类表，中文照官方界面的说法。
 */
import { request, seg } from './http';
import { isRecord, listOf, str } from './parse';
import { workspacePath } from './workspace';

export type Rating = 'like' | 'dislike';

export const FEEDBACK_CATEGORIES = [
  { value: 'task-result', label: '任务结果' },
  { value: 'instruction-following', label: '指令理解与遵循' },
  { value: 'product-interaction', label: '产品功能与交互' },
  { value: 'service-stability', label: '稳定性和速度' },
  { value: 'resource-cost', label: '资源使用与费用' },
  { value: 'security-privacy-permission', label: '隐私与权限' },
  { value: 'other', label: '其他' },
] as const;

export type FeedbackCategory = (typeof FEEDBACK_CATEGORIES)[number]['value'];

export const MAX_FEEDBACK_NOTE = 2000;

export interface RunFeedback {
  readonly runId: string;
  readonly rating: Rating;
  readonly category: FeedbackCategory | null;
  readonly note: string | null;
}

export interface FeedbackInput {
  readonly rating: Rating;
  readonly category?: FeedbackCategory | null;
  readonly note?: string;
}

function feedbackOf(value: unknown): RunFeedback | undefined {
  if (!isRecord(value)) return undefined;
  const runId = str(value.runId);
  const rating = value.rating === 'like' || value.rating === 'dislike' ? value.rating : undefined;
  if (!runId || !rating) return undefined;
  const category = FEEDBACK_CATEGORIES.find((c) => c.value === value.category)?.value ?? null;
  return { runId, rating, category, note: str(value.note) ?? null };
}

const path = (conversationId: string, runId?: string) =>
  workspacePath(`/conversations/${seg(conversationId)}/feedback${runId ? `/${seg(runId)}` : ''}`);

export async function listFeedback(conversationId: string): Promise<readonly RunFeedback[]> {
  const body = await request<unknown>(path(conversationId));
  return isRecord(body) ? listOf(body.items, feedbackOf) : [];
}

export function putFeedback(conversationId: string, runId: string, input: FeedbackInput): Promise<unknown> {
  return request(path(conversationId, runId), {
    method: 'PUT',
    body: { rating: input.rating, category: input.category ?? null, note: input.note?.trim() || null },
  });
}

export function deleteFeedback(conversationId: string, runId: string): Promise<unknown> {
  return request(path(conversationId, runId), { method: 'DELETE' });
}
