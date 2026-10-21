/** 测试渲染：带上全局 Provider 与内存路由。 */
import { vi } from 'vitest';
import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { Providers } from '../app/Providers';

interface Options {
  readonly route?: string;
  readonly path?: string;
  readonly extraRoutes?: Readonly<Record<string, ReactElement>>;
  readonly client?: QueryClient;
}

export function renderWithApp(
  ui: ReactElement,
  { route = '/', path = '*', extraRoutes = {}, client }: Options = {},
) {
  const queryClient = client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <Providers client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
          {Object.entries(extraRoutes).map(([p, el]) => (
            <Route key={p} path={p} element={el} />
          ))}
        </Routes>
      </MemoryRouter>
    </Providers>,
  );
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(body === undefined ? '' : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export interface RecordedCall {
  readonly method: string;
  readonly path: string;
  readonly query: URLSearchParams;
  readonly body: unknown;
}

type Handler = Response | ((call: RecordedCall) => Response | Promise<Response>);

/**
 * 按「方法 路径」分发的假 fetch，记下每次调用。没登记的请求返回 404，免得测试静默通过。
 * 路径里可以用 * 匹配一段，例如 'DELETE /api/x/*'。
 */
export function routeFetch(routes: Readonly<Record<string, Handler>>) {
  const calls: RecordedCall[] = [];
  const patterns = Object.entries(routes).map(([key, handler]) => {
    const [method, path] = key.split(' ') as [string, string];
    const regex = new RegExp(`^${path.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]+')}$`);
    return { method, regex, handler };
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://kw.test');
    const method = (init?.method ?? 'GET').toUpperCase();
    const raw = init?.body;
    const call: RecordedCall = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body: typeof raw === 'string' ? (JSON.parse(raw) as unknown) : undefined,
    };
    calls.push(call);
    const match = patterns.find((p) => p.method === method && p.regex.test(url.pathname));
    if (!match) return jsonResponse({ detail: `未登记的请求 ${method} ${url.pathname}` }, 404);
    const response = typeof match.handler === 'function' ? await match.handler(call) : match.handler.clone();
    return response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    calls,
    fetchMock,
    find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path),
  };
}

export function testClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

export const ME = { id: 'u1', email: 'li@corp.cn', name: '李雷', authMethod: 'session', scopes: null };
