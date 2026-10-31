/**
 * events.v1 的轮次事件与条目事件折叠进界面状态。全部是纯函数，只返回新对象。
 */
import type { AttachmentChip } from '../../../api/attachments';
import type { Mention, MentionType } from '../../../api/mentions';
import type { ApprovalStatus, ChatState, EndReason, RunView, StepView, V1Event } from './types';
import { argumentsText, preview, searchSummary, str } from './parse';

/** 不作为步骤显示的工具：提问由卡片展示，待办由清单展示，放进执行过程只会凑步骤数。 */
export const INTERNAL_TOOLS: ReadonlySet<string> = new Set(['ask_user_question', 'todo_write']);

const FAILED_MESSAGES: Readonly<Record<string, string>> = {
  blocked: '这一轮被拦截，没有继续执行',
  skill_gate_failed: '没有完成技能要求的收尾步骤',
};

const PROBLEM_MESSAGES: Readonly<Record<string, string>> = {
  events_gap: '部分执行过程没有记录下来',
  skill_gate_failed: '技能要求的收尾步骤没有完成',
};

export function endReasonOf(event: V1Event): EndReason {
  switch (event.type) {
    case 'run.cancelled':
      return { kind: 'cancelled', message: '已停止' };
    case 'run.failed': {
      const code = str(event.data.code) ?? '';
      return { kind: 'failed', message: FAILED_MESSAGES[code] ?? '这一轮没有完成，请稍后重试' };
    }
    default:
      return event.data.reason === 'max_tokens'
        ? { kind: 'max-tokens', message: '回答过长，已被截断' }
        : { kind: 'completed' };
  }
}

function emptyRun(runId: string, startedAt?: number): RunView {
  return {
    runId,
    status: 'running',
    startedAt,
    steps: [],
    notes: [],
    thinking: [],
    answer: '',
    problems: [],
    asked: [],
    trail: [],
  };
}

/** 取轮次（不存在就新建并排到最后）。 */
export function withRun(state: ChatState, runId: string, time?: number): ChatState {
  if (state.runs[runId]) return state;
  return {
    ...state,
    runs: { ...state.runs, [runId]: emptyRun(runId, time) },
    runOrder: [...state.runOrder, runId],
  };
}

export function updateRun(state: ChatState, runId: string, update: (r: RunView) => RunView): ChatState {
  const current = state.runs[runId];
  return current ? { ...state, runs: { ...state.runs, [runId]: update(current) } } : state;
}

/** 条目所属的轮次：事件自带的优先，其次是正在进行的那一轮，最后是最近一轮。 */
export function runFor(state: ChatState, event: V1Event): string | undefined {
  return event.runId ?? state.openRun ?? state.runOrder[state.runOrder.length - 1];
}

export function onRunStarted(state: ChatState, event: V1Event): ChatState {
  if (!event.runId) return state;
  const runId = event.runId;
  const next = updateRun(withRun(state, runId, event.time), runId, (r) => ({
    ...r,
    status: 'running',
    startedAt: r.startedAt ?? event.time,
  }));
  return { ...next, openRun: runId };
}

export function onRunWaiting(state: ChatState, event: V1Event): ChatState {
  const runId = runFor(state, event);
  if (!runId) return state;
  return updateRun(withRun(state, runId, event.time), runId, (r) =>
    r.endedAt === undefined ? { ...r, status: 'waiting' } : r,
  );
}

function settleStep(step: StepView, time: number, reason: string): StepView {
  return step.status === 'running' || step.status === 'awaiting-approval'
    ? { ...step, status: 'error', endedAt: time, errorReason: reason }
    : step;
}

const STATUS_BY_END: Readonly<Record<EndReason['kind'], RunView['status']>> = {
  completed: 'completed',
  'max-tokens': 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};

export function onRunEnded(state: ChatState, event: V1Event): ChatState {
  const runId = runFor(state, event);
  if (!runId) return state;
  const endReason = endReasonOf(event);
  const reason = endReason.message ?? '没有完成';
  const ended = updateRun(withRun(state, runId, event.time), runId, (r) => ({
    ...r,
    status: STATUS_BY_END[endReason.kind],
    endedAt: event.time,
    endReason,
    // 轮次已经结束，还挂着的步骤不会再有结果了
    steps: r.steps.map((s) => settleStep(s, event.time, reason)),
  }));
  // 已结束轮次里还在等的审批不再有效
  const approvals = ended.approvals.map((a) =>
    a.runId === runId && a.status === 'pending' ? { ...a, status: 'cancelled' as const } : a,
  );
  return { ...ended, approvals, openRun: ended.openRun === runId ? undefined : ended.openRun };
}

