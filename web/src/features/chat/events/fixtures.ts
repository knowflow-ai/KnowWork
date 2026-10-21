/** 测试用的帧构造器，按 events_v1.py 的投影结果造数据。 */
import type { WireEvent } from './types';
import { parseCursor } from './parse';

let clock = 1_700_000_000_000;

export function resetClock(): void {
  clock = 1_700_000_000_000;
}

interface Spec {
  readonly id: string;
  readonly type: string;
  readonly run?: number | null;
  readonly item?: string;
  readonly kind?: string;
  readonly data?: Record<string, unknown>;
  readonly time?: number;
}

export function wire({ id, type, run = 1, item, kind, data = {}, time }: Spec): WireEvent {
  const cursor = parseCursor(id);
  if (!cursor) throw new Error(`bad id ${id}`);
  clock += 1000;
  return {
    id,
    cursor,
    event: {
      type,
      seq: cursor.seq,
      runId: run === null ? null : `run-${run}`,
      time: time ?? clock,
      itemId: item ?? null,
      kind: kind ?? null,
      data,
    },
  };
}

/** 与服务端一致的原始 JSON（SSE data 字段）。 */
export function rawJson(event: WireEvent): string {
  const e = event.event;
  return JSON.stringify({
    schema_version: 'events.v1',
    type: e.type,
    seq: e.seq,
    run_id: e.runId,
    time: e.time,
    item_id: e.itemId,
    kind: e.kind,
    data: e.data,
  });
}

const s = (n: number | string) => String(n);

/** 与 events_v1.py 投影一致的一道单选题。 */
export const FORMAT_QUESTION: readonly Record<string, unknown>[] = [
  {
    id: 'fmt',
    question: '导出成什么格式？',
    header: '格式',
    detail: null,
    multi_select: false,
    options: [{ label: 'Excel (Recommended)', description: '方便继续编辑' }, { label: 'PDF' }],
  },
];

export const ev = {
  runStarted: (id: number | string, run = 1, time?: number) =>
    wire({ id: s(id), type: 'run.started', run, time }),
  runCompleted: (id: number | string, run = 1, time?: number, data: Record<string, unknown> = {}) =>
    wire({ id: s(id), type: 'run.completed', run, time, data }),
  runFailed: (id: number | string, run = 1, code = 'UNKNOWN') =>
    wire({ id: s(id), type: 'run.failed', run, data: { code, message: '本轮执行失败' } }),
  runCancelled: (id: number | string, run = 1) => wire({ id: s(id), type: 'run.cancelled', run }),
  runWaiting: (id: number | string, approvalId: string, run = 1) =>
    wire({ id: s(id), type: 'run.waiting', run, item: approvalId, data: { reason: 'approval' } }),
  message: (id: number | string, text: string, run = 1, item = `m${id}`) =>
    wire({
      id: s(id),
      type: 'item.completed',
      run,
      item,
      kind: 'message',
      data: { role: 'assistant', text },
    }),
  asked: (
    id: number | string,
    text: string,
    run = 1,
    item = `u${id}`,
    attachments?: readonly { name: string; size: number }[],
  ) =>
    wire({
      id: s(id),
      type: 'item.completed',
      run,
      item,
      kind: 'message',
      data: attachments ? { role: 'user', text, attachments } : { role: 'user', text },
    }),
  thinking: (id: number | string, text: string, run = 1) =>
    wire({
      id: s(id),
      type: 'item.completed',
      run,
      item: `m${id}:thinking`,
      kind: 'thinking',
      data: { text },
    }),
  toolStarted: (
    id: number | string,
    callId: string,
    name: string,
    args: unknown = '{"path":"a.txt"}',
    run = 1,
  ) =>
    wire({
      id: s(id),
      type: 'item.started',
      run,
      item: callId,
      kind: 'tool',
      data: { name, arguments: args },
    }),
  toolCompleted: (id: number | string, callId: string, text: string, run = 1) =>
    wire({ id: s(id), type: 'item.completed', run, item: callId, kind: 'tool', data: { text } }),
  toolFailed: (id: number | string, callId: string, text: string, run = 1) =>
    wire({ id: s(id), type: 'item.failed', run, item: callId, kind: 'tool', data: { text, code: 'E_X' } }),
  approvalRequested: (
    id: number | string,
    approvalId: string,
    toolName: string,
    callId?: string,
    run: number | null = 1,
  ) =>
    wire({
      id: s(id),
      type: 'item.started',
      run,
      item: approvalId,
      kind: 'approval',
      data: { tool_name: toolName, call_id: callId ?? null, reason: '需要写入文件' },
    }),
  approvalResolved: (id: number | string, approvalId: string, outcome: string, run: number | null = 1) =>
    wire({ id: s(id), type: 'item.completed', run, item: approvalId, kind: 'approval', data: { outcome } }),
  subagent: (id: number | string, childId: string, done = false, status = 'completed', run = 1) =>
    wire({
      id: s(id),
      type: done ? 'item.completed' : 'item.started',
      run,
      item: childId,
      kind: 'subagent',
      data: done ? { status } : {},
    }),
  problem: (id: number | string, code: string, run = 1) =>
    wire({ id: s(id), type: 'item.failed', run, item: `err-${id}`, kind: 'error', data: { code } }),
  questionRequested: (
    id: number | string,
    questionId: string,
    questions: readonly Record<string, unknown>[] = FORMAT_QUESTION,
    run: number | null = 1,
  ) =>
    wire({
      id: s(id),
      type: 'item.started',
      run,
      item: questionId,
      kind: 'input_request',
      data: { questions, timed: true },
    }),
  runWaitingInput: (id: number | string, questionId: string, run = 1) =>
    wire({ id: s(id), type: 'run.waiting', run, item: questionId, data: { reason: 'input' } }),
  questionAnswered: (
    id: number | string,
    questionId: string,
    answers: readonly Record<string, unknown>[],
    run = 1,
    late = false,
  ) =>
    wire({
      id: s(id),
      type: 'item.completed',
      run,
      item: questionId,
      kind: 'input_request',
      data: { answers, late },
    }),
  questionContinued: (id: number | string, questionId: string, run = 1) =>
    wire({
      id: s(id),
      type: 'item.updated',
      run,
      item: questionId,
      kind: 'input_request',
      data: { state: 'continued' },
    }),
  questionClosed: (id: number | string, questionId: string, reason: string, run = 1) =>
    wire({ id: s(id), type: 'item.failed', run, item: questionId, kind: 'input_request', data: { reason } }),
  todos: (id: number | string, items: readonly Record<string, unknown>[], run = 1) =>
    wire({ id: s(id), type: 'item.updated', run, item: `todo-${run}`, kind: 'todo', data: { items } }),
};
