import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { Composer } from '../components/Composer';
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

function setup(onSend = vi.fn(async () => undefined)) {
  const api = routeFetch({
    'GET /api/workspaces/default/conversations/mention-options': jsonResponse(OPTIONS),
    'GET /api/workspaces/default/conversations/mention-options/documents': jsonResponse({
      documents: [{ id: 'kb1/d1', title: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' }],
    }),
  });
  renderWithApp(<Composer running={false} onSend={onSend} />);
  return { api, onSend, input: screen.getByRole('textbox', { name: '消息' }) };
}

const menu = () => screen.getByRole('listbox', { name: '@ 点名' });
const chips = () => screen.getByRole('list', { name: '这一问 @ 点名的内容' });

describe('输入框 @', () => {
  it('打 @ 先列各个分组，各写选了会怎样；进组选一项变成写明类型的小标签，打出来的 @ 去掉', async () => {
    const { input } = setup();
    await userEvent.type(input, '标准是什么 @');
    await waitFor(() => expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument());
    for (const effect of [
      '只在选中的文件里找',
      '只在带这个标签的文件里找',
      '这一轮优先用选中服务的工具',
      '按这个技能的做法来完成',
    ]) {
      expect(within(menu()).getByText(effect)).toBeInTheDocument();
    }
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
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /技能/ })).toBeInTheDocument());
    // 分组依次是知识库、文件、标签、MCP、技能：往下四次到技能
    for (let i = 0; i < 4; i += 1) fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await within(menu()).findByRole('option', { name: '技能 weekly-report' })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'ArrowLeft' });
    expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument();
    expect(within(menu()).getByRole('option', { selected: true })).toHaveTextContent('技能');
    for (let i = 0; i < 4; i += 1) fireEvent.keyDown(input, { key: 'ArrowUp' });
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

  it('快速问答：@ 菜单不列技能，@ 过的技能不显示也不发；切回智能体技能还在', async () => {
    const { input, onSend } = setup();
    await userEvent.type(input, '@weekly');
    await userEvent.click(await screen.findByRole('option', { name: /weekly-report/ }));
    expect(within(chips()).getByText('weekly-report')).toBeInTheDocument();

    act(() => useAnswerMode.getState().setMode('quick'));
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的内容' })).toBeNull();
    await userEvent.type(input, '@');
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /知识库/ })).toBeInTheDocument());
    expect(within(menu()).queryByRole('option', { name: /技能/ })).toBeNull();
    fireEvent.keyDown(input, { key: 'Escape' });
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
});
