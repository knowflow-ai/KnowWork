/**
 * 输入框里还没发出去的附件直接进编辑器，不用先打一句话发出去：开着在线编辑（扩展经 chat.files.server 补的 editsDrafts 说能）时，
 * 能编辑的附件卡片点得开；新对话页点了先建一段空对话、带着输入框里的草稿进会话页，那边进来就建副本、打开编辑器。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, renderWithApp } from '../../test/render';
import { register } from '../../slots/registry';
import type { StreamHandlers } from '../../api/sse';
import type { ConversationFiles } from './sources';
import { ev } from './events/fixtures';

const streams: StreamHandlers[] = [];
vi.mock('../../api/sse', () => ({
  openEventStream: (_options: unknown, handlers: StreamHandlers) => {
    streams.push(handlers);
    return () => undefined;
  },
}));

const { default: ChatPage } = await import('./ChatPage');

const STAGE = '/api/workspaces/default/attachments';
const CREATE = '/api/workspaces/default/conversations';

let editable = true;
const editsDrafts = vi.fn<NonNullable<ConversationFiles['editsDrafts']>>(async () => editable);
const copyFile = vi.fn<NonNullable<ConversationFiles['workingCopy']>>(async (_id, source) =>
  'attachmentId' in source ? '简报.docx' : null,
);
const editFile = vi.fn<NonNullable<ConversationFiles['edit']>>(async () => ({
  url: 'http://127.0.0.1:8003/docs/?path=x',
}));
const prewarm = vi.fn<NonNullable<ConversationFiles['prewarm']>>();
register('chat.files.server', { edit: editFile, workingCopy: copyFile, editsDrafts, prewarm });

type Call = { url: string; method?: string; body?: string | FormData };
let calls: Call[] = [];
let staged: unknown = { items: [], failures: [] };

beforeEach(() => {
  streams.length = 0;
  calls = [];
  editable = true;
  copyFile.mockClear();
  editsDrafts.mockClear();
  prewarm.mockClear();
  // 窗口够宽：能编辑的文件并排开在对话右边
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('min-width'),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: init?.body as string | FormData | undefined });
      if (url === CREATE && init?.method === 'POST') return jsonResponse({ id: 'c-new', title: 't' }, 201);
      if (url === STAGE && init?.method === 'POST') return jsonResponse(staged);
      if (url === '/api/workspaces/default/conversations/mention-options')
        return jsonResponse({ bases: [], services: [], skills: [] });
      return jsonResponse({ messageId: 'm1' }, 202);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function stage(...names: readonly string[]) {
  staged = {
    items: names.map((name, index) => ({
      id: `att_${index + 1}`,
      filename: name,
      size: 9,
      status: 'staged',
    })),
    failures: [],
  };
  fireEvent.change(screen.getByLabelText('选择文件'), {
    target: { files: names.map((name) => new File(['x'], name)) },
  });
}

const drafts = () => screen.getByRole('list', { name: '待发送的附件' });

describe('输入框里的附件直接进编辑器', () => {
  it('新对话页：点 Word 附件先建一段空对话（标题是文件名，不发消息），带着草稿进会话页，进来就按副本打开编辑器', async () => {
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      extraRoutes: { '/chat/:conversationId': <ChatPage /> },
    });
    await userEvent.type(screen.getByRole('textbox'), '先放着');
    stage('简报.docx', '扫描.pdf');
    const open = await within(drafts()).findByRole('button', { name: '打开 简报.docx' });
    expect(within(drafts()).queryByRole('button', { name: '打开 扫描.pdf' })).not.toBeInTheDocument();

    await userEvent.click(open);
    const pane = await screen.findByRole('region', { name: '简报.docx' });
    expect(await within(pane).findByTitle('编辑 简报.docx')).toBeInTheDocument();
    expect(copyFile).toHaveBeenCalledWith('c-new', { attachmentId: 'att_1' });
    expect(editFile).toHaveBeenCalledWith('c-new', '简报.docx');
    const created = calls.filter((c) => c.url === CREATE && c.method === 'POST');
    expect(created).toHaveLength(1);
    expect(JSON.parse(String(created[0]?.body)).title).toBe('简报.docx');
    expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(false);
    // 输入框里的字和附件跟过来了，接着能发
    expect(screen.getByRole('textbox')).toHaveValue('先放着');
    expect(within(drafts()).getByText('扫描.pdf')).toBeInTheDocument();
    expect(copyFile).toHaveBeenCalledTimes(1);
  });

  it('会话页：点输入框里的 Word 附件直接建副本、在编辑器里打开，不建对话；移除按钮照样能点', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    stage('简报.docx');
    await userEvent.click(await within(drafts()).findByRole('button', { name: '打开 简报.docx' }));
    expect(await screen.findByRole('region', { name: '简报.docx' })).toBeInTheDocument();
    expect(copyFile).toHaveBeenCalledWith('c1', { attachmentId: 'att_1' });
    expect(calls.some((c) => c.url === CREATE)).toBe(false);
    await userEvent.click(within(drafts()).getByRole('button', { name: '移除 简报.docx' }));
    expect(screen.queryByRole('list', { name: '待发送的附件' })).not.toBeInTheDocument();
  });

  it('提前起编辑器：新对话页的输入框里加了附件，就把文件名交给扩展（是不是能编辑的格式由它判断）', async () => {
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    stage('简报.docx');
    await waitFor(() => expect(prewarm).toHaveBeenCalledWith(['简报.docx']));
  });

  it('提前起编辑器：会话页的输入框里加了附件也交给扩展', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    // 先问清这段对话在哪执行（问清以前文件能力是空的），人加附件时早就问清了
    await waitFor(() => expect(editsDrafts).toHaveBeenCalled());
    stage('预算.xlsx');
    await waitFor(() => expect(prewarm).toHaveBeenCalledWith(['预算.xlsx']));
  });

  it('提前起编辑器：会话页里有带附件的提问、交付的文件，进来就把文件名交给扩展', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    expect(prewarm).not.toHaveBeenCalled();
    act(() => streams[0]?.onEvent(ev.runStarted(1)));
    act(() => streams[0]?.onEvent(ev.asked(2, '看看这份', 1, 'u2', [{ name: '合同.docx', size: 10 }])));
    await waitFor(() => expect(prewarm).toHaveBeenLastCalledWith(['合同.docx']));
    act(() => streams[0]?.onEvent(ev.delivered(3, [{ path: '/sessions/c1/output/报告.pptx' }])));
    await waitFor(() =>
      expect(prewarm).toHaveBeenLastCalledWith(['合同.docx', '/sessions/c1/output/报告.pptx']),
    );
  });

  it('没开在线编辑：输入框里的附件点不开，也不会白建对话', async () => {
    editable = false;
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    stage('简报.docx');
    await waitFor(() => expect(editsDrafts).toHaveBeenCalled());
    await within(drafts()).findByText('简报.docx');
    expect(within(drafts()).queryByRole('button', { name: '打开 简报.docx' })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === CREATE)).toBe(false);
  });
});
