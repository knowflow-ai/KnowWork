import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { Folder } from '@openai/apps-sdk-ui/components/Icon';
import userEvent, { pickOption } from '../../test/user';
import { ApiError } from '../../api/http';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import { register } from '../../slots/registry';
import ChatPage from './ChatPage';
import type { ConversationFiles, ConversationSource } from './sources';

vi.mock('../preview/PdfView', () => ({
  default: ({ data }: { data: ArrayBuffer }) => <div>PDF 阅读器：{new TextDecoder().decode(data)}</div>,
}));

afterEach(() => vi.unstubAllGlobals());

const started = vi.fn(async (text: string) => ({ id: 'x1', firstMessage: { text } }));
const sent = vi.fn(async () => undefined);
const stopped = vi.fn(async () => undefined);
const decided = vi.fn(async () => undefined);
const answered = vi.fn(async () => undefined);
const readFile = vi.fn<ConversationFiles['read']>(
  async () => new TextEncoder().encode('# 季度报告\n\n正文').buffer,
);
const openFile = vi.fn<NonNullable<ConversationFiles['open']>>(async () => undefined);
const pdfFile = vi.fn<NonNullable<ConversationFiles['pdf']>>(
  async () => new TextEncoder().encode('%PDF-本机转的').buffer,
);
const saveFile = vi.fn<NonNullable<ConversationFiles['save']>>(async () => undefined);
let blocked = false;
// 能不能在这里继续：null 能，字符串是不能的原因，ApiError 是问不到（例如本机执行环境没起来）
let here: string | null | Error = null;

/** 一个不在业务服务上执行的来源：新对话怎么建、操作交给谁由它定；读的还是业务服务。 */
const OTHER: ConversationSource = {
  id: 'elsewhere',
  label: '别处',
  description: '在别处执行',
  server: false,
  welcome: '在别处做点什么？',
  footnote: '在别处执行',
  limits: {
    answerMode: { value: 'agent', reason: '别处只有智能体' },
    attachments: { tip: '别处直接用文件夹里的文件', blocked: '别处不能带附件' },
    mentions: '别处还不能 @',
    model: '别处用默认模型',
  },
  Picker: () => (
    <span>
      <Folder /> 选好的文件夹
    </span>
  ),
  start: ({ text }) => started(text),
  useBlocker: () => (blocked ? '先选好再说' : null),
  actions: {
    check: async () => {
      if (here instanceof Error) throw here;
      return here;
    },
    send: sent,
    cancel: stopped,
    decide: decided,
    answer: answered,
  },
  files: { read: readFile, pdf: pdfFile, save: saveFile, open: openFile },
};
register('chat.sources', OTHER);
// 扩展说得出在用户设备上执行的对话在哪台设备上（chat.place）：默认说不出
let device: string | null = null;
register('chat.place', { describe: async () => device });

/** 业务服务的事件流里有一条助手消息（对话的内容不管在哪执行都从这里来）。 */
function eventsWith(text: string) {
  const event = {
    schema_version: 'events.v1',
    type: 'item.completed',
    seq: 1,
    run_id: 'run-1',
    time: 1,
    item_id: 'm1',
    kind: 'message',
    data: { role: 'assistant', text },
  };
  return new Response(`id: 1.0\nevent: message\ndata: ${JSON.stringify(event)}\n\n`, {
    headers: { 'content-type': 'text/event-stream' },
  });
}

function server(source = 'elsewhere', answer = '业务服务里的回答') {
  routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/workspaces/default/conversations/mention-options': jsonResponse({
      bases: [],
      services: [],
      skills: [],
    }),
    'GET /api/workspaces/default/answer-modes': jsonResponse([]),
    'GET /api/workspaces/default/chat-models': jsonResponse([]),
    'GET /api/workspaces/default/conversations/x1': jsonResponse({ id: 'x1', title: '', source }),
    'GET /api/workspaces/default/conversations/x1/scope': jsonResponse(null),
    'GET /api/workspaces/default/conversations/x1/feedback': jsonResponse([]),
    'GET /api/workspaces/default/conversations/x1/events': () => eventsWith(answer),
    'GET /api/workspaces/default/conversations/x1/artifacts': jsonResponse([]),
    'GET /api/workspaces/default/conversations/x1/citations': jsonResponse({ items: [] }),
  });
}

async function chooseElsewhere() {
  await pickOption(document.body, '在哪执行', /别处/);
}

