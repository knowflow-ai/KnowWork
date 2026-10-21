/**
 * 会话事件流（events.v1）。
 *
 * 用 fetch 读流而不是 EventSource：能区分 401/404 这类不该重连的失败、能控制退避，
 * 重连时手动带上 Last-Event-ID（与 EventSource 的语义一致，id 形如 `12` 或 `12.0`）。
 */
import { messageForStatus, NETWORK_MESSAGE } from './http';
import { useSessionStore } from '../stores/session';
import { compareCursor, parseCursor, parseV1Event } from '../features/chat/events/parse';
import type { Cursor, WireEvent } from '../features/chat/events/types';

export interface SseMessage {
  readonly id?: string;
  readonly event: string;
  readonly data: string;
}

/** 增量 SSE 解析器：喂入文本片段，返回已完整的消息。 */
export function createSseParser(): (chunk: string) => SseMessage[] {
  let buffer = '';
  let id: string | undefined;
  let event = '';
  let data: string[] = [];

  const flush = (out: SseMessage[]) => {
    if (data.length > 0) out.push({ id, event: event || 'message', data: data.join('\n') });
    event = '';
    data = [];
  };

  return (chunk) => {
    buffer += chunk;
    const out: SseMessage[] = [];
    const lines = buffer.split(/\r\n|\r|\n/);
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line === '') {
        flush(out);
        continue;
      }
      if (line.startsWith(':')) continue; // 注释与心跳
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'data') data = [...data, value];
      else if (field === 'event') event = value;
      else if (field === 'id' && !value.includes('\0')) id = value;
    }
    return out;
  };
}

/** SSE 消息转成 reducer 能吃的帧；id 或事件格式不对就丢弃。 */
export function toWireEvent(message: SseMessage): WireEvent | undefined {
  const cursor = parseCursor(message.id);
  if (!message.id || !cursor) return undefined;
  let data: unknown;
  try {
    data = JSON.parse(message.data);
  } catch {
    return undefined;
  }
  const event = parseV1Event(data);
  return event ? { id: message.id, cursor, event } : undefined;
}

export interface StreamHandlers {
  readonly onEvent: (event: WireEvent) => void;
  /** 连接状态变化：connected / reconnecting（附带可读原因）。 */
  readonly onStatus?: (status: 'connected' | 'reconnecting', reason?: string) => void;
  /** 不可恢复的失败（如未登录、会话不存在），之后不再重连。 */
  readonly onFatal?: (message: string) => void;
}

export interface StreamOptions {
  readonly url: string;
  /** 已收到的最后一条的 SSE id，重连时原样交回。 */
  readonly lastEventId?: string;
  readonly retryMs?: number;
  readonly maxRetryMs?: number;
  readonly fetchImpl?: typeof fetch;
}

class FatalStreamError extends Error {}
class RetryableStreamError extends Error {}

interface Position {
  id?: string;
  cursor?: Cursor;
}

async function readOnce(
  options: StreamOptions,
  position: Position,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (position.id) headers['Last-Event-ID'] = position.id;
  // 浏览器会话是 HttpOnly Cookie，同源请求自动带上
  const response = await (options.fetchImpl ?? fetch)(options.url, {
    headers,
    signal,
    credentials: 'same-origin',
  });
  if (!response.ok) {
    const message = messageForStatus(response.status);
    if (response.status === 401) useSessionStore.getState().expire();
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      throw new FatalStreamError(message);
    }
    throw new RetryableStreamError(message);
  }
  if (!response.body) throw new RetryableStreamError(NETWORK_MESSAGE);
  handlers.onStatus?.('connected');
  const parse = createSseParser();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    for (const message of parse(value)) {
      const event = toWireEvent(message);
      // 坏帧丢弃；重连时服务端可能重发已收到的帧，也在这里挡掉（reducer 另有幂等兜底）
      if (!event || (position.cursor && compareCursor(event.cursor, position.cursor) <= 0)) continue;
      position.id = event.id;
      position.cursor = event.cursor;
      handlers.onEvent(event);
    }
  }
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      resolve();
    });
  });

/** 打开事件流，断线自动重连（指数退避），返回关闭函数。 */
export function openEventStream(options: StreamOptions, handlers: StreamHandlers): () => void {
  const controller = new AbortController();
  const position: Position = { id: options.lastEventId, cursor: parseCursor(options.lastEventId) };
  const baseDelay = options.retryMs ?? 1000;
  const maxDelay = options.maxRetryMs ?? 15_000;

  void (async () => {
    let delay = baseDelay;
    while (!controller.signal.aborted) {
      try {
        await readOnce(options, position, handlers, controller.signal);
        delay = baseDelay; // 服务端正常关闭（例如跟不上被踢出）：按原间隔重连
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof FatalStreamError) {
          handlers.onFatal?.(error.message);
          return;
        }
        // 只把自己写的提示给用户看，浏览器的英文网络错误统一换成可读的话
        handlers.onStatus?.(
          'reconnecting',
          error instanceof RetryableStreamError ? error.message : NETWORK_MESSAGE,
        );
      }
      await wait(delay, controller.signal);
      delay = Math.min(delay * 2, maxDelay);
    }
  })();

  return () => controller.abort();
}
