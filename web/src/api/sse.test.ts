import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSseParser, openEventStream, toWireEvent } from './sse';
import { useSessionStore } from '../stores/session';
import type { WireEvent } from '../features/chat/events/types';

function streamOf(chunks: readonly string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
}

afterEach(() => {
  useSessionStore.getState().reset();
});

describe('createSseParser', () => {
  it('跨片段拼接，跳过注释，支持多行 data 与 CRLF', () => {
    const parse = createSseParser();
    expect(parse('id: 1\nevent: run.st')).toEqual([]);
    expect(parse('atus\ndata: {"a":1}\n\n: ping\n\n')).toEqual([
      { id: '1', event: 'run.status', data: '{"a":1}' },
    ]);
    expect(parse('id: 2\r\ndata: x\r\ndata: y\r\n\r\n')).toEqual([
      { id: '2', event: 'message', data: 'x\ny' },
    ]);
  });

  it('没有 data 的块不产生消息，无冒号字段按空值处理', () => {
    const parse = createSseParser();
    expect(parse('event: a\n\ndata\n\n')).toEqual([{ id: undefined, event: 'message', data: '' }]);
  });
});

const v1 = (seq: number, type = 'run.started') =>
  JSON.stringify({
    schema_version: 'events.v1',
    type,
    seq,
    run_id: 'run-1',
    time: 1,
    item_id: null,
    kind: null,
    data: {},
  });

describe('toWireEvent', () => {
  it('解析位置与 events.v1 事件', () => {
    expect(toWireEvent({ id: '7.1', event: 'run.started', data: v1(7) })).toMatchObject({
      id: '7.1',
      cursor: { seq: 7, index: 1 },
      event: { type: 'run.started', seq: 7, runId: 'run-1', itemId: null, data: {} },
    });
    expect(toWireEvent({ id: '7', event: 'x', data: v1(7) })?.cursor.index).toBe(Infinity);
  });

  it.each([
    [{ id: 'abc', event: 'k', data: v1(1) }],
    [{ id: '0', event: 'k', data: v1(1) }],
    [{ id: '1.x', event: 'k', data: v1(1) }],
    [{ event: 'k', data: v1(1) }],
    [{ id: '3', event: 'k', data: '{bad' }],
    [{ id: '3', event: 'k', data: '{"schema_version":"events.v0","type":"x","seq":3}' }],
    [{ id: '3', event: 'k', data: '{"schema_version":"events.v1","seq":3}' }],
  ])('丢弃不合法的消息 %#', (message) => {
    expect(toWireEvent(message)).toBeUndefined();
  });
});

const frame = (id: string, seq: number) => `id: ${id}\nevent: run.started\ndata: ${v1(seq)}\n\n`;

describe('openEventStream', () => {
  it('同源带 Cookie 读取事件，断线后用 Last-Event-ID 重连并去重', async () => {
    const calls: Array<{ headers: Record<string, string>; credentials?: string }> = [];
    const fetchImpl = vi.fn(async (_url: unknown, init?: RequestInit) => {
      calls.push({
        headers: { ...(init?.headers as Record<string, string>) },
        credentials: init?.credentials,
      });
      if (calls.length === 1)
        return new Response(streamOf([frame('5.0', 5), frame('5', 5)]), { status: 200 });
      if (calls.length === 2) throw new TypeError('Failed to fetch');
      // 服务端重发了 5，接着给 6
      return new Response(streamOf([frame('5', 5), frame('6', 6)]), { status: 200 });
    });
    const events: WireEvent[] = [];
    const statuses: string[] = [];
    const close = openEventStream(
      { url: '/e', retryMs: 1, maxRetryMs: 2, fetchImpl: fetchImpl as typeof fetch },
      {
        onEvent: (e) => events.push(e),
        onStatus: (s, reason) => statuses.push(reason ? `${s}:${reason}` : s),
      },
    );
    await vi.waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(4));
    close();
    expect(events.map((e) => e.id)).toEqual(['5.0', '5', '6']);
    expect(calls[0]).toMatchObject({ credentials: 'same-origin', headers: { Accept: 'text/event-stream' } });
    expect(calls[0]?.headers.Authorization).toBeUndefined();
    expect(calls[0]?.headers['Last-Event-ID']).toBeUndefined();
    expect(calls[2]?.headers['Last-Event-ID']).toBe('5');
    expect(calls[3]?.headers['Last-Event-ID']).toBe('6');
    expect(statuses).toContain('reconnecting:无法连接服务，请检查网络后重试');
  });

  it('从给定位置续传，服务端 5xx 时给出可读原因并重试', async () => {
    let count = 0;
    const seen: string[] = [];
    const fetchImpl = vi.fn(async (_u: unknown, init?: RequestInit) => {
      count += 1;
      seen.push((init?.headers as Record<string, string>)['Last-Event-ID'] ?? '');
      return count === 1 ? new Response('', { status: 502 }) : new Response(streamOf([]), { status: 200 });
    });
    const statuses: string[] = [];
    const close = openEventStream(
      { url: '/e', lastEventId: '9.2', retryMs: 1, fetchImpl: fetchImpl as typeof fetch },
      { onEvent: () => undefined, onStatus: (s, r) => statuses.push(`${s}:${r ?? ''}`) },
    );
    await vi.waitFor(() => expect(statuses).toContain('connected:'));
    close();
    expect(seen[0]).toBe('9.2');
    expect(statuses[0]).toBe('reconnecting:服务暂时不可用，请稍后重试');
  });

  it('会话不存在时停止重连；未登录时还标记会话失效', async () => {
    const fetchImpl = vi.fn(async () => new Response('', { status: 404 }));
    const onFatal = vi.fn();
    openEventStream(
      { url: '/e', retryMs: 1, fetchImpl: fetchImpl as typeof fetch },
      { onEvent: () => undefined, onFatal },
    );
    await vi.waitFor(() => expect(onFatal).toHaveBeenCalledWith('要找的内容不存在或已删除'));
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    const unauthorized = vi.fn(async () => new Response('', { status: 401 }));
    const fatal = vi.fn();
    openEventStream(
      { url: '/e', fetchImpl: unauthorized as typeof fetch },
      { onEvent: () => undefined, onFatal: fatal },
    );
    await vi.waitFor(() => expect(fatal).toHaveBeenCalled());
    expect(useSessionStore.getState().expired).toBe(true);
  });

  it('没有响应体时按可重试处理', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValue(new Response(streamOf([]), { status: 200 }));
    const statuses: string[] = [];
    const close = openEventStream(
      { url: '/e', retryMs: 1, fetchImpl: fetchImpl as unknown as typeof fetch },
      { onEvent: () => undefined, onStatus: (s, r) => statuses.push(`${s}:${r ?? ''}`) },
    );
    await vi.waitFor(() => expect(statuses).toContain('connected:'));
    close();
    expect(statuses[0]).toBe('reconnecting:无法连接服务，请检查网络后重试');
  });
});
