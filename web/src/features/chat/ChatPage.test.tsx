import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../test/user';
import { renderWithApp, jsonResponse } from '../../test/render';
import { ev, wire } from './events/fixtures';
import type { StreamHandlers } from '../../api/sse';
import type { WireEvent } from './events/types';
import { useAnswerMode } from '../../stores/answerMode';
import { useReplyLanguage } from '../../stores/replyLanguage';

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
/** 附件上传的应答；测试里按需改成有失败的。 */
let uploadReply: unknown = { items: [], failures: [] };

beforeEach(() => {
  streams.length = 0;
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: init?.body as string | FormData | undefined });
      if (url === '/api/workspaces/default/conversations' && init?.method === 'POST')
        return jsonResponse({ id: 'c-new', title: 't' }, 201);
      if (url.endsWith('/attachments') && init?.method === 'POST') return jsonResponse(uploadReply, 201);
      if (url === '/api/workspaces/default/conversations/mention-options')
        return jsonResponse(MENTION_OPTIONS);
      return jsonResponse({ messageId: 'm1' }, 202);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  uploadReply = { items: [], failures: [] };
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
        // 进会话后台自动拉的（产物、@ 候选、每轮的赞踩）不算
        .filter((url) => !/\/(artifacts|mention-options|feedback)$/.test(url)),
    ).toEqual([
      '/api/workspaces/default/conversations',
      '/api/workspaces/default/conversations/c-new/messages',
    ]);
    expect(screen.getByText('帮我写周报')).toBeInTheDocument();
  });

  it('回答语言：用户选了 English，之后的消息都带 en', async () => {
    act(() => useReplyLanguage.getState().setLanguage('en'));
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    await userEvent.type(screen.getByRole('textbox'), 'Zleap-AI/SAG-Wiki');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        '{"text":"Zleap-AI/SAG-Wiki","mode":"followup","language":"en","answer_mode":"agent"}',
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
        '{"text":"报销标准是多少","mode":"followup","language":"zh","answer_mode":"quick"}',
      ),
    );
  });

  it('附件：先传到这个会话，再带着附件 id 发消息；消息上方显示文件', async () => {
    uploadReply = {
      items: [{ id: 'att_1', filename: '合同.docx', size: 9, status: 'ready', inputName: '合同.docx' }],
      failures: [],
    };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['合同正文'], '合同.docx')] },
    });
    await userEvent.type(screen.getByRole('textbox'), '改一下付款条款');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith('/messages'))?.body).toBe(
        '{"text":"改一下付款条款","mode":"followup","language":"zh","answer_mode":"agent","attachments":["att_1"]}',
      ),
    );
    const upload = calls.find((c) => c.url === '/api/workspaces/default/conversations/c1/attachments');
    expect((upload?.body as FormData).getAll('files').map((f) => (f as File).name)).toEqual(['合同.docx']);
    expect(
      within(screen.getByRole('list', { name: '这条消息带的附件' })).getByText('合同.docx'),
    ).toBeInTheDocument();
  });

  it('附件没有全部收下时消息不发，并说明是哪个文件', async () => {
    uploadReply = {
      items: [],
      failures: [{ filename: 'a.exe', code: 'unsupported_format', message: '不支持这种文件' }],
    };
    renderWithApp(<ChatPage />, { route: '/chat/c1', path: '/chat/:conversationId' });
    act(() => streams[0]?.onStatus?.('connected'));
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [new File(['MZ'], 'a.exe')] } });
    await userEvent.type(screen.getByRole('textbox'), '看看');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText(/a\.exe：不支持这种文件/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(false);
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
        '{"text":"财务部的报销标准","mode":"followup","language":"zh","answer_mode":"agent","dropped_conditions":["部门 = 财务部"]}',
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
          ],
        },
      }),
    );
    push(ev.message(3, '按标准报销'));
    push(ev.runCompleted(4));
    const shown = screen.getByRole('list', { name: '这条消息 @ 点名的内容' });
    expect(within(shown).getByText('差旅办法')).toBeInTheDocument();
    expect(within(shown).getByText('目录')).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/messages'))).toHaveLength(1));
    const body = JSON.parse(String(calls.find((c) => c.url.endsWith('/messages'))?.body));
    expect(body.mentions).toEqual([
      { type: 'folder', id: '财务', name: '财务', bases: ['builtin.kb1', 'builtin.kb2'] },
      { type: 'document', id: 'kb1/d1', name: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' },
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
        '{"text":"顺便加上目录","mode":"steer","language":"zh","answer_mode":"agent"}',
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
        '{"text":"顺便加上目录","mode":"followup","language":"zh","answer_mode":"agent"}',
      ),
    );
  });

  it('产物：进入会话时拉一次，每轮完成后再拉，文件卡片挂在对应那一轮下面，下载走鉴权接口', async () => {
    let listed = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push({ url });
        if (!url.endsWith('/artifacts')) return jsonResponse({}, 202);
        listed += 1;
        const items =
          listed === 1
            ? [{ id: 'art_old', filename: '上一轮.xlsx', size: 1024, runId: 'run-1' }]
            : [
                { id: 'art_old', filename: '上一轮.xlsx', size: 1024, runId: 'run-1' },
                { id: 'art_new', filename: '2025年度团队工作总结.docx', size: 20480, runId: 'run-2' },
              ];
        return jsonResponse({ items });
      }),
    );
    renderWithApp(<ChatPage />, { route: '/chat/c5', path: '/chat/:conversationId' });
    push(ev.runStarted(1, 1));
    push(ev.message(2, '表格做好了', 1));
    push(ev.runCompleted(3, 1));
    expect(await screen.findByText('上一轮.xlsx')).toBeInTheDocument();
    push(ev.runStarted(4, 2));
    push(ev.message(5, '总结已生成，可在下方下载。', 2));
    push(ev.runCompleted(6, 2));
    expect(await screen.findByText('2025年度团队工作总结.docx')).toBeInTheDocument();
    const turns = screen.getAllByRole('article', { name: '助手回答' });
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveTextContent('上一轮.xlsx');
    expect(turns[0]).not.toHaveTextContent('工作总结');
    expect(turns[1]).toHaveTextContent('2025年度团队工作总结.docx');
    expect(turns[1]).toHaveTextContent('Word 文档 · 20 KB');
    const download = screen.getByRole('link', { name: '下载 2025年度团队工作总结.docx' });
    expect(download).toHaveAttribute('href', '/api/workspaces/default/conversations/c5/artifacts/art_new');
    expect(
      calls.filter((c) => c.url === '/api/workspaces/default/conversations/c5/artifacts').length,
    ).toBeLessThanOrEqual(3);
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
