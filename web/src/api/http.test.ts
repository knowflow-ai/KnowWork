import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, messageForStatus, readableError, request, withQuery } from './http';
import { useSessionStore } from '../stores/session';
import { jsonResponse } from '../test/render';

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().reset();
});

describe('messageForStatus', () => {
  it('优先用后端给的可读说明，不暴露错误码', () => {
    expect(messageForStatus(404, { detail: '会话不存在' })).toBe('会话不存在');
    expect(messageForStatus(502, { detail: { code: 'host_down', message: '运行时暂时不可用' } })).toBe(
      '运行时暂时不可用',
    );
  });

  it('字段校验数组不给人看，未知状态按类别兜底', () => {
    expect(messageForStatus(422, { detail: [{ loc: ['body'] }] })).toBe('提交的内容有误，请检查后重试');
    expect(messageForStatus(503)).toBe('服务暂时不可用，请稍后重试');
    expect(messageForStatus(418)).toBe('请求没有成功，请稍后重试');
    expect(messageForStatus(409)).toBe('操作冲突，请刷新后重试');
  });
});

describe('request', () => {
  it('失败时带上后端错误码供程序判断，消息仍是给人看的话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ detail: { code: 'question_not_found', message: '这个问题已经结束' } }, 404),
      ),
    );
    const error = await request('/api/x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: 'question_not_found', message: '这个问题已经结束' });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: '会话不存在' }, 404)),
    );
    expect(await request('/api/x').catch((e: unknown) => (e as ApiError).code)).toBeUndefined();
  });

  it('同源带 Cookie、JSON 请求体与查询参数，不带令牌头', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      request('/api/x', { method: 'POST', body: { a: 1 }, query: { q: '中 文', skip: undefined, n: 0 } }),
    ).resolves.toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/x?q=%E4%B8%AD+%E6%96%87&n=0');
    expect(init.credentials).toBe('same-origin');
    expect(init.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json' });
    expect(init.body).toBe('{"a":1}');
  });

  it('401 时标记会话失效；要求安静的请求不标记', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: '未登录' }, 401)),
    );
    await expect(request('/api/x', { quietUnauthorized: true })).rejects.toMatchObject({ status: 401 });
    expect(useSessionStore.getState().expired).toBe(false);
    await expect(request('/api/x')).rejects.toMatchObject({ status: 401, message: '未登录' });
    expect(useSessionStore.getState().expired).toBe(true);
  });

  it('网络失败给出可读提示，空响应体返回 undefined，坏 JSON 按状态兜底', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))),
    );
    await expect(request('/api/x')).rejects.toMatchObject({
      status: 0,
      message: '无法连接服务，请检查网络后重试',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 202 })),
    );
    await expect(request('/api/x')).resolves.toBeUndefined();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('not json', { status: 500 })),
    );
    await expect(request('/api/x')).rejects.toMatchObject({ message: '服务暂时不可用，请稍后重试' });
  });

  it('取消请求时原样抛出', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new DOMException('aborted', 'AbortError'))),
    );
    await expect(request('/api/x')).rejects.toBeInstanceOf(DOMException);
  });

  it('withQuery 没有参数时原样返回', () => {
    expect(withQuery('/a')).toBe('/a');
    expect(withQuery('/a', { x: null, y: '' })).toBe('/a');
  });
});

describe('readableError', () => {
  it('只透出 ApiError 的说明', () => {
    expect(readableError(new ApiError(500, '服务忙'))).toBe('服务忙');
    expect(readableError(new Error('stack trace'), '兜底')).toBe('兜底');
  });
});
