import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { jsonResponse, routeFetch, testClient } from '../../../test/render';
import { browser, useMcpLogin } from './login';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useMcpLogin', () => {
  it('外壳里页面没跳走（授权在系统浏览器里做）：回到窗口时清掉「跳转中」并刷新页面数据', async () => {
    routeFetch({
      'POST /api/workspaces/default/me/mcp-connections/mcp_9/login': () =>
        jsonResponse({ authorizeUrl: 'https://as.example/authorize?state=s' }),
    });
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined);
    const client = testClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useMcpLogin(), { wrapper });

    await act(() => result.current[1]('mcp_9', '/settings'));
    expect(go).toHaveBeenCalledWith('https://as.example/authorize?state=s');
    expect(result.current[0]).toBe('mcp_9');
    expect(invalidate).not.toHaveBeenCalled();

    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(result.current[0]).toBeUndefined());
    expect(invalidate).toHaveBeenCalledTimes(1);
    act(() => window.dispatchEvent(new Event('focus')));
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
});
