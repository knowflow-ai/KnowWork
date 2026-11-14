import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../test/user';
import { renderWithApp, jsonResponse } from '../../test/render';
import { ev, wire } from './events/fixtures';
import type { StreamHandlers } from '../../api/sse';
import type { WireEvent } from './events/types';
import { useAnswerMode } from '../../stores/answerMode';
import { useReplyLanguage } from '../../stores/replyLanguage';
import { browserTimeZone } from '../../api/conversations';

/** 发消息的请求体原文：浏览器时区跟在 language 后面（sendMessage 的字段顺序），时区按跑测试的机器取。 */
function sentBody({ text, mode, language, ...rest }: Record<string, unknown>): string {
  const zone = browserTimeZone();
  return JSON.stringify({ text, mode, language, ...(zone ? { time_zone: zone } : {}), ...rest });
}

const streams: StreamHandlers[] = [];
vi.mock('../../api/sse', () => ({
  openEventStream: (_options: unknown, handlers: StreamHandlers) => {
    streams.push(handlers);
    return () => undefined;
  },
}));

// 延迟导入，确保拿到被替换的事件流
const { default: ChatPage } = await import('./ChatPage');

type Call = { url: string; method?: string; body?: string | FormData };
let calls: Call[] = [];
/** 输入框暂存附件的地址（还没有会话也能传） */
const STAGE = '/api/workspaces/default/attachments';
/** 附件上传的应答；测试里按需改成有失败的。 */
let uploadReply: unknown = { items: [], failures: [] };
/** 查附件状态的应答（等解析时轮询它）。 */
let listReply: unknown = { items: [] };

beforeEach(() => {
  streams.length = 0;
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: init?.body as string | FormData | undefined });
      if (url === '/api/workspaces/default/conversations' && init?.method === 'POST')
        return jsonResponse({ id: 'c-new', title: 't' }, 201);
      if (url === STAGE && init?.method === 'POST') return jsonResponse(uploadReply);
      if (url.startsWith(`${STAGE}?`)) return jsonResponse(listReply);
      if (url.startsWith(`${STAGE}/`) && init?.method === 'DELETE')
        return new Response(null, { status: 204 });
      if (url === '/api/workspaces/default/conversations/mention-options')
        return jsonResponse(MENTION_OPTIONS);
      return jsonResponse({ messageId: 'm1' }, 202);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  uploadReply = { items: [], failures: [] };
  listReply = { items: [] };
  window.localStorage.clear();
  useReplyLanguage.setState({ language: 'zh' });
  useAnswerMode.setState({ mode: 'agent' });
});

const MENTION_OPTIONS = {
  bases: [
    {
      id: 'builtin.kb1',
      name: '报销制度',
      description: '',
      source: '扩展知识库',
      path: ['财务'],
      tags: [],
      pinnable: true,
    },
    {
      id: 'builtin.kb2',
      name: '预算库',
      description: '',
      source: '扩展知识库',
      path: ['财务'],
      tags: [],
      pinnable: true,
    },
  ],
  services: [],
  skills: [],
};

const push = (event: WireEvent) => act(() => streams[streams.length - 1]?.onEvent(event));

