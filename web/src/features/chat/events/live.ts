/**
 * 逐字输出（DSH 官方 agent/assistant-stream，经宿主、业务服务推来的片段，SSE 事件名 live）。
 * 片段不进事件日志、不带 id，不影响断线续传的位置；拼成的文字只是这一步正式消息到来之前的临时显示：
 * offset 为 0 从头替换（新一步开始、重连后的快照），等于已有字数才接着拼，对不上就不拼、等正式消息。
 * tool：模型开始写某个工具调用了（参数还在写，工具还没跑），执行过程那一行据此写「正在写文件」而不是一直「思考中」。
 * reasoning：思考片段，和正文分开计字数、拼法相同；开始与快照带着思考全文（开始时为空）表示连思考一起替换，
 * 正文片段不带它，保留这一步的思考。执行过程那一行下面边想边显示。
 */
import type { ChatState, LivePiece, V1Event } from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

const isCount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0;

export function parseLive(value: unknown): LivePiece | undefined {
  if (!isRecord(value) || typeof value.run_id !== 'string' || !isCount(value.step)) return undefined;
  const head = { runId: value.run_id, step: value.step };
  if (value.done === true) return { ...head, done: true };
  if (typeof value.tool === 'string' && value.tool) return { ...head, tool: value.tool };
  if (typeof value.reasoning === 'string' && isCount(value.reasoning_offset))
    return { ...head, reasoningOffset: value.reasoning_offset, reasoning: value.reasoning };
  if (!isCount(value.offset) || typeof value.text !== 'string') return undefined;
  const tools = Array.isArray(value.tools)
    ? value.tools.filter((t): t is string => typeof t === 'string')
    : [];
  const reasoning = typeof value.reasoning === 'string' ? { reasoning: value.reasoning } : {};
  return { ...head, offset: value.offset, text: value.text, tools, ...reasoning };
}

export function onLive(state: ChatState, piece: LivePiece): ChatState {
  const current = state.live;
  const same = current?.runId === piece.runId && current.step === piece.step;
  if ('done' in piece) return same ? { ...state, live: undefined } : state;
  if ('tool' in piece) {
    if (!same || current.tools.includes(piece.tool)) return state;
    return { ...state, live: { ...current, tools: [...current.tools, piece.tool] } };
  }
  if ('reasoningOffset' in piece) {
    if (!same || current.reasoning.length !== piece.reasoningOffset) return state;
    return { ...state, live: { ...current, reasoning: current.reasoning + piece.reasoning } };
  }
  if (piece.offset === 0)
    return {
      ...state,
      live: {
        runId: piece.runId,
        step: piece.step,
        text: piece.text,
        tools: piece.tools ?? [],
        // 开始与快照带着思考（可能为空）就连思考一起替换；正文第一段也是 offset 0 但不带它，保留这一步已有的思考
        reasoning: piece.reasoning ?? (same ? current.reasoning : ''),
      },
    };
  if (!same || current.text.length !== piece.offset) return state;
  return { ...state, live: { ...current, text: current.text + piece.text } };
}

/** 这一步的正式消息到了、这一轮结束：临时文字让位给正式内容。 */
export function settleLive(state: ChatState, event: V1Event): ChatState {
  const live = state.live;
  if (!live || event.runId !== live.runId) return state;
  const message =
    event.type === 'item.completed' &&
    event.kind === 'message' &&
    event.data.role !== 'user' &&
    event.data.role !== 'trigger';
  const ended =
    event.type === 'run.completed' || event.type === 'run.failed' || event.type === 'run.cancelled';
  return message || ended ? { ...state, live: undefined } : state;
}
