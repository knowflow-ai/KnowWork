/**
 * 上下文压缩（DSH 官方 dsh-compaction）在对话里的样子：照官方对话界面，一次压缩一行，进行中写「正在压缩」，做完写压掉了
 * 多少、可看摘要，出错写原因。服务端把官方会话日志里的 compaction/start、compaction/summary、compaction/end 投影成同一个
 * item_id（官方 compactionId）的条目（events_v1.py），这里合成一条。
 * 位置：发生在某一轮里的（自动压缩）排在那一轮前面——那一轮的模型从这里起只看摘要；轮次之间的（/compact）排在当时已有的
 * 轮次之后，同命令。
 */
import type { ChatState, CompactionView, V1Event } from './types';

const num = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined;
const str = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

function statusOf(event: V1Event, current?: CompactionView): CompactionView['status'] {
  const status = event.data.status;
  if (status === 'completed' || status === 'error') return status;
  // 摘要写好以后的收尾（没出错的结束）不改结论
  if (status === 'ended') return current?.status === 'completed' ? 'completed' : 'ended';
  return current?.status ?? 'running';
}

function placeOf(state: ChatState, event: V1Event): number {
  const index = event.runId ? state.runOrder.indexOf(event.runId) : -1;
  return index >= 0 ? index : state.runOrder.length;
}

export function onCompaction(state: ChatState, event: V1Event, itemId: string): ChatState {
  const current = state.compactions.find((compaction) => compaction.id === itemId);
  const items = num(event.data.items) ?? current?.items;
  const tokens = num(event.data.tokens) ?? current?.tokens;
  const summary = str(event.data.summary) ?? current?.summary;
  const error = str(event.data.error) ?? current?.error;
  const next: CompactionView = {
    id: itemId,
    status: statusOf(event, current),
    ...(items !== undefined ? { items } : {}),
    ...(tokens !== undefined ? { tokens } : {}),
    ...(summary !== undefined ? { summary } : {}),
    ...(error !== undefined ? { error } : {}),
    time: current?.time ?? event.time,
    afterRuns: current?.afterRuns ?? placeOf(state, event),
  };
  return {
    ...state,
    compactions: current
      ? state.compactions.map((compaction) => (compaction.id === itemId ? next : compaction))
      : [...state.compactions, next],
  };
}

/** 压掉了多少：照官方「已压缩 N 条历史记录（约 T tokens）」，缺哪样就不写哪样 */
export function compactedAmount(compaction: Pick<CompactionView, 'items' | 'tokens'>): string {
  const parts = [
    compaction.items !== undefined ? `${compaction.items} 条记录` : '',
    compaction.tokens !== undefined ? `约 ${compaction.tokens.toLocaleString('zh-CN')} tokens` : '',
  ].filter(Boolean);
  return parts.length > 0 ? `（${parts.join('，')}）` : '';
}
