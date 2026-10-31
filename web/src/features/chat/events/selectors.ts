/** 从界面状态推导显示用的值，均为纯函数。 */
import type { Mention } from '../../../api/mentions';
import { INTERNAL_TOOLS } from './runs';
import { stepLabel, toolLabel } from './stepLabels';
import type {
  ApprovalView,
  AskedMessage,
  ChatState,
  QuestionView,
  RunState,
  RunView,
  SearchSummary,
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

/** 本页刚发出、事件流里还没回显的消息：回显到了就合并掉，不显示两遍。
 * 发送接口返回了消息 id 的按 id 认：回显的原话去掉了首尾空白、超长的只留前面一段，按原话认会认不出来。
 * 还没拿到 id 的（请求还在路上，回显可能先到）按去掉首尾空白的原话记数认，不算已被别的消息按 id 认走的。 */
function unechoedSent(state: ChatState): readonly SentMessage[] {
  const asked = state.runOrder.flatMap((runId) => state.runs[runId]?.asked ?? []);
  const echoed = new Set(asked.map((m) => m.id));
  const claimed = new Set(state.sent.flatMap((m) => (m.messageId ? [m.messageId] : [])));
  const pending = new Map<string, number>();
  for (const m of asked) {
    if (!claimed.has(m.id)) pending.set(m.text, (pending.get(m.text) ?? 0) + 1);
  }
  return state.sent.filter((m) => {
    if (m.messageId) return !echoed.has(m.messageId);
    const text = m.text.trim();
    const left = pending.get(text) ?? 0;
    if (left === 0) return true;
    pending.set(text, left - 1);
    return false;
  });
}

function askedEntry(index: number, asked: AskedMessage): TimelineEntry {
  const { id, text, attachments, mentions } = asked;
  return {
    type: 'user',
    message: { localId: id, text, mode: 'followup', afterRuns: index, status: 'sent', attachments, mentions },
  };
}

/** 用户消息与轮次按发生顺序交错：每条消息排在发送时已有的那些轮次之后。 */
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

/** 最近一条已发出的用户消息，「重新生成」用：本页还没回显的优先，其次是事件流里最近的提问。
 * 连同那一问 @ 的东西一起给，重新回答时照原样再带上，不然就悄悄放宽成全部知识库了。 */
export function selectLastUserMessage(
  state: ChatState,
): { readonly text: string; readonly mentions: readonly Mention[] } | undefined {
  const local = [...unechoedSent(state)].reverse().find((m) => m.status === 'sent');
  if (local) return { text: local.text, mentions: local.mentions ?? [] };
  for (const runId of [...state.runOrder].reverse()) {
    const asked = state.runs[runId]?.asked ?? [];
    const last = asked[asked.length - 1];
    if (last) return { text: last.text, mentions: last.mentions ?? [] };
  }
  return undefined;
}

export function selectLastUserText(state: ChatState): string | undefined {
  return selectLastUserMessage(state)?.text;
}

/** 这一轮检索用上的条件、撤掉的条件与命中的文档数，回答上方列出来。没用条件也没撤条件时不显示，返回 undefined。 */
export interface RunSearch {
  readonly conditions: readonly string[];
  readonly removed: readonly string[];
  readonly documents: number;
}

export function selectRunSearch(run: RunView): RunSearch | undefined {
  const searches = run.steps.flatMap((s) => (s.search ? [s.search] : []));
  const unique = (pick: (s: SearchSummary) => readonly string[]) => [...new Set(searches.flatMap(pick))];
  const removed = unique((s) => s.removed);
  const conditions = unique((s) => s.conditions).filter((c) => !removed.includes(c));
  if (conditions.length === 0 && removed.length === 0) return undefined;
  return { conditions, removed, documents: unique((s) => s.documents).length };
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
}

/** 执行过程的一行摘要，例如「已完成 4 个步骤 · 用时 38 秒」。preparing 是模型正在写的工具调用（还没开始跑）。 */
export function summarizeProcess(run: RunView, preparing: readonly string[] = []): string {
  const count = run.steps.length;
  const running = run.endedAt === undefined;
  const current = [...run.steps]
    .reverse()
    .find((s) => s.status === 'running' || s.status === 'awaiting-approval');
  if (running) {
    if (current?.status === 'awaiting-approval') return `等待批准：${current.name}`;
    // 审批之外让一轮停下来等人的只有提问（提问不算步骤，见 INTERNAL_TOOLS）
    if (run.status === 'waiting') return '等待你回答';
    // 模型在写工具的参数（例如把整个网页写进 write）：说它在做什么，不是一直「思考中」；
    // 待办、提问这类内部工具不算步骤、没有中文名，不把工具名露出来
    const name = current?.name ?? preparing.filter((tool) => !INTERNAL_TOOLS.has(tool)).at(-1);
    if (name === undefined) return '思考中';
    const label = current ? stepLabel(current) : toolLabel(name);
    return label === name ? `正在执行：${label}` : `正在${label}`;
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
