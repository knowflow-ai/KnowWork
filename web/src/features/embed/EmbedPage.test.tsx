import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithApp, jsonResponse } from '../../test/render';
import type { StreamHandlers } from '../../api/sse';
import { embedToken } from '../../api/embedAuth';
import { useWorkspaceStore } from '../../stores/workspace';

const streams: { url: string; handlers: StreamHandlers }[] = [];
vi.mock('../../api/sse', () => ({
  openEventStream: (options: { url: string }, handlers: StreamHandlers) => {
    streams.push({ url: options.url, handlers });
    return () => undefined;
  },
}));

const { default: EmbedPage } = await import('./EmbedPage');
const { visitorId } = await import('./visitor');

const KEY = 'ws1~abcdefghijklmnopqrstuvwx';
const CONFIG = {
  title: 'AI 问数',
  greeting: '你好，我能帮你查数',
  starters: ['上月销售额'],
  color: '#112233',
  position: 'right',
  answerMode: 'agent',
  access: 'public',
};

interface Call {
  readonly url: string;
  readonly method: string;
  readonly init?: RequestInit;
}
let calls: Call[] = [];
let existing: { id: string; title: string }[] = [];
let configStatus = 200;
let access = 'public';

beforeEach(() => {
  streams.length = 0;
  calls = [];
  existing = [];
  configStatus = 200;
  access = 'public';
  useWorkspaceStore.setState({ id: null });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ url, method, init });
      if (url === `/api/embed/v1/channels/${encodeURIComponent(KEY)}`)
        return jsonResponse(
          configStatus === 200 ? { ...CONFIG, access } : { detail: '这个入口已经关闭或不存在' },
          configStatus,
        );
      if (url.endsWith('/sessions') && method === 'POST')
        return jsonResponse({
          token: 'emb_tok.sig',
          expiresAt: Date.now() / 1000 + 7200,
          workspaceId: 'ws1',
        });
      if (url.startsWith('/api/workspaces/ws1/conversations?'))
        return jsonResponse({ items: existing, next: null });
      if (url.endsWith('/mention-options')) return jsonResponse({ bases: [], services: [], skills: [] });
      return jsonResponse({});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

const render = (route = `/embed/${KEY}`) => renderWithApp(<EmbedPage />, { route, path: '/embed/:key' });

describe('网页嵌入页', () => {
  it('不登录：读配置、拿访客 id 换令牌，之后的请求带令牌不带 Cookie；访客看不到附件入口', async () => {
    render();
    expect(await screen.findByText('你好，我能帮你查数')).toBeInTheDocument();
    expect(screen.getByText('AI 问数')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上月销售额' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新对话' })).toBeInTheDocument();
    expect(screen.queryByLabelText('添加文件')).not.toBeInTheDocument();
    const opened = calls.find((c) => c.url.endsWith('/sessions'));
    expect(JSON.parse(String(opened?.init?.body))).toEqual({
      visitor: expect.stringMatching(/^[A-Za-z0-9_-]{32}$/),
    });
    const listed = calls.find((c) => c.url.startsWith('/api/workspaces/ws1/conversations'));
    expect(listed?.init?.credentials).toBe('omit');
    expect((listed?.init?.headers as Record<string, string>).Authorization).toBe('Bearer emb_tok.sig');
    expect(embedToken()).toBe('emb_tok.sig');
    // 只设当前空间，不记成「上次用的空间」
    expect(useWorkspaceStore.getState().id).toBe('ws1');
    expect(window.localStorage.getItem('kw.workspace')).toBeNull();
  });

  it('有上次的会话就接着', async () => {
    existing = [{ id: 'c-last', title: '上次' }];
    render();
    await waitFor(() =>
      expect(streams.map((s) => s.url)).toContain('/api/workspaces/ws1/conversations/c-last/events'),
    );
  });

  it('入口关了或不存在：说清楚，不去换令牌', async () => {
    configStatus = 404;
    render();
    expect(await screen.findByText('这个入口已经关闭或不存在')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/sessions'))).toBe(false);
  });

  it('访客 id 存下来接着用，格式不对的换一个', () => {
    const first = visitorId();
    expect(first).toMatch(/^[A-Za-z0-9_-]{32}$/);
    expect(visitorId()).toBe(first);
    window.localStorage.setItem('kw.embed.visitor', 'bad id');
    expect(visitorId()).not.toBe('bad id');
  });

  it('认证访问：整页打开时用地址 # 后面带的登录信息换令牌，不带访客 id', async () => {
    access = 'member';
    window.history.replaceState(null, '', '/embed/x#token=jwt.from.app');
    render();
    expect(await screen.findByText('你好，我能帮你查数')).toBeInTheDocument();
    const opened = calls.find((c) => c.url.endsWith('/sessions'));
    expect(JSON.parse(String(opened?.init?.body))).toEqual({ token: 'jwt.from.app' });
    expect(window.location.hash).toBe('');
  });

  it('认证访问又没拿到登录信息：说清楚要从接入的系统里打开', async () => {
    access = 'member';
    render();
    expect(await screen.findByText(/这个入口要从接入的系统里打开/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/sessions'))).toBe(false);
  });
});
