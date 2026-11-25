import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { Composer, type InputLimits } from '../components/Composer';
import type { FileReference } from '../sources';
import { BUILT_IN_ANSWER_MODES, type ConversationCommand } from '../../../api/conversations';
import { currentAnswerMode, useAnswerMode } from '../../../stores/answerMode';

afterEach(() => vi.unstubAllGlobals());

const OPTIONS = {
  bases: [
    {
      id: 'builtin.kb1',
      name: '报销制度',
      description: '差旅与报销',
      source: '扩展知识库',
      path: ['财务'],
      tags: ['简报'],
      pinnable: true,
    },
  ],
  services: [{ name: 'context7', title: 'Context7', tools: 2, status: 'ready' }],
  skills: [{ name: 'weekly-report', description: '写周报' }],
};

function setup(
  onSend = vi.fn(async () => undefined),
  limits?: InputLimits,
  fileReferences?: (query: string) => Promise<readonly FileReference[]>,
) {
  const api = routeFetch({
    'GET /api/workspaces/default/conversations/mention-options': jsonResponse(OPTIONS),
    'GET /api/workspaces/default/conversations/mention-options/documents': jsonResponse({
      documents: [{ id: 'kb1/d1', title: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' }],
    }),
    // 输入框一选文件就传上去暂存
    'POST /api/workspaces/default/attachments': jsonResponse({
      items: [{ id: 'att_1', filename: '合同.docx', size: 6, status: 'staged' }],
      failures: [],
    }),
  });
  renderWithApp(<Composer running={false} onSend={onSend} limits={limits} fileReferences={fileReferences} />);
  return { api, onSend, input: screen.getByRole('textbox', { name: '消息' }) };
}

const menu = () => screen.getByRole('listbox', { name: '@ 点名' });
const chips = () => screen.getByRole('list', { name: '这一问 @ 点名的内容' });

describe('输入框 @', () => {
  it('打 @ 先列各个分组，各写选了会怎样；进组选一项变成写明类型的小标签，打出来的 @ 去掉', async () => {
    const { input } = setup();
    await userEvent.type(input, '标准是什么 @');
    await waitFor(() => expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument());
    for (const effect of ['只在选中的文件里找', '只在带这个标签的文件里找', '这一轮优先用选中服务的工具']) {
      expect(within(menu()).getByText(effect)).toBeInTheDocument();
    }
    expect(within(menu()).queryByText('按这个技能的做法来完成')).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter' });
    await userEvent.click(within(menu()).getByRole('option', { name: '知识库 报销制度' }));
    expect(input).toHaveValue('标准是什么 ');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(within(chips()).getByText('知识库')).toBeInTheDocument();
    expect(within(chips()).getByText('报销制度')).toBeInTheDocument();
  });

  it('接着打字跨组查，文件按名字去服务端找；发出去带上 @，小标签留着，可以去掉', async () => {
    const { input, onSend, api } = setup();
    await userEvent.type(input, '@差旅');
    const file = await screen.findByRole('option', { name: '文件 差旅办法' });
    expect(within(menu()).getByText('文件 · 只在选中的文件里找')).toBeInTheDocument();
    expect(
      api.calls.some((c) => c.path === '/api/workspaces/default/conversations/mention-options/documents'),
    ).toBe(true);
    await userEvent.click(file);
    await userEvent.type(input, '报销要多久');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(onSend).toHaveBeenCalledWith(
      '报销要多久',
      'followup',
      [],
      [{ type: 'document', id: 'kb1/d1', name: '差旅办法', base: 'builtin.kb1', baseName: '报销制度' }],
    );
    expect(within(chips()).getByText('差旅办法')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '去掉文件「差旅办法」' }));
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的内容' })).toBeNull();
  });

  it('键盘：↑↓ 选、回车确定、→ 进分组、← 退回、Esc 先退出分组再关；@ 按钮在光标处补一个 @', async () => {
    const { input } = setup();
    await userEvent.click(screen.getByRole('button', { name: '@ 点名' }));
    expect(input).toHaveValue('@');
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /MCP/ })).toBeInTheDocument());
    // 分组依次是知识库、文件、标签、MCP（技能在 / 里）：往下三次到 MCP
    for (let i = 0; i < 3; i += 1) fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await within(menu()).findByRole('option', { name: 'MCP Context7' })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'ArrowLeft' });
    expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument();
    expect(within(menu()).getByRole('option', { selected: true })).toHaveTextContent('MCP');
    for (let i = 0; i < 3; i += 1) fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    expect(await within(menu()).findByRole('option', { name: /知识库 报销制度/ })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(input).toHaveValue('@');
  });

  it('@ MCP 服务：小标签写明类型，发出去带服务标识；快速问答不列这一组、也不发', async () => {
    const { input, onSend } = setup();
    await userEvent.type(input, '@context');
    await userEvent.click(await screen.findByRole('option', { name: 'MCP Context7' }));
    expect(within(chips()).getByText('MCP')).toBeInTheDocument();
    await userEvent.type(input, 'React 19 的 useActionState 怎么用');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        'React 19 的 useActionState 怎么用',
        'followup',
        [],
        [{ type: 'mcp', id: 'context7', name: 'Context7' }],
      ),
    );
    act(() => useAnswerMode.getState().setMode('quick'));
    await userEvent.type(input, '@');
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /知识库/ })).toBeInTheDocument());
    expect(within(menu()).queryByRole('option', { name: /MCP/ })).toBeNull();
  });

  it('邮箱里的 @ 不弹菜单', async () => {
    const { input } = setup();
    await userEvent.type(input, 'a@b.com');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('文件组', () => {
  it('进了「文件」组先列最近更新的文件，接着打字按名字查找', async () => {
    const { input, api } = setup();
    await userEvent.type(input, '@');
    await waitFor(() => expect(within(menu()).getByText('只在选中的文件里找')).toBeInTheDocument());
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    expect(await within(menu()).findByRole('option', { name: '文件 差旅办法' })).toBeInTheDocument();
    expect(within(menu()).getByText('这里是最近更新的文件，接着打字按名字查找')).toBeInTheDocument();
    expect(
      api.calls.some(
        (c) =>
          c.path === '/api/workspaces/default/conversations/mention-options/documents' && !c.query.has('q'),
      ),
    ).toBe(true);
  });
});

describe('回答方式', () => {
  beforeEach(() => useAnswerMode.setState({ mode: 'agent' }));

  const modeSelect = () => screen.getByRole('group', { name: '回答方式' });
  const pick = (mode: RegExp) => pickOption(document.body, '回答方式', mode);

  it('回答方式在输入框外常驻显示：默认智能体；选快速问答后占位文字换成查知识库，选择记住', async () => {
    const { input } = setup();
    expect(within(modeSelect()).getByRole('button')).toHaveTextContent('智能体');
    expect(input).toHaveAttribute('placeholder', '有问题，尽管问');
    expect(input.closest('.kw-composer-box')).not.toContainElement(modeSelect());
    // 每项的说明有自己的样式（小一号、灰一档），不和模式名一个样
    await userEvent.click(within(modeSelect()).getByRole('button'));
    const quickOption = await screen.findByRole('option', { name: /快速问答/ });
    expect(within(quickOption).getByText('只查知识库，回答更快')).toHaveClass('kw-answer-mode-description');
    await userEvent.click(quickOption);
    expect(currentAnswerMode()).toBe('quick');
    expect(within(modeSelect()).getByRole('button')).toHaveTextContent('快速问答');
    expect(input).toHaveAttribute('placeholder', '在知识库里查…');

    await pick(/智能体/);
    expect(currentAnswerMode()).toBe('agent');
  });

  it('快速问答：/ 里不列技能，选过的技能不显示也不发；切回智能体技能还在', async () => {
    const { input, onSend } = setup();
    await userEvent.type(input, '/weekly');
    await userEvent.click(await screen.findByRole('option', { name: '技能 weekly-report' }));
    expect(within(chips()).getByText('weekly-report')).toBeInTheDocument();

    act(() => useAnswerMode.getState().setMode('quick'));
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的内容' })).toBeNull();
    await userEvent.type(input, '/');
    expect(screen.queryByRole('listbox')).toBeNull();
    await userEvent.clear(input);
    await userEvent.clear(input);
    await userEvent.type(input, '报销标准');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('报销标准', 'followup', [], []));

    await pick(/智能体/);
    expect(within(chips()).getByText('weekly-report')).toBeInTheDocument();
  });

  it('快速问答不带附件：「+」添加文件不可用；切过去之前选好的文件发送时提示，不发出去', async () => {
    const { input, onSend } = setup();
    expect(screen.getByRole('button', { name: '添加文件' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['正文'], '合同.docx')] },
    });
    act(() => useAnswerMode.getState().setMode('quick'));
    expect(screen.getByRole('button', { name: '添加文件' })).toBeDisabled();
    await userEvent.type(input, '看看合同');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText('快速问答不能带附件，去掉附件或切换到智能体再发')).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue('看看合同');
  });

  it('扩展登记的类型（数据源）是单独一组；选了在别的回答方式里会提示切过去，切了提示就没了；发出去带类型', async () => {
    const onSend = vi.fn(async () => undefined);
    routeFetch({
      'GET /api/workspaces/default/conversations/mention-options': jsonResponse({
        ...OPTIONS,
        extensions: [
          {
            kind: 'data_source',
            label: '数据源',
            effect: '问数只查这个数据源',
            answer_mode: 'analytics',
            items: [{ id: 'p1', name: '销售库', description: '第 3 版' }],
          },
        ],
      }),
    });
    act(() =>
      useAnswerMode.setState({
        mode: 'agent',
        options: [
          { id: 'agent', label: '智能体', description: '' },
          { id: 'analytics', label: '问数', description: '' },
        ],
      }),
    );
    renderWithApp(<Composer running={false} onSend={onSend} />);
    const input = screen.getByRole('textbox', { name: '消息' });
    await userEvent.type(input, '@销售');
    await userEvent.click(await screen.findByRole('option', { name: '数据源 销售库' }));
    expect(within(chips()).getByText('数据源')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('@ 了数据源，切换到「问数」回答会更准');
    await userEvent.click(screen.getByRole('button', { name: '切换' }));
    expect(currentAnswerMode()).toBe('analytics');
    expect(screen.queryByText(/回答会更准/)).toBeNull();
    await userEvent.type(input, '上月销售额');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        '上月销售额',
        'followup',
        [],
        [{ type: 'extension', kind: 'data_source', label: '数据源', id: 'p1', name: '销售库' }],
      ),
    );
    act(() => useAnswerMode.setState({ mode: 'agent', options: BUILT_IN_ANSWER_MODES }));
  });
});