export function upsertStep(run: RunView, callId: string, patch: (s?: StepView) => StepView): RunView {
  const index = run.steps.findIndex((s) => s.callId === callId);
  if (index < 0) {
    return {
      ...run,
      steps: [...run.steps, patch(undefined)],
      trail: [...run.trail, { type: 'step', callId }],
    };
  }
  return { ...run, steps: run.steps.map((s, i) => (i === index ? patch(s) : s)) };
}

/** 在所有轮次里找到某个工具调用并更新它；审批不一定带轮次。 */
export function updateStepAnywhere(
  state: ChatState,
  callId: string,
  patch: (s: StepView) => StepView,
): ChatState {
  const run = Object.values(state.runs).find((r) => r.steps.some((s) => s.callId === callId));
  if (!run) return state;
  return updateRun(state, run.runId, (r) => upsertStep(r, callId, (s) => patch(s as StepView)));
}

function initialStepStatus(state: ChatState, callId: string): StepView['status'] {
  return state.approvals.some((a) => a.callId === callId && a.status === 'pending')
    ? 'awaiting-approval'
    : 'running';
}

/** 工具调用之前的那段输出是「说明」，不是最终回答。 */
function answerToNote(r: RunView): RunView {
  return r.answer
    ? {
        ...r,
        notes: [...r.notes, r.answer],
        trail: [...r.trail, { type: 'note' as const, text: r.answer }],
        answer: '',
      }
    : r;
}

export function onToolStarted(state: ChatState, event: V1Event, runId: string, callId: string): ChatState {
  const name = str(event.data.name) || '工具';
  const args = argumentsText(event.data.arguments);
  if (INTERNAL_TOOLS.has(name)) {
    const withNote = updateRun(withRun(state, runId, event.time), runId, answerToNote);
    return withNote.internalCalls.includes(callId)
      ? withNote
      : { ...withNote, internalCalls: [...withNote.internalCalls, callId] };
  }
  return updateRun(withRun(state, runId, event.time), runId, (r) => {
    const moved = answerToNote(r);
    return upsertStep(moved, callId, (s) =>
      s
        ? { ...s, name, arguments: args || s.arguments }
        : {
            callId,
            kind: 'tool',
            name,
            arguments: args,
            status: initialStepStatus(state, callId),
            startedAt: event.time,
          },
    );
  });
}

const MAX_REASON = 60;

/** 失败原因取工具返回内容的第一行（MCP 服务、业务工具都会写明原因），没有就给通用说明。 */
function failureReason(text: string): string {
  const line = text.trim().split('\n')[0]?.trim() ?? '';
  if (!line) return '工具执行没有成功';
  return line.length > MAX_REASON ? `${line.slice(0, MAX_REASON)}…` : line;
}

export function onToolEnded(state: ChatState, event: V1Event, runId: string, callId: string): ChatState {
  if (state.internalCalls.includes(callId)) return state;
  const failed = event.type === 'item.failed';
  const resultPreview = preview(str(event.data.text) ?? '');
  const search = failed ? undefined : searchSummary(event.data.search);
  return updateRun(withRun(state, runId, event.time), runId, (r) =>
    upsertStep(r, callId, (s) => {
      const base: StepView = s ?? { callId, kind: 'tool', name: '工具', arguments: '', status: 'running' };
      if (base.status === 'rejected') return { ...base, endedAt: event.time };
      return failed
        ? {
            ...base,
            status: 'error',
            endedAt: event.time,
            resultPreview,
            errorReason: failureReason(resultPreview),
          }
        : { ...base, status: 'done', endedAt: event.time, resultPreview, ...(search ? { search } : {}) };
    }),
  );
}

export function onSubagent(state: ChatState, event: V1Event, runId: string, childId: string): ChatState {
  const done = event.type !== 'item.started';
  return updateRun(withRun(state, runId, event.time), runId, (r) =>
    upsertStep(r, childId, (s) => {
      const base: StepView = s ?? {
        callId: childId,
        kind: 'subagent',
        name: '子任务',
        arguments: '',
        status: 'running',
        startedAt: event.time,
      };
      if (!done) return base;
      const failed = event.type === 'item.failed' || /fail|error/i.test(str(event.data.status) ?? '');
      return { ...base, status: failed ? 'error' : 'done', endedAt: event.time };
    }),
  );
}

/** 用户消息带的文件：服务端把宿主的附件说明换成了名字与大小（不含沙箱路径）。 */
function attachmentsOf(value: unknown): readonly AttachmentChip[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown) => {
    if (typeof item !== 'object' || item === null) return [];
    const { name, size } = item as { name?: unknown; size?: unknown };
    return typeof name === 'string' && name ? [{ name, size: typeof size === 'number' ? size : 0 }] : [];
  });
}