describe('ChatPage', () => {
  it('新对话：创建会话、发送消息，带着这条消息进入会话页', async () => {
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      extraRoutes: { '/chat/:conversationId': <ChatPage /> },
    });
    expect(screen.getByText('有什么可以帮你？')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '帮我写周报');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('正在连接…')).toBeInTheDocument());
    expect(
      calls
        .map((c) => c.url)
        // 进会话后台自动拉的（产物、@ 候选、每轮的赞踩、可选的回答方式、对话的范围、可选的模型）不算
        .filter(
          (url) =>
            !/\/(artifacts|mention-options|feedback|answer-modes|scope)$/.test(url) &&
            !url.includes('/chat-models'),
        ),
    ).toEqual([
      '/api/workspaces/default/conversations',
      '/api/workspaces/default/conversations/c-new/messages',
    ]);
    expect(screen.getByText('帮我写周报')).toBeInTheDocument();
  });

  it('从别的页面带着回答方式和 @ 进新对话（例如数据源页「去问」）：换成那种回答方式，@ 摆在输入框里', async () => {
    const source = { type: 'extension', kind: 'data_source', label: '数据源', id: 'p1', name: '销售库' };
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      state: { answerMode: 'analytics', mentions: [source] },
    });
    const chips = screen.getByRole('list', { name: '这一问 @ 点名的内容' });
    expect(within(chips).getByText('销售库')).toBeInTheDocument();
    expect(within(chips).getByText('数据源')).toBeInTheDocument();
    expect(useAnswerMode.getState().mode).toBe('analytics');
  });

  it('回答语言：用户选了 English，之后的消息都带 en', async () => {
    act(() => useReplyLanguage.getState().setLanguage('en'));
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    await userEvent.type(screen.getByRole('textbox'), 'Zleap-AI/SAG-Wiki');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        sentBody({ text: 'Zleap-AI/SAG-Wiki', mode: 'followup', language: 'en', answer_mode: 'agent' }),
      ),
    );
  });

  it('回答方式：在输入框左上角选快速问答，之后的消息都带 quick', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    await pickOption(document.body, '回答方式', /快速问答/);
    await userEvent.type(screen.getByRole('textbox'), '报销标准是多少');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        sentBody({ text: '报销标准是多少', mode: 'followup', language: 'zh', answer_mode: 'quick' }),
      ),
    );
  });

  it('附件：一选文件就传上去暂存，发消息时带上附件 id；消息上方显示文件', async () => {
    uploadReply = {
      items: [{ id: 'att_1', filename: '合同.docx', size: 9, status: 'staged' }],
      failures: [],
    };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['合同正文'], '合同.docx')] },
    });
    await waitFor(() => expect(calls.some((c) => c.url === STAGE && c.method === 'POST')).toBe(true));
    const upload = calls.find((c) => c.url === STAGE);
    expect((upload?.body as FormData).getAll('files').map((f) => (f as File).name)).toEqual(['合同.docx']);
    await userEvent.type(screen.getByRole('textbox'), '改一下付款条款');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        sentBody({
          text: '改一下付款条款',
          mode: 'followup',
          language: 'zh',
          answer_mode: 'agent',
          attachments: ['att_1'],
        }),
      ),
    );
    expect(calls.some((c) => c.url.includes('/conversations/c1/attachments'))).toBe(false);
    expect(
      within(screen.getByRole('list', { name: '这条消息带的附件' })).getByText('合同.docx'),
    ).toBeInTheDocument();
  });

  it('新对话：还没有会话时就传上去，发首条时才建会话、带上附件 id', async () => {
    uploadReply = { items: [{ id: 'att_9', filename: '年报.pdf', size: 9, status: 'staged' }], failures: [] };
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['%PDF-'], '年报.pdf')] },
    });
    await waitFor(() => expect(calls.some((c) => c.url === STAGE)).toBe(true));
    expect(calls.some((c) => c.url === '/api/workspaces/default/conversations')).toBe(false);
    await userEvent.type(screen.getByRole('textbox'), '总结一下');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(
        JSON.parse(String(calls.find((c) => c.url.endsWith('/c-new/messages'))?.body)).attachments,
      ).toEqual(['att_9']),
    );
  });

  it('加附件时预热对话；发出去马上清空输入框、显示「思考中」，不等发送接口；发送失败把文字和附件放回输入框', async () => {
    uploadReply = {
      items: [{ id: 'att_1', filename: '合同.docx', size: 9, status: 'staged' }],
      failures: [],
    };
    const original = globalThis.fetch;
    let reply: (response: Response) => void = () => undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        if (!url.endsWith('/messages')) return original(url, init);
        calls.push({ url, method: init?.method, body: init?.body as string | undefined });
        return new Promise<Response>((resolve) => {
          reply = resolve;
        });
      }),
    );
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['合同正文'], '合同.docx')] },
    });
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/workspaces/default/conversations/c1/warm')).toBe(true),
    );
    await waitFor(() => expect(calls.some((c) => c.url === STAGE && c.method === 'POST')).toBe(true));
    await userEvent.type(screen.getByRole('textbox'), '改一下付款条款');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(true));
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByText('思考中')).toBeInTheDocument();
    expect(screen.getByText('改一下付款条款')).toBeInTheDocument();
    await act(async () => reply(jsonResponse({ detail: '沙箱暂时不可用' }, 503)));
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue('改一下付款条款'));
    expect(screen.queryByText('思考中')).not.toBeInTheDocument();
    expect(screen.getByText('合同.docx')).toBeInTheDocument();
  });

  it('新对话：按了发送马上换成这条消息和「思考中」，不等建会话；建好进会话页', async () => {
    const original = globalThis.fetch;
    let created: () => void = () => undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) =>
        url === '/api/workspaces/default/conversations' && init?.method === 'POST'
          ? new Promise<Response>((resolve) => {
              created = () => resolve(jsonResponse({ id: 'c-new', title: 't' }, 201));
            })
          : original(url, init),
      ),
    );
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      extraRoutes: { '/chat/:conversationId': <ChatPage /> },
    });
    await userEvent.type(screen.getByRole('textbox'), '帮我写周报');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText('思考中')).toBeInTheDocument();
    expect(screen.getByText('帮我写周报')).toBeInTheDocument();
    expect(screen.queryByText('有什么可以帮你？')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('');
    act(() => created());
    await waitFor(() => expect(screen.getByText('正在连接…')).toBeInTheDocument());
    expect(screen.getByText('帮我写周报')).toBeInTheDocument();
  });

  it('新对话建不成：换回欢迎页，刚才的话放回输入框', async () => {
    const original = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) =>
        url === '/api/workspaces/default/conversations' && init?.method === 'POST'
          ? Promise.resolve(jsonResponse({ detail: '宿主暂时不可用' }, 503))
          : original(url, init),
      ),
    );
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    await userEvent.type(screen.getByRole('textbox'), '帮我写周报');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue('帮我写周报'));
    expect(screen.getByText('有什么可以帮你？')).toBeInTheDocument();
    expect(screen.queryByText('思考中')).not.toBeInTheDocument();
  });

  it('附件还在解析：点了发送先等着，卡片上显示解析中，解析完自动发出去', async () => {
    const pdf = { id: 'att_1', filename: '年报.pdf', size: 9, status: 'staged', parseStatus: 'parsing' };
    uploadReply = { items: [pdf], failures: [] };
    listReply = { items: [pdf] };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['%PDF-'], '年报.pdf')] },
    });
    const draft = await screen.findByRole('list', { name: '待发送的附件' });
    expect(await within(draft).findByText('解析中')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '总结一下');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText('解析完自动发送')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(false);
    listReply = { items: [{ ...pdf, parseStatus: 'ready' }] };
    await waitFor(
      () =>
        expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
          sentBody({
            text: '总结一下',
            mode: 'followup',
            language: 'zh',
            answer_mode: 'agent',
            attachments: ['att_1'],
          }),
        ),
      { timeout: 4000 },
    );
    expect(calls.find((c) => c.url.startsWith(`${STAGE}?`))?.url).toBe(`${STAGE}?ids=att_1`);
  });

  it('长文档解析完还在生成导读：卡片上显示做到第几段，点了发送等导读做完再发', async () => {
    const pdf = {
      id: 'att_1',
      filename: '年报.pdf',
      size: 9,
      status: 'staged',
      parseStatus: 'ready',
      digestStatus: 'digesting',
      digestDone: 3,
      digestTotal: 10,
    };
    uploadReply = { items: [pdf], failures: [] };
    listReply = { items: [pdf] };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['%PDF-'], '年报.pdf')] },
    });
    const draft = await screen.findByRole('list', { name: '待发送的附件' });
    expect(await within(draft).findByText('生成导读 3/10')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '总结全文');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText('导读生成完自动发送')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '不等了，直接发送' })).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(false);
    listReply = { items: [{ ...pdf, digestStatus: 'ready', digestDone: 10 }] };
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(true), {
      timeout: 4000,
    });
  });

  it('解析太久可以不等：点「直接发送」马上发出去', async () => {
    const pdf = { id: 'att_1', filename: '年报.pdf', size: 9, status: 'staged', parseStatus: 'parsing' };
    uploadReply = { items: [pdf], failures: [] };
    listReply = { items: [pdf] };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['%PDF-'], '年报.pdf')] },
    });
    await userEvent.type(screen.getByRole('textbox'), '总结一下');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await userEvent.click(await screen.findByRole('button', { name: '不等了，直接发送' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(true));
  });

  it('传不上去的文件马上提示原因、从输入框里去掉；移除的附件服务端那份也删', async () => {
    uploadReply = {
      items: [],
      failures: [{ filename: 'a.exe', code: 'unsupported_format', message: '不支持这种文件' }],
    };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [new File(['MZ'], 'a.exe')] } });
    expect(await screen.findByText(/a\.exe：不支持这种文件/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('list', { name: '待发送的附件' })).not.toBeInTheDocument());
    uploadReply = { items: [{ id: 'att_2', filename: 'b.txt', size: 1, status: 'staged' }], failures: [] };
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [new File(['b'], 'b.txt')] } });
    const draft = await screen.findByRole('list', { name: '待发送的附件' });
    await waitFor(() => expect(within(draft).queryByText('上传中')).not.toBeInTheDocument());
    await userEvent.click(within(draft).getByRole('button', { name: '移除 b.txt' }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.url === `${STAGE}/att_2`)).toBe(true),
    );
  });

  it('撤掉回答上方的检索条件：用同一个问题重新回答，带上这一轮要去掉的条件', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c3', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(ev.asked(2, '财务部的报销标准', 1));
    push(ev.toolStarted(3, 'k1', 'knowledge_search', '{"query":"报销标准"}'));
    push(
      wire({
        id: '4',
        type: 'item.completed',
        run: 1,
        item: 'k1',
        kind: 'tool',
        data: { text: '{}', search: { documents: ['b:d1'], conditions: ['部门 = 财务部'], removed: [] } },
      }),
    );
    push(ev.message(5, '差旅按标准报销'));
    push(ev.runCompleted(6));
    await userEvent.click(await screen.findByRole('button', { name: '撤掉「部门 = 财务部」重新回答' }));
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/messages')).at(-1)?.body).toBe(
        sentBody({
          text: '财务部的报销标准',
          mode: 'followup',
          language: 'zh',
          answer_mode: 'agent',
          dropped_conditions: ['部门 = 财务部'],
        }),
      ),
    );
  });

  it('提问 @ 过的东西显示在气泡上方；重新生成照原样带上，目录按路径重新展开', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c4', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(
      wire({
        id: '2',
        type: 'item.completed',
        run: 1,
        item: 'u2',
        kind: 'message',
        data: {
          role: 'user',
          text: '报销标准',
          mentions: [
            { type: 'folder', id: '财务', name: '财务' },
            { type: 'document', id: 'kb1/d1', name: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' },
            { type: 'extension', id: 'p1', name: '销售库', kind: 'data_source', label: '数据源' },
          ],
        },
      }),
    );
    push(ev.message(3, '按标准报销'));
    push(ev.runCompleted(4));
    const shown = screen.getByRole('list', { name: '这条消息 @ 点名的内容' });
    expect(within(shown).getByText('差旅办法')).toBeInTheDocument();
    expect(within(shown).getByText('目录')).toBeInTheDocument();
    expect(within(shown).getByText('数据源')).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/messages'))).toHaveLength(1));
    const body = JSON.parse(String(calls.find((c) => c.url.endsWith('/messages'))?.body));
    expect(body.mentions).toEqual([
      { type: 'folder', id: '财务', name: '财务', bases: ['builtin.kb1', 'builtin.kb2'] },
      { type: 'document', id: 'kb1/d1', name: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' },
      { type: 'extension', id: 'p1', name: '销售库', kind: 'data_source' },
    ]);
  });

  it('事件流里的提问带着附件时，消息上方显示文件', () => {
    renderWithApp(<ChatPage />, { route: '/chat/c2', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(ev.asked(2, '帮我核对', 1, 'u2', [{ name: '报表.xlsx', size: 2048 }]));
    expect(
      within(screen.getByRole('list', { name: '这条消息带的附件' })).getByText('报表.xlsx'),
    ).toBeInTheDocument();
  });

  it('会话页：渲染事件流、审批、停止与插话', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    expect(screen.getByText('正在连接…')).toBeInTheDocument();
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(ev.toolStarted(3, 'c9', 'write_file', '{"path":"报告.docx"}'));
    push(ev.approvalRequested('4.0', 'ap1', 'write_file', 'c9'));
    push(ev.runWaiting(4, 'ap1'));

    expect(screen.getByText('等待批准：write_file')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '批准' }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.url === '/api/workspaces/default/conversations/c1/approvals/ap1')?.body,
      ).toBe('{"allow":true}'),
    );

    push(ev.approvalResolved(5, 'ap1', 'allowed-once'));
    await userEvent.type(screen.getByRole('textbox'), '顺便加上目录');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', metaKey: true });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        sentBody({ text: '顺便加上目录', mode: 'steer', language: 'zh', answer_mode: 'agent' }),
      ),
    );
    expect(screen.getByText('顺便加上目录')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '停止' }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/workspaces/default/conversations/c1/cancel')).toBe(true),
    );

    push(ev.toolCompleted(6, 'c9', '已写入'));
    push(ev.message(7, '报告已生成[ID:1]'));
    push(ev.runCompleted(8));
    expect(await screen.findByText(/报告已生成/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /已完成 1 个步骤/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '重新生成' }));
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/messages')).at(-1)?.body).toBe(
        sentBody({ text: '顺便加上目录', mode: 'followup', language: 'zh', answer_mode: 'agent' }),
      ),
    );
  });

  it('交付的文件：按官方 present 的路径挂在交付的那一轮下面，点开按路径读业务服务', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push({ url });
        return url.includes('/files/content') ? new Response('# 总结') : jsonResponse({}, 202);
      }),
    );
    renderWithApp(<ChatPage />, { route: '/chat/c5', path: '/chat/:conversationId' });
    push(ev.runStarted(1, 1));
    push(ev.message(2, '表格做好了', 1));
    push(ev.delivered(3, [{ path: '/sessions/c5/上一轮.md' }], 1));
    push(ev.runCompleted(4, 1));
    push(ev.runStarted(5, 2));
    push(ev.delivered(6, [{ path: '/sessions/c5/out/总结.md', description: '年度总结（按部门）' }], 2));
    push(ev.message(7, '总结已生成，可在下方查看。', 2));
    push(ev.runCompleted(8, 2));
    expect(await screen.findByText('总结.md')).toBeInTheDocument();
    const turns = screen.getAllByRole('article', { name: '助手回答' });
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveTextContent('上一轮.md');
    expect(turns[0]).not.toHaveTextContent('总结.md');
    // 说明末尾括号里的补充去掉（同官方交付卡片）
    expect(turns[1]).toHaveTextContent('年度总结');
    expect(turns[1]).not.toHaveTextContent('按部门');
    // 确认了这段对话在哪执行（取对话信息回来）后卡片才点得开
    await userEvent.click(await within(turns[1]!).findByRole('button', { name: '预览 总结.md' }));
    const sheet = await screen.findByRole('dialog', { name: /总结\.md/ });
    expect(await within(sheet).findByRole('heading', { name: '总结' })).toBeInTheDocument();
    expect(calls.map((c) => c.url)).toContain(
      `/api/workspaces/default/conversations/c5/files/content?path=${encodeURIComponent('/sessions/c5/out/总结.md')}`,
    );
    expect(calls.some((c) => c.url.endsWith('/artifacts'))).toBe(false);
  });

  it('提问：卡片作答后提交到提问接口；回答回来后显示回答，待办随轮次显示', async () => {
    renderWithApp(<ChatPage />, { route: '/chat/c6', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(
      ev.todos(2, [
        { content: '读取合同', status: 'completed' },
        { content: '导出台账', status: 'in_progress' },
      ]),
    );
    push(ev.toolStarted(3, 'call_1', 'ask_user_question', '{"questions":[]}'));
    push(ev.questionRequested(4, 'call_1'));
    push(ev.runWaitingInput('4.1', 'call_1'));

    expect(screen.getByText('等待你回答')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /待办 · 已完成 1\/2/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'PDF' }));
    await userEvent.click(screen.getByRole('button', { name: '提交回答' }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.url === '/api/workspaces/default/conversations/c6/questions/call_1')?.body,
      ).toBe('{"answers":[{"id":"fmt","selected":["PDF"]}]}'),
    );
    push(ev.questionAnswered(5, 'call_1', [{ id: 'fmt', selected: ['PDF'] }]));
    expect(await screen.findByText('你的回答：PDF')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /执行：ask_user_question/ })).toBeNull();
  });

  it('提问已结束（后端 question_not_found）：提示原因，卡片标成失效', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { detail: { code: 'question_not_found', message: '这个问题已经结束，不能再回答' } },
          404,
        ),
      ),
    );
    renderWithApp(<ChatPage />, { route: '/chat/c7', path: '/chat/:conversationId' });
    push(ev.runStarted(1));
    push(ev.questionRequested(2, 'call_1'));
    await userEvent.click(screen.getByRole('radio', { name: 'PDF' }));
    await userEvent.click(screen.getByRole('button', { name: '提交回答' }));
    expect(await screen.findByText('这个问题已经结束，不能再回答')).toBeInTheDocument();
    expect(screen.getByText('这个问题已经结束，请在输入框里直接回复')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('会话不存在时禁用输入并说明原因', () => {
    renderWithApp(<ChatPage />, { route: '/chat/c2', path: '/chat/:conversationId' });
    act(() => streams[0]?.onFatal?.('要找的内容不存在或已删除'));
    expect(screen.getByText('要找的内容不存在或已删除')).toBeInTheDocument();
  });

  it('断线时提示正在重连；审批提交失败给出提示；发送失败撤回消息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: '审批不存在或已结束' }, 404)),
    );
    renderWithApp(<ChatPage />, { route: '/chat/c3', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('reconnecting', '无法连接服务'));
    expect(screen.getByText('连接中断，正在重连…')).toBeInTheDocument();
    push(ev.approvalRequested(1, 'ap9', 'bash', undefined, null));
    await userEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(await screen.findByText('审批不存在或已结束')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '一句话');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(screen.getAllByText('审批不存在或已结束').length).toBeGreaterThan(0));
    expect(screen.queryByLabelText('正在发送')).not.toBeInTheDocument();
  });
});