describe('@ 工作文件夹里的文件（本机对话，照官方 @file）', () => {
  const FILES: readonly FileReference[] = [
    { path: '资料 2026', kind: 'directory', insert: '@"资料 2026/' },
    { path: 'brief.docx', kind: 'file', insert: '@brief.docx' },
  ];
  const INSIDE: readonly FileReference[] = [
    { path: '资料 2026/合同.pdf', kind: 'file', insert: '@"资料 2026/合同.pdf"' },
  ];

  it('进「工作文件夹」列出文件；选文件写进 @路径 并结束，选文件夹接着往下列，都不变成小标签', async () => {
    const list = vi.fn(async (query: string) => (query.startsWith('资料 2026/') ? INSIDE : FILES));
    const { input } = setup(undefined, undefined, list);
    await userEvent.type(input, '看看 @');
    await userEvent.click(within(menu()).getByRole('option', { name: /工作文件夹/ }));
    await userEvent.click(await within(menu()).findByRole('option', { name: '文件 brief.docx' }));
    expect(input).toHaveValue('看看 @brief.docx ');
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的内容' })).not.toBeInTheDocument();

    // 输入框在下一帧把光标放到插入的文字后面（浏览器里早就过了这一帧），等它一下再接着打字
    await act(() => new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))));
    await userEvent.type(input, '和 @');
    await userEvent.click(within(menu()).getByRole('option', { name: /工作文件夹/ }));
    await userEvent.click(await within(menu()).findByRole('option', { name: '文件夹 资料 2026' }));
    expect(input).toHaveValue('看看 @brief.docx 和 @"资料 2026/');
    await userEvent.click(await within(menu()).findByRole('option', { name: '文件 资料 2026/合同.pdf' }));
    expect(input).toHaveValue('看看 @brief.docx 和 @"资料 2026/合同.pdf" ');
    expect(list).toHaveBeenCalledWith('资料 2026/');
  });

  it('没给工作文件夹（云端对话）时菜单里没有这一组', async () => {
    const { input } = setup();
    await userEvent.type(input, '@');
    expect(within(menu()).queryByRole('option', { name: /工作文件夹/ })).not.toBeInTheDocument();
  });
});

