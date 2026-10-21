/** 从界面状态推导显示用的值，均为纯函数。 */
import { stepLabel } from './stepLabels';
import type {
  ApprovalView,
  AskedMessage,
  ChatState,
  QuestionView,
  RunState,
  RunView,
  SentMessage,
  TimelineEntry,
} from './types';

export function selectRunState(state: ChatState): RunState {
  if (state.approvals.some((a) => a.status === 'pending')) return 'waiting-approval';
  if (state.openRun !== undefined) return 'running';
  return 'idle';
}

export function selectPendingApprovals(state: ChatState): readonly ApprovalView[] {
  return state.approvals.filter((a) => a.status === 'pending');
}

export function selectRunApprovals(state: ChatState, runId: string): readonly ApprovalView[] {
  return state.approvals.filter((a) => a.runId === runId);
}

/** 这一轮提的问题（迟到回答落在后面的轮次，卡片仍留在提问的这一轮）。 */
export function selectRunQuestions(state: ChatState, runId: string): readonly QuestionView[] {
  return state.questions.filter((q) => q.runId === runId);
}

/** 用户消息与轮次按发生顺序交错：每条消息排在发送时已有的那些轮次之后。 */
/** 事件流里的提问（历史）按文本记数：本页刚发出的同一条消息到了就合并掉，不显示两遍。 */
function unechoedSent(state: ChatState): readonly SentMessage[] {
  const pending = new Map<string, number>();
  for (const runId of state.runOrder) {
    for (const m of state.runs[runId]?.asked ?? []) pending.set(m.text, (pending.get(m.text) ?? 0) + 1);
  }
  return state.sent.filter((m) => {
    const left = pending.get(m.text) ?? 0;
    if (left === 0) return true;
    pending.set(m.text, left - 1);
    return false;
  });
}

function askedEntry(index: number, asked: AskedMessage): TimelineEntry {
  const { id, text, attachments } = asked;
  return {
    type: 'user',
    message: { localId: id, text, mode: 'followup', afterRuns: index, status: 'sent', attachments },
  };
}

export function selectTimeline(state: ChatState): readonly TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const placed = new Set<string>();
  const local = unechoedSent(state);
  const usersBefore = (count: number) =>
    local
      .filter((m) => m.afterRuns <= count && !placed.has(m.localId))
      .map((message) => {
        placed.add(message.localId);
        return { type: 'user' as const, message };
      });
  state.runOrder.forEach((runId, index) => {
    const asked = (state.runs[runId]?.asked ?? []).map((m) => askedEntry(index, m));
    entries.push(...usersBefore(index), ...asked, { type: 'run', runId });
  });
  entries.push(...usersBefore(Number.POSITIVE_INFINITY));
  return entries;
}

/** 最近一条已发出的用户消息，「重新生成」用：本页还没回显的优先，其次是事件流里最近的提问。 */
export function selectLastUserText(state: ChatState): string | undefined {
  const local = [...unechoedSent(state)].reverse().find((m) => m.status === 'sent');
  if (local) return local.text;
  for (const runId of [...state.runOrder].reverse()) {
    const asked = state.runs[runId]?.asked ?? [];
    if (asked.length > 0) return asked[asked.length - 1]?.text;
  }
  return undefined;
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
}

/** 执行过程的一行摘要，例如「已完成 4 个步骤 · 用时 38 秒」。 */
export function summarizeProcess(run: RunView): string {
  const count = run.steps.length;
  const running = run.endedAt === undefined;
  const current = [...run.steps]
    .reverse()
    .find((s) => s.status === 'running' || s.status === 'awaiting-approval');
  if (running) {
    if (current?.status === 'awaiting-approval') return `等待批准：${current.name}`;
    // 审批之外让一轮停下来等人的只有提问（提问不算步骤，见 INTERNAL_TOOLS）
    if (run.status === 'waiting') return '等待你回答';
    if (!current) return '思考中';
    const label = stepLabel(current);
    return label === current.name ? `正在执行：${label}` : `正在${label}`;
  }
  const elapsed =
    run.startedAt !== undefined && run.endedAt !== undefined ? run.endedAt - run.startedAt : undefined;
  const head = count > 0 ? `已完成 ${count} 个步骤` : '已完成';
  const timeText = elapsed !== undefined && elapsed > 0 ? ` · 用时 ${formatDuration(elapsed)}` : '';
  return `${head}${timeText}`;
}

/** 这一轮是否有可展开的执行细节。模型思考原文不展示，不算细节。 */
export function hasDetails(run: RunView): boolean {
  return run.steps.length > 0 || run.notes.length > 0 || run.problems.length > 0;
}

/** 这一轮是否需要显示执行过程那一行。 */
export function hasProcess(run: RunView): boolean {
  return hasDetails(run) || run.endedAt === undefined;
}