describe('新对话页输入框下面的扩展内容（chat.welcome）', () => {
  it('拿得到当前回答方式；点了就照那句、带着它给的 @ 开新对话', async () => {
    const { register } = await import('../../slots/registry');
    const source = {
      type: 'extension',
      kind: 'data_source',
      label: '数据源',
      id: 'p1',
      name: '销售库',
    } as const;
    register('chat.welcome', {
      Component: ({ answerMode, ask }) => (
        <button type="button" onClick={() => ask('上个月各区域的净收入', [source])}>
          示例 {answerMode}
        </button>
      ),
    });
    useAnswerMode.setState({ mode: 'agent' });
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      extraRoutes: { '/chat/:conversationId': <ChatPage /> },
    });
    await userEvent.click(screen.getByRole('button', { name: '示例 agent' }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/workspaces/default/conversations/c-new/messages')).toBe(true),
    );
    const sent = calls.find((c) => c.url === '/api/workspaces/default/conversations/c-new/messages');
    const body = JSON.parse(String(sent?.body));
    expect(body.text).toBe('上个月各区域的净收入');
    expect(body.mentions).toEqual([
      expect.objectContaining({ type: 'extension', kind: 'data_source', id: 'p1' }),
    ]);
  });
});

describe('挂了范围的对话（扩展给它定了名字与固定的回答方式）', () => {
  function withScope(scope: unknown) {
    const original = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) =>
        url === '/api/workspaces/default/conversations/c1/scope' ? jsonResponse(scope) : original(url, init),
      ),
    );
  }

  it('顶部写范围的名字；不显示回答方式，发消息按固定的那种', async () => {
    withScope({ label: '经营分析', answerMode: 'quick' });
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    expect(await screen.findByText('经营分析')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '回答方式' })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('问经营分析…')).toBeInTheDocument();
    // @ 候选按这段对话的范围取（服务端收窄，例如专家对话只列专家能查的库）
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('mention-options') && c.url.includes('conversation_id=c1')),
      ).toBe(true),
    );
    await userEvent.type(screen.getByRole('textbox'), '上月华东区营收');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', metaKey: true });
    await waitFor(() =>
      expect(JSON.parse(String(calls.find((c) => c.url.endsWith('/c1/messages'))?.body)).answer_mode).toBe(
        'quick',
      ),
    );
  });

  it('扩展登记了 chat.scope 就画它的顶部、开场和输入框下那行字：开场里点快捷提问就发出去', async () => {
    const { register } = await import('../../slots/registry');
    register('chat.scope', {
      Bar: ({ label }) => <div>专家：{label}</div>,
      Intro: ({ ask }) => (
        <button type="button" onClick={() => ask('上月营收是多少')}>
          快捷提问
        </button>
      ),
      Footnote: ({ conversationId, fallback }) => (
        <span>{conversationId === 'c1' ? '可查：销售库' : fallback}</span>
      ),
    });
    withScope({ label: '经营分析', answerMode: null });
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    expect(await screen.findByText('专家：经营分析')).toBeInTheDocument();
    expect(screen.queryByText('经营分析', { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText('可查：销售库')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '回答方式' })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: '快捷提问' }));
    await waitFor(() =>
      expect(JSON.parse(String(calls.find((c) => c.url.endsWith('/c1/messages'))?.body)).text).toBe(
        '上月营收是多少',
      ),
    );
  });
});