const slashMenu = () => screen.getByRole('listbox', { name: '/ 技能与命令' });

describe('输入框 /', () => {
  const GOAL = {
    name: 'goal',
    description: 'Set or view the goal for a long-running task',
    hint: '[<objective>|clear|edit <objective>|pause|resume]',
    definitionId: '@deepseek-ai/dsh-command-goal',
  };

  function setupWithCommands(listed: readonly ConversationCommand[] = [GOAL]) {
    const onSend = vi.fn(async () => undefined);
    const run = vi.fn(async () => undefined);
    const list = vi.fn(async () => listed);
    routeFetch({
      'GET /api/workspaces/default/conversations/mention-options': jsonResponse(OPTIONS),
      'GET /api/workspaces/default/me/plugins': jsonResponse({
        plugins: [
          {
            key: 'goal',
            name: '长任务目标',
            description: '',
            source: 'builtin',
            enabled: true,
            locked: false,
          },
          {
            key: 'plan',
            name: '规划模式',
            description: '',
            source: 'builtin',
            enabled: false,
            locked: false,
          },
        ],
      }),
    });
    renderWithApp(<Composer running={false} onSend={onSend} commands={{ key: 'c1', list, run }} />);
    return { onSend, run, list, input: screen.getByRole('textbox', { name: '消息' }) };
  }

  it('开头打 / 列技能和命令；@ 里不再有技能，打技能名时提示到 / 里找', async () => {
    const { input, list } = setupWithCommands();
    expect(list).not.toHaveBeenCalled();
    await userEvent.type(input, '/');
    expect(
      await within(slashMenu()).findByRole('option', { name: '技能 weekly-report' }),
    ).toBeInTheDocument();
    // 官方内置命令照官方命令菜单显示：中文标题、命令名、官方的中文说明
    expect(await within(slashMenu()).findByRole('option', { name: '命令 /goal' })).toHaveTextContent(
      /目标\s*\/goal\s*设置或查看长期任务目标/,
    );
    expect(list).toHaveBeenCalledTimes(1);
    // 管理员设为可选、自己还没打开的插件：提示去「我的插件」打开
    expect(await within(slashMenu()).findByText(/可选的插件「规划模式」你还没打开/)).toBeInTheDocument();
    expect(within(slashMenu()).getByRole('link', { name: '我的插件' })).toHaveAttribute(
      'href',
      '/settings?tab=plugins',
    );
    await userEvent.clear(input);
    await userEvent.type(input, '看看 /');
    expect(screen.queryByRole('listbox', { name: '/ 技能与命令' })).toBeNull();
    await userEvent.clear(input);
    await userEvent.type(input, '@weekly');
    expect(await screen.findByText('技能改到 / 里了：在输入框开头打 / 选技能')).toBeInTheDocument();
  });

  it('上下键移动高亮时，高亮的那一行滚进可见区', async () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    try {
      const { input } = setupWithCommands();
      await userEvent.type(input, '/');
      await within(slashMenu()).findByRole('option', { name: '命令 /goal' });
      scrolled.mockClear();
      fireEvent.keyDown(input, { key: 'ArrowDown' });
      await waitFor(() => expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' }));
      expect(scrolled.mock.contexts.at(-1)).toBe(
        within(slashMenu()).getByRole('option', { name: '命令 /goal' }),
      );
      // 往上：回到技能那一行，它也滚进可见区
      scrolled.mockClear();
      fireEvent.keyDown(input, { key: 'ArrowUp' });
      await waitFor(() =>
        expect(scrolled.mock.contexts.at(-1)).toBe(
          within(slashMenu()).getByRole('option', { name: '技能 weekly-report' }),
        ),
      );
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it('按键让列表滚到鼠标底下时不改高亮；鼠标真的动了才跟着鼠标', async () => {
    const { input } = setupWithCommands();
    await userEvent.type(input, '/');
    const skill = await within(slashMenu()).findByRole('option', { name: '技能 weekly-report' });
    const goal = await within(slashMenu()).findByRole('option', { name: '命令 /goal' });
    fireEvent.mouseMove(goal, { clientX: 10, clientY: 50 });
    expect(goal).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(skill).toHaveAttribute('aria-selected', 'true');
    // 列表滚动后浏览器对鼠标底下的行补发的事件，鼠标位置没变：高亮留在键盘选的那一行
    fireEvent.mouseEnter(goal, { clientX: 10, clientY: 50 });
    fireEvent.mouseMove(goal, { clientX: 10, clientY: 50 });
    expect(skill).toHaveAttribute('aria-selected', 'true');
    fireEvent.mouseMove(goal, { clientX: 12, clientY: 52 });
    expect(goal).toHaveAttribute('aria-selected', 'true');
  });

  it('选技能变成小标签，后面的话照常发出去', async () => {
    const { input, onSend } = setupWithCommands();
    await userEvent.type(input, '/wee');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(
      within(await screen.findByRole('list', { name: '这一问 @ 点名的内容' })).getByText('weekly-report'),
    ).toBeInTheDocument();
    expect(input).toHaveValue('');
    await userEvent.type(input, '写本周的周报');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        '写本周的周报',
        'followup',
        [],
        [{ type: 'skill', id: 'weekly-report', name: 'weekly-report' }],
      ),
    );
  });

  /** 输入框里 @ 那一行的小标签（选中的命令排在最前面） */
  const chips = () => screen.queryByRole('list', { name: '这一问 @ 点名的内容' });
  const removeCommand = (name: string) => screen.queryByRole('button', { name: `去掉命令「${name}」` });

  it('选带参数的命令变成带 × 的小标签（同 ChatGPT 选中的工具），接着写参数，回车执行、不发消息；直接打出来的命令也认', async () => {
    const { input, onSend, run } = setupWithCommands();
    await userEvent.type(input, '/go');
    await userEvent.click(await within(slashMenu()).findByRole('option', { name: '命令 /goal' }));
    expect(within(chips()!).getByText('目标')).toBeInTheDocument();
    expect(removeCommand('目标')).toBeInTheDocument();
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('placeholder', GOAL.hint);
    expect(screen.getByRole('button', { name: '发送' }), '只有命令不带参数也能执行').toBeEnabled();
    await userEvent.type(input, '把三季度的合同核对完');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(run).toHaveBeenCalledWith('/goal 把三季度的合同核对完'));
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue('');
    expect(removeCommand('目标')).toBeNull();
    await userEvent.type(input, '/goal{Escape}');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(run).toHaveBeenLastCalledWith('/goal'));
  });

  it('官方命令的中文写法：打 /目 能找到，标签写中文名，发出时换成 /goal；直接打 /目标 也认', async () => {
    const { input, onSend, run } = setupWithCommands();
    await userEvent.type(input, '/目');
    await userEvent.click(await within(slashMenu()).findByRole('option', { name: '命令 /goal' }));
    expect(removeCommand('目标')).toBeInTheDocument();
    await userEvent.type(input, '写周报');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(run).toHaveBeenCalledWith('/goal 写周报'));
    await userEvent.type(input, '/目标 核对合同{Escape}');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(run).toHaveBeenLastCalledWith('/goal 核对合同'));
    expect(onSend).not.toHaveBeenCalled();
    // 别的中文开头照常当普通消息发
    await userEvent.type(input, '/你好 在吗');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('/你好 在吗', 'followup', [], expect.anything()));
  });

  it('打完命令名按空格也变成标签；点 × 去掉，打过的参数留成普通文字；输入框空着时退格也能去掉', async () => {
    const { input, run } = setupWithCommands();
    await userEvent.type(input, '/go');
    await within(slashMenu()).findByRole('option', { name: '命令 /goal' });
    await userEvent.type(input, 'al 核对合同');
    expect(removeCommand('目标')).toBeInTheDocument();
    expect(input).toHaveValue('核对合同');
    (input as HTMLTextAreaElement).setSelectionRange(0, 0);
    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(removeCommand('目标'), '框里还有字时退格不动标签').toBeInTheDocument();
    await userEvent.click(removeCommand('目标')!);
    expect(removeCommand('目标')).toBeNull();
    expect(input).toHaveValue('核对合同');
    await userEvent.clear(input);
    await userEvent.type(input, '/goal ');
    expect(removeCommand('目标')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(removeCommand('目标')).toBeNull();
    expect(run).not.toHaveBeenCalled();
  });

  it('执行失败：标签和参数都放回去；不带参数的命令选中就执行', async () => {
    const compact = {
      name: 'compact',
      description: 'Compact the conversation',
      definitionId: '@deepseek-ai/dsh-command-compact',
    };
    const { input, run } = setupWithCommands([GOAL, compact]);
    run.mockRejectedValueOnce(new Error('目标没有设成'));
    await userEvent.type(input, '/go');
    await userEvent.click(await within(slashMenu()).findByRole('option', { name: '命令 /goal' }));
    await userEvent.type(input, '核对合同');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText('目标没有设成')).toBeInTheDocument();
    expect(removeCommand('目标')).toBeInTheDocument();
    expect(input).toHaveValue('核对合同');
    await userEvent.click(removeCommand('目标')!);
    await userEvent.clear(input);
    await userEvent.type(input, '/comp');
    await userEvent.click(await within(slashMenu()).findByRole('option', { name: '命令 /compact' }));
    await waitFor(() => expect(run).toHaveBeenLastCalledWith('/compact'));
    expect(removeCommand('压缩')).toBeNull();
  });

  it('/ 开头但不是认得的命令或技能：提示，不当提问发出去；直接打出来的技能名照技能处理', async () => {
    const { input, onSend, run } = setupWithCommands([]);
    await userEvent.type(input, '/plan 先想想{Escape}');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText(/没有 \/plan 这个命令或技能/)).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
    await userEvent.clear(input);
    await userEvent.type(input, '/weekly-report 写周报{Escape}');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        '写周报',
        'followup',
        [],
        [{ type: 'skill', id: 'weekly-report', name: 'weekly-report' }],
      ),
    );
    await userEvent.clear(input);
    await userEvent.type(input, '/data/report 这个路径怎么写');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenLastCalledWith(
        '/data/report 这个路径怎么写',
        'followup',
        [],
        expect.anything(),
      ),
    );
  });
});
