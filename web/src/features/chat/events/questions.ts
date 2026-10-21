/**
 * 提问（input_request）与待办（todo）事件折叠进界面状态。全部是纯函数，只返回新对象。
 *
 * 提问以工具调用 id 为身份：迟到回答（前台等待结束后、或宿主重启后的回答）落在后来那一轮里，
 * 按 id 找回原卡片，卡片留在提问的那一轮。
 */
import { isRecord, listOf, str, strings, text } from '../../../api/parse';
import { updateRun, withRun } from './runs';
import type {
  ChatState,
  QuestionAnswerView,
  QuestionClosedReason,
  QuestionItemView,
  QuestionOptionView,
  QuestionView,
  TodoStatus,
  TodoView,
  V1Event,
} from './types';

const CLOSED_REASONS: ReadonlySet<string> = new Set(['timeout', 'cancelled', 'interrupted']);
const TODO_STATUSES: ReadonlySet<string> = new Set(['pending', 'in_progress', 'completed']);

function parseOption(value: unknown): QuestionOptionView | undefined {
  if (!isRecord(value)) return undefined;
  const label = text(value.label);
  if (!label) return undefined;
  const description = text(value.description);
  return description ? { label, description } : { label };
}

function parseQuestion(value: unknown): QuestionItemView | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const question = text(value.question);
  if (!id || !question) return undefined;
  return {
    id,
    question,
    header: text(value.header),
    detail: text(value.detail),
    options: listOf(value.options, parseOption),
    multiSelect: value.multi_select === true,
  };
}

function parseAnswer(value: unknown): QuestionAnswerView | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  const custom = text(value.custom);
  return custom
    ? { id, selected: strings(value.selected), custom }
    : { id, selected: strings(value.selected) };
}

function parseTodo(value: unknown): TodoView | undefined {
  if (!isRecord(value)) return undefined;
  const content = text(value.content);
  const status = str(value.status);
  if (!content || !status || !TODO_STATUSES.has(status)) return undefined;
  return { content, status: status as TodoStatus };
}

function updateQuestion(state: ChatState, id: string, patch: (q: QuestionView) => QuestionView): ChatState {
  const target = state.questions.find((q) => q.id === id);
  if (!target) return state;
  return { ...state, questions: state.questions.map((q) => (q.id === id ? patch(q) : q)) };
}

/** 提问的那一轮不再等人：前台等待结束（回答、到期、关闭）时把「等待中」改回「执行中」。 */
function resumeRun(state: ChatState, question: QuestionView | undefined): ChatState {
  if (!question?.runId) return state;
  return updateRun(state, question.runId, (r) => (r.status === 'waiting' ? { ...r, status: 'running' } : r));
}

function onQuestionRequested(state: ChatState, event: V1Event, id: string): ChatState {
  if (state.questions.some((q) => q.id === id)) return state;
  const questions = listOf(event.data.questions, parseQuestion);
  if (questions.length === 0) return state;
  const expired = state.expiredQuestions.includes(id);
  const question: QuestionView = {
    id,
    runId: event.runId ?? state.openRun,
    questions,
    status: expired ? 'closed' : 'pending',
    continued: false,
    closedReason: expired ? 'expired' : undefined,
  };
  return { ...state, questions: [...state.questions, question] };
}

function onQuestionAnswered(state: ChatState, event: V1Event, id: string): ChatState {
  const target = state.questions.find((q) => q.id === id);
  const answered = updateQuestion(state, id, (q) => ({
    ...q,
    status: 'answered',
    answers: listOf(event.data.answers, parseAnswer),
    closedReason: undefined,
  }));
  return resumeRun(answered, target);
}

function onQuestionContinued(state: ChatState, id: string): ChatState {
  const target = state.questions.find((q) => q.id === id);
  return resumeRun(
    updateQuestion(state, id, (q) => (q.status === 'pending' ? { ...q, continued: true } : q)),
    target,
  );
}

function onQuestionClosed(state: ChatState, event: V1Event, id: string): ChatState {
  const target = state.questions.find((q) => q.id === id);
  const raw = str(event.data.reason) ?? '';
  const reason = (CLOSED_REASONS.has(raw) ? raw : 'cancelled') as QuestionClosedReason;
  return resumeRun(
    updateQuestion(state, id, (q) =>
      q.status === 'pending' ? { ...q, status: 'closed', closedReason: reason } : q,
    ),
    target,
  );
}

export function onQuestion(state: ChatState, event: V1Event, id: string): ChatState {
  switch (event.type) {
    case 'item.started':
      return onQuestionRequested(state, event, id);
    case 'item.completed':
      return onQuestionAnswered(state, event, id);
    case 'item.updated':
      return event.data.state === 'continued' ? onQuestionContinued(state, id) : state;
    case 'item.failed':
      return onQuestionClosed(state, event, id);
    default:
      return state;
  }
}

/** 本页提交回答时后端说已结束：标成失效。记进 expiredQuestions，整体重算后仍然生效。 */
export function onQuestionExpired(state: ChatState, id: string): ChatState {
  const expiredQuestions = state.expiredQuestions.includes(id)
    ? state.expiredQuestions
    : [...state.expiredQuestions, id];
  return updateQuestion({ ...state, expiredQuestions }, id, (q) =>
    q.status === 'pending' ? { ...q, status: 'closed', closedReason: 'expired' } : q,
  );
}

/** 官方待办每次整表替换：这一轮的清单就是最近一次写入。 */
export function onTodos(state: ChatState, event: V1Event, runId: string): ChatState {
  const todos = listOf(event.data.items, parseTodo);
  return updateRun(withRun(state, runId, event.time), runId, (r) => ({ ...r, todos }));
}