describe('对话的来源：同一个对话页、都读业务服务，只有在哪执行、操作交给谁不同', () => {
  it('新对话页默认云端；输入框下面选「在哪执行」，换成别处：标题、旁边的控件、下面的小字由它给', async () => {
    server();
    renderWithApp(<ChatPage />, {
      route: '/chat',
      path: '/chat',
      extraRoutes: { '/chat/:conversationId': <div>会话页</div> },
    });
    expect(await screen.findByRole('heading', { name: '有什么可以帮你？' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '在哪执行' })).toHaveTextContent('云端');
    expect(screen.queryByText('选好的文件夹')).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: '消息' }), '整理一下');
    await chooseElsewhere();
    expect(await screen.findByRole('heading', { name: '在别处做点什么？' })).toBeInTheDocument();
    expect(screen.getByText('选好的文件夹')).toBeInTheDocument();
    expect(screen.getByText('在别处执行', { selector: '.kw-composer-hint' })).toBeInTheDocument();
    // 换执行位置不丢已经打的字
    expect(screen.getByRole('textbox', { name: '消息' })).toHaveValue('整理一下');
    await userEvent.type(screen.getByRole('textbox', { name: '消息' }), '{Enter}');
    await waitFor(() => expect(started).toHaveBeenCalledWith('整理一下'));
    expect(await screen.findByText('会话页')).toBeInTheDocument();
  });

  it('别处用不了的输入能力：按钮照常摆着、置灰，回答方式固定为智能体', async () => {
    server();
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    await screen.findByRole('textbox', { name: '消息' });
    expect(screen.getByRole('button', { name: '添加文件' })).toBeEnabled();
    await chooseElsewhere();
    await waitFor(() => expect(screen.getByRole('button', { name: '添加文件' })).toBeDisabled());
    expect(screen.getByRole('button', { name: '@ 点名' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '默认模型' })).toBeDisabled();
    // 组件库的选择框置灰时用 aria-disabled
    expect(within(screen.getByRole('group', { name: '回答方式' })).getByRole('button')).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.getByRole('group', { name: '回答方式' })).toHaveTextContent('智能体');
  });

  it('来源说还不能发时，输入框不可用、占位文字写原因', async () => {
    server();
    blocked = true;
    renderWithApp(<ChatPage />, { route: '/chat', path: '/chat' });
    const box = await screen.findByRole('textbox', { name: '消息' });
    expect(box).toBeEnabled();
    await chooseElsewhere();
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute('placeholder', '先选好再说');
    blocked = false;
  });

  it('会话页：内容从业务服务来，发消息交给这段对话的来源，输入框下面写明在哪执行', async () => {
    server();
    here = null;
    renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
    expect(await screen.findByText('业务服务里的回答')).toBeInTheDocument();
    expect(await screen.findByLabelText('在哪执行：别处')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: '消息' }), '接着来{Enter}');
    // 交出的是和云端发消息同一个请求
    await waitFor(() =>
      expect(sent).toHaveBeenCalledWith('x1', expect.objectContaining({ text: '接着来', mode: 'followup' })),
    );
  });

  it('来源说这里不能继续：照样能看，输入框不可用、写明原因', async () => {
    server();
    here = '在另一台电脑上执行';
    renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
    expect(await screen.findByText('业务服务里的回答')).toBeInTheDocument();
    const box = screen.getByRole('textbox', { name: '消息' });
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute('placeholder', '在另一台电脑上执行');
    here = null;
  });

  it('问不到能不能在这里继续：照不能继续处理，写出原因，不让人发出去才报错', async () => {
    server();
    here = new ApiError(503, '本机执行环境没有起来，请重新打开桌面端再试'); // 来源的约定：出错抛 ApiError，说明给人看
    renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
    expect(await screen.findByText('业务服务里的回答')).toBeInTheDocument();
    const box = screen.getByRole('textbox', { name: '消息' });
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute('placeholder', '本机执行环境没有起来，请重新打开桌面端再试');
    here = null;
  });

  it('在用户设备上执行、这里没有能接手的（网页、手机）：只读，提示到那台电脑上继续', async () => {
    server('device');
    renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
    expect(await screen.findByText('业务服务里的回答')).toBeInTheDocument();
    const box = screen.getByRole('textbox', { name: '消息' });
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute('placeholder', '这段对话在创建它的电脑上执行，请到那台电脑上继续');
    expect(screen.queryByRole('button', { name: '重新生成' })).not.toBeInTheDocument();
  });

  it('扩展说得出在哪台设备上（chat.place）：只读提示写出那台设备（网页、手机上回看时知道回哪台继续）', async () => {
    device = '李雷的 MacBook Pro';
    try {
      server('device');
      renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
      const box = await screen.findByRole('textbox', { name: '消息' });
      await waitFor(() =>
        expect(box).toHaveAttribute(
          'placeholder',
          '这段对话在「李雷的 MacBook Pro」上执行，请到那台电脑上继续',
        ),
      );
    } finally {
      device = null;
    }
  });

  describe('回答里的文件链接（照 DSH 官方：点开在右侧预览，相对路径按这段对话的工作目录）', () => {
    const ANSWER = '写好了：[季度报告](./out/report.md)，参考了 [官网](https://example.com)';

    it('能在这里继续的对话：文件链接点开在右侧预览，字节由来源读；顶部「打开」交给来源用本机程序打开或显示位置', async () => {
      server('elsewhere', ANSWER);
      here = null;
      renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
      const link = await screen.findByRole('button', { name: '季度报告' });
      // 网址照常是链接
      expect(screen.getByRole('link', { name: /官网/ })).toHaveAttribute('href', 'https://example.com');
      await userEvent.click(link);
      const sheet = await screen.findByRole('dialog', { name: /report\.md/ });
      expect(await within(sheet).findByRole('heading', { name: '季度报告' })).toBeInTheDocument();
      expect(readFile).toHaveBeenCalledWith('x1', './out/report.md');
      // 本机文件不给下载，给「打开」
      expect(within(sheet).queryByRole('link', { name: /下载/ })).not.toBeInTheDocument();
      await userEvent.click(within(sheet).getByRole('button', { name: '打开' }));
      await waitFor(() => expect(openFile).toHaveBeenCalledWith('x1', './out/report.md', 'app'));
      await userEvent.click(within(sheet).getByRole('button', { name: '更多打开方式' }));
      await userEvent.click(await screen.findByRole('menuitem', { name: '显示文件位置' }));
      await waitFor(() => expect(openFile).toHaveBeenCalledWith('x1', './out/report.md', 'reveal'));
    });

    it('要转换的（PPT、Word）由来源转成 PDF 交给 PDF 阅读器；箭头菜单里能另存为', async () => {
      server('elsewhere', '做好了：[简报](deck.pptx)');
      here = null;
      renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
      await userEvent.click(await screen.findByRole('button', { name: '简报' }));
      const sheet = await screen.findByRole('dialog', { name: /deck\.pptx/ });
      expect(await within(sheet).findByText('PDF 阅读器：%PDF-本机转的')).toBeInTheDocument();
      expect(pdfFile).toHaveBeenCalledWith('x1', 'deck.pptx');
      await userEvent.click(within(sheet).getByRole('button', { name: '更多打开方式' }));
      await userEvent.click(await screen.findByRole('menuitem', { name: '另存为…' }));
      await waitFor(() => expect(saveFile).toHaveBeenCalledWith('x1', 'deck.pptx'));
    });

    it('这里不能继续（在别的电脑上执行）：文件链接只是文字', async () => {
      server('elsewhere', ANSWER);
      here = '在另一台电脑上执行';
      renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
      expect(await screen.findByText(/写好了/)).toBeInTheDocument();
      await waitFor(() => expect(screen.getByRole('textbox', { name: '消息' })).toBeDisabled());
      expect(screen.queryByRole('button', { name: '季度报告' })).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: '季度报告' })).not.toBeInTheDocument();
      expect(screen.getByText(/写好了/)).toHaveTextContent('写好了：季度报告，参考了');
      here = null;
    });

    it('云端对话：文件链接按路径读业务服务，PPT 用服务端转的 PDF，顶部是下载', async () => {
      const calls = routeFetch({
        'GET /api/auth/me': jsonResponse(ME),
        'GET /api/workspaces/default/conversations/mention-options': jsonResponse({
          bases: [],
          services: [],
          skills: [],
        }),
        'GET /api/workspaces/default/answer-modes': jsonResponse([]),
        'GET /api/workspaces/default/chat-models': jsonResponse([]),
        'GET /api/workspaces/default/conversations/x1': jsonResponse({
          id: 'x1',
          title: '',
          source: 'server',
        }),
        'GET /api/workspaces/default/conversations/x1/scope': jsonResponse(null),
        'GET /api/workspaces/default/conversations/x1/feedback': jsonResponse([]),
        'GET /api/workspaces/default/conversations/x1/events': () =>
          eventsWith('做好了：[简报](out/deck.pptx)，参考了 [官网](https://example.com)'),
        'GET /api/workspaces/default/conversations/x1/artifacts': jsonResponse([]),
        'GET /api/workspaces/default/conversations/x1/citations': jsonResponse({ items: [] }),
        'GET /api/workspaces/default/conversations/x1/files/preview': (call) =>
          new Response(`%PDF-服务端转的 ${call.query.get('path')}`),
      });
      renderWithApp(<ChatPage />, { route: '/chat/x1', path: '/chat/:conversationId' });
      const deck = await screen.findByRole('button', { name: '简报' });
      expect(screen.getByRole('link', { name: /官网/ })).toBeInTheDocument();
      await userEvent.click(deck);
      const sheet = await screen.findByRole('dialog', { name: /deck\.pptx/ });
      expect(await within(sheet).findByText('PDF 阅读器：%PDF-服务端转的 out/deck.pptx')).toBeInTheDocument();
      expect(calls.find('GET', '/api/workspaces/default/conversations/x1/files/preview')).toHaveLength(1);
      // 云端没有本机程序可开：顶部只有下载
      expect(within(sheet).getByRole('button', { name: /下载/ })).toBeInTheDocument();
      expect(within(sheet).queryByRole('button', { name: '打开' })).not.toBeInTheDocument();
    });
  });
});
