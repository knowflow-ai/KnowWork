/**
 * 会话事件流（events.v1）。
 *
 * 用 fetch 读流而不是 EventSource：能区分 401/404 这类不该重连的失败、能控制退避，
 * 重连时手动带上 Last-Event-ID（与 EventSource 的语义一致，id 形如 `12` 或 `12.0`）。
 */
import { credentialsFor } from './embedAuth';
import { messageForStatus, NETWORK_MESSAGE } from './http';
import { useSessionStore } from '../stores/session';
import { compareCursor, parseCursor, parseV1Event } from '../features/chat/events/parse';
import { parseLive } from '../features/chat/events/live';
import type { Cursor, LivePiece, WireEvent } from '../features/chat/events/types';

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

function liveOf(message: SseMessage): LivePiece | undefined {
  try {
    return parseLive(JSON.parse(message.data));
  } catch {
    return undefined;
  }
}

export interface StreamHandlers {
  readonly onEvent: (event: WireEvent) => void;
  /** 逐字片段（SSE 事件名 live，不带 id）：不进事件日志，也不改变续传位置。 */
  readonly onLive?: (piece: LivePiece) => void;
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
  /** 多久什么都没收到（事件、心跳都算）就当连接已断、重连；默认 IDLE_MS */
  readonly idleMs?: number;
  readonly fetchImpl?: typeof fetch;
}

/**
 * 服务端每 15 秒发一次心跳（conversations/api.py 的 ping=15）。这么久连心跳都没收到，说明连接已经悄悄断了：代理只断了
 * 后端那一侧、浏览器这一侧还开着（开发时业务服务重启，Vite 代理就这样），或者电脑休眠、换网络后连接半开。不管它的话
 * 页面一直等下去，对话停在「思考中」，这一轮的事件都收不到。
 */
const IDLE_MS = 45_000;

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
  // 浏览器会话是 HttpOnly Cookie，同源请求自动带上；网页嵌入页带内存里的嵌入令牌（embedAuth.ts）
  const auth = credentialsFor(headers);
  // 这一次连接自己的中止：关掉事件流时跟着中止；IDLE_MS 内什么都没收到也中止（连接已悄悄断开），外层照常重连续传
  const attempt = new AbortController();
  const stop = () => attempt.abort();
  signal.addEventListener('abort', stop);
  const idleMs = options.idleMs ?? IDLE_MS;
  let idle = setTimeout(stop, idleMs);
  const heard = () => {
    clearTimeout(idle);
    idle = setTimeout(stop, idleMs);
  };
  try {
    await readUntilClosed(options, position, handlers, auth, attempt.signal, heard);
  } catch (error) {
    if (attempt.signal.aborted && !signal.aborted) throw new RetryableStreamError(NETWORK_MESSAGE);
    throw error;
  } finally {
    clearTimeout(idle);
    signal.removeEventListener('abort', stop);
  }
}

async function readUntilClosed(
  options: StreamOptions,
  position: Position,
  handlers: StreamHandlers,
  auth: ReturnType<typeof credentialsFor>,
  signal: AbortSignal,
  heard: () => void,
): Promise<void> {
  const response = await (options.fetchImpl ?? fetch)(options.url, {
    headers: auth.headers,
    signal,
    credentials: auth.credentials,
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
  // 中止时也要让正在等的 read 结束：响应体不一定跟着请求的中止报错（例如测试里的流、某些代理）
  signal.addEventListener('abort', () => void reader.cancel().catch(() => undefined));
  heard();
  for (;;) {
    const { done, value } = await reader.read();
    if (signal.aborted) throw new RetryableStreamError(NETWORK_MESSAGE);
    if (done) return;
    heard();
    for (const message of parse(value)) {
      // 先按事件名分流：解析器在没有 id 的消息上会沿用上一条的 id，片段不能走下面落库事件那条路
      if (message.event === 'live') {
        const piece = liveOf(message);
        if (piece) handlers.onLive?.(piece);
        continue;
      }
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