const MENTION_TYPES: readonly MentionType[] = ['knowledge_base', 'folder', 'document', 'tag', 'mcp', 'skill'];

/** 用户消息 @ 的东西：服务端从宿主写的说明块里取出来的类型、id、名字与所在知识库。 */
function mentionsOf(value: unknown): readonly Mention[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown): Mention[] => {
    if (typeof item !== 'object' || item === null) return [];
    const { type, id, name, base, base_name: baseName } = item as Record<string, unknown>;
    if (!MENTION_TYPES.includes(type as MentionType) || typeof id !== 'string' || typeof name !== 'string')
      return [];
    return [
      {
        type: type as MentionType,
        id,
        name,
        ...(typeof base === 'string'
          ? { base, baseName: typeof baseName === 'string' ? baseName : base }
          : {}),
      },
    ];
  });
}

export function onMessage(state: ChatState, event: V1Event, runId: string): ChatState {
  const role = str(event.data.role);
  const value = (str(event.data.text) ?? '').trim();
  if (role === 'user') {
    const attachments = attachmentsOf(event.data.attachments);
    const mentions = mentionsOf(event.data.mentions);
    return value || attachments.length > 0
      ? onAsked(state, event, runId, value, attachments, mentions)
      : state;
  }
  if (!value) return state;
  if (role !== undefined && role !== 'assistant') return state;
  return updateRun(withRun(state, runId, event.time), runId, (r) => ({
    ...r,
    answer: r.answer ? `${r.answer}\n\n${value}` : value,
  }));
}

function onAsked(
  state: ChatState,
  event: V1Event,
  runId: string,
  text: string,
  attachments: readonly AttachmentChip[],
  mentions: readonly Mention[],
): ChatState {
  const id = event.itemId ?? `asked-${runId}-${text}`;
  const message = {
    id,
    text,
    ...(attachments.length > 0 ? { attachments } : {}),
    ...(mentions.length > 0 ? { mentions } : {}),
  };
  return updateRun(withRun(state, runId, event.time), runId, (r) =>
    r.asked.some((m) => m.id === id) ? r : { ...r, asked: [...r.asked, message] },
  );
}

export function onThinking(state: ChatState, event: V1Event, runId: string): ChatState {
  const value = (str(event.data.text) ?? '').trim();
  if (!value) return state;
  return updateRun(withRun(state, runId, event.time), runId, (r) => ({
    ...r,
    thinking: [...r.thinking, value],
    trail: [...r.trail, { type: 'thinking', text: value }],
  }));
}

export function onProblem(state: ChatState, event: V1Event, runId: string): ChatState {
  const message = PROBLEM_MESSAGES[str(event.data.code) ?? ''] ?? '执行中出现了问题';
  return updateRun(withRun(state, runId, event.time), runId, (r) =>
    r.problems.includes(message) ? r : { ...r, problems: [...r.problems, message] },
  );
}

export function approvalStatus(outcome: string | undefined): ApprovalStatus {
  if (outcome?.startsWith('allow')) return 'allowed';
  if (outcome === 'cancelled' || outcome === 'timeout') return 'cancelled';
  return 'rejected';
}

export function onApprovalRequested(state: ChatState, event: V1Event, approvalId: string): ChatState {
  if (state.approvals.some((a) => a.id === approvalId)) return state;
  const callId = str(event.data.call_id) || undefined;
  const approval = {
    id: approvalId,
    toolName: str(event.data.tool_name) || '工具',
    callId,
    reason: str(event.data.reason) || undefined,
    runId: event.runId ?? state.openRun,
    status: 'pending' as const,
  };
  const next = { ...state, approvals: [...state.approvals, approval] };
  return callId ? updateStepAnywhere(next, callId, (s) => ({ ...s, status: 'awaiting-approval' })) : next;
}

export function onApprovalResolved(state: ChatState, event: V1Event, approvalId: string): ChatState {
  const target = state.approvals.find((a) => a.id === approvalId);
  if (!target || target.status !== 'pending') return state;
  const status = approvalStatus(str(event.data.outcome));
  const approvals = state.approvals.map((a) => (a.id === approvalId ? { ...a, status } : a));
  const withStatus: ChatState = target.runId
    ? updateRun({ ...state, approvals }, target.runId, (r) =>
        r.status === 'waiting' ? { ...r, status: 'running' } : r,
      )
    : { ...state, approvals };
  if (!target.callId) return withStatus;
  return updateStepAnywhere(withStatus, target.callId, (s) =>
    s.status === 'awaiting-approval' ? { ...s, status: status === 'allowed' ? 'running' : 'rejected' } : s,
  );
}
