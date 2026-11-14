/**
 * 官方 present 交付的文件（events.v1 的 deliverable 条目，服务端从 deliverables/presented 投影）挂到那一轮上：
 * 同一路径再交付用最近一次的说明、位置不变（同官方交付卡片）。网页与桌面同一套，按路径经对话的来源读（方案 7.7.3）。
 */
import { isRecord, listOf, text } from '../../../api/parse';
import { updateRun, withRun } from './runs';
import type { ChatState, DeliverableView, V1Event } from './types';

function parseDeliverable(value: unknown): DeliverableView | undefined {
  if (!isRecord(value)) return undefined;
  const path = text(value.path);
  if (!path) return undefined;
  const description = text(value.description);
  return description ? { path, description } : { path };
}

function merged(
  current: readonly DeliverableView[],
  incoming: readonly DeliverableView[],
): readonly DeliverableView[] {
  return incoming.reduce<readonly DeliverableView[]>((list, file) => {
    const index = list.findIndex((item) => item.path === file.path);
    return index < 0 ? [...list, file] : list.map((item, at) => (at === index ? file : item));
  }, current);
}

export function onDeliverables(state: ChatState, event: V1Event, runId: string): ChatState {
  const files = listOf(event.data.files, parseDeliverable);
  if (files.length === 0) return state;
  return updateRun(withRun(state, runId, event.time), runId, (run) => ({
    ...run,
    deliverables: merged(run.deliverables ?? [], files),
  }));
}
