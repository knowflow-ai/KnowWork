/**
 * 对话事件 reducer：把 events.v1 序列折叠成对话页的界面状态。
 *
 * - 幂等：同一位置的帧重复到达只算一次（断线续传可能重发）。
 * - 乱序：位置早于已应用最后位置的新帧，插回有序日志后整体重算，结果与按序到达一致。
 * - 纯函数：不修改入参，每次返回新对象。
 */
import type { ChatAction, ChatState, Cursor, V1Event, WireEvent } from './types';
import { compareCursor } from './parse';
import {
  onApprovalRequested,
  onApprovalResolved,
  onMessage,
  onProblem,
  onRunEnded,
  onRunStarted,
  onRunWaiting,
  onSubagent,
  onThinking,
  onToolEnded,
  onToolStarted,
  runFor,
} from './runs';
import { onQuestion, onQuestionExpired, onTodos } from './questions';

export const initialChatState: ChatState = {
  log: [],
  runOrder: [],
  runs: {},
  approvals: [],
  questions: [],
  internalCalls: [],
  expiredQuestions: [],
  artifacts: [],
  sent: [],
};

const ITEM_TYPES = new Set(['item.started', 'item.completed', 'item.failed', 'item.updated']);

function applyApproval(state: ChatState, event: V1Event, itemId: string): ChatState {
  return event.type === 'item.started'
    ? onApprovalRequested(state, event, itemId)
    : onApprovalResolved(state, event, itemId);
}

function applyItem(state: ChatState, event: V1Event): ChatState {
  const itemId = event.itemId;
  if (!itemId || !ITEM_TYPES.has(event.type)) return state;
  if (event.kind === 'approval') return applyApproval(state, event, itemId);
  if (event.kind === 'input_request') return onQuestion(state, event, itemId);
  const runId = runFor(state, event);
  if (!runId) return state;
  switch (event.kind) {
    case 'tool':
      return event.type === 'item.started'
        ? onToolStarted(state, event, runId, itemId)
        : onToolEnded(state, event, runId, itemId);
    case 'subagent':
      return onSubagent(state, event, runId, itemId);
    case 'message':
      return event.type === 'item.completed' ? onMessage(state, event, runId) : state;
    case 'thinking':
      return event.type === 'item.completed' ? onThinking(state, event, runId) : state;
    case 'error':
      return onProblem(state, event, runId);
    case 'todo':
      return event.type === 'item.updated' ? onTodos(state, event, runId) : state;
    default:
      return state; // 产物不走事件流（回合结束后由 /artifacts 拉取）；evidence 等后端还没有产出，界面先忽略
  }
}

/** 应用一帧，不处理位置（由 reduceWire 负责）。 */
export function applyEvent(state: ChatState, wire: WireEvent): ChatState {
  const event = wire.event;
  switch (event.type) {
    case 'run.started':
      return onRunStarted(state, event);
    case 'run.waiting':
      return onRunWaiting(state, event);
    case 'run.completed':
    case 'run.failed':
    case 'run.cancelled':
      return onRunEnded(state, event);
    default:
      return applyItem(state, event);
  }
}

function indexOf(log: readonly WireEvent[], cursor: Cursor): { found: boolean; index: number } {
  let low = 0;
  let high = log.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    const order = compareCursor((log[mid] as WireEvent).cursor, cursor);
    if (order === 0) return { found: true, index: mid };
    if (order < 0) low = mid + 1;
    else high = mid;
  }
  return { found: false, index: low };
}

/** 本页自己维护、不来自事件流的字段：重算时原样保留。 */
type Kept = Pick<ChatState, 'sent' | 'artifacts' | 'citations' | 'expiredQuestions'>;

export function rebuild(log: readonly WireEvent[], kept: Kept): ChatState {
  const folded = log.reduce(applyEvent, {
    ...initialChatState,
    sent: kept.sent,
    artifacts: kept.artifacts,
    citations: kept.citations,
    expiredQuestions: kept.expiredQuestions,
  });
  const last = log[log.length - 1];
  return { ...folded, log, last: last?.cursor, lastId: last?.id };
}

export function reduceWire(state: ChatState, wire: WireEvent): ChatState {
  if (!state.last || compareCursor(wire.cursor, state.last) > 0) {
    return { ...applyEvent(state, wire), log: [...state.log, wire], last: wire.cursor, lastId: wire.id };
  }
  const { found, index } = indexOf(state.log, wire.cursor);
  if (found) return state; // 重复帧
  return rebuild([...state.log.slice(0, index), wire, ...state.log.slice(index)], state);
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'wire':
      return reduceWire(state, action.event);
    case 'wire/batch':
      return action.events.reduce(reduceWire, state);
    case 'local/sent': {
      const message = {
        localId: action.localId,
        text: action.text,
        mode: action.mode,
        ...(action.attachments?.length ? { attachments: action.attachments } : {}),
      };
      return {
        ...state,
        sent: [...state.sent, { ...message, afterRuns: state.runOrder.length, status: 'sending' }],
      };
    }
    case 'local/delivered':
      return {
        ...state,
        sent: state.sent.map((m) => (m.localId === action.localId ? { ...m, status: 'sent' } : m)),
      };
    case 'local/failed':
      return { ...state, sent: state.sent.filter((m) => m.localId !== action.localId) };
    case 'local/question-expired':
      return onQuestionExpired(state, action.questionId);
    case 'artifacts/loaded':
      return { ...state, artifacts: action.artifacts };
    case 'citations/loaded':
      return { ...state, citations: action.citations };
    case 'reset':
      return initialChatState;
    default:
      return state;
  }
}
