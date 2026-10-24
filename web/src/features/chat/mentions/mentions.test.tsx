import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
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
  skills: [{ name: 'weekly-report', description: '写周报' }],
};

function setup(onSend = vi.fn(async () => undefined)) {
  const api = routeFetch({
    'GET /api/conversations/mention-options': jsonResponse(OPTIONS),
    'GET /api/conversations/mention-options/documents': jsonResponse({
      documents: [{ id: 'kb1/d1', title: '差旅办法', base: 'builtin.kb1', base_name: '报销制度' }],
    }),
  });
  renderWithApp(<Composer running={false} onSend={onSend} />);
  return { api, onSend, input: screen.getByRole('textbox', { name: '消息' }) };
}

const menu = () => screen.getByRole('listbox', { name: '@ 点名' });
const chips = () => screen.getByRole('list', { name: '这一问 @ 点名的范围与技能' });

describe('输入框 @', () => {
  it('打 @ 先列四个分组，各写选了会怎样；进组选一项变成写明类型的小标签，打出来的 @ 去掉', async () => {
    const { input } = setup();
    await userEvent.type(input, '标准是什么 @');
    await waitFor(() => expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument());
    for (const effect of ['只在选中的文件里找', '只在带这个标签的文件里找', '按这个技能的做法来完成']) {
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
    expect(api.calls.some((c) => c.path === '/api/conversations/mention-options/documents')).toBe(true);
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
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的范围与技能' })).toBeNull();
  });

  it('键盘：↑↓ 选、回车确定、→ 进分组、← 退回、Esc 先退出分组再关；@ 按钮在光标处补一个 @', async () => {
    const { input } = setup();
    await userEvent.click(screen.getByRole('button', { name: '@ 点名' }));
    expect(input).toHaveValue('@');
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /技能/ })).toBeInTheDocument());
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await within(menu()).findByRole('option', { name: '技能 weekly-report' })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'ArrowLeft' });
    expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument();
    expect(within(menu()).getByRole('option', { selected: true })).toHaveTextContent('技能');
    for (let i = 0; i < 3; i += 1) fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    expect(await within(menu()).findByRole('option', { name: /知识库 报销制度/ })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(within(menu()).getByText('只在选中的知识库或目录里找')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(input).toHaveValue('@');
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
      api.calls.some((c) => c.path === '/api/conversations/mention-options/documents' && !c.query.has('q')),
    ).toBe(true);
  });
});

describe('回答方式', () => {
  beforeEach(() => useAnswerMode.setState({ mode: 'agent' }));

  const plus = () => screen.getByRole('button', { name: '添加文件、快速问答' });
  const pill = () => screen.queryByRole('button', { name: '快速问答，点击取消' });

  it('默认是智能体，框里没有标签；从「+」里选快速问答：出现可去掉的标签，占位文字换成查知识库，选择记住', async () => {
    const { input } = setup();
    expect(pill()).toBeNull();
    expect(input).toHaveAttribute('placeholder', '有问题，尽管问');
    await userEvent.click(plus());
    expect(await screen.findByRole('menuitem', { name: '添加文件' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: /快速问答/ }));
    expect(currentAnswerMode()).toBe('quick');
    expect(pill()).toBeInTheDocument();
    expect(input).toHaveAttribute('placeholder', '在知识库里查…');

    await userEvent.click(pill() as HTMLElement);
    expect(currentAnswerMode()).toBe('agent');
    expect(pill()).toBeNull();
  });

  it('快速问答：@ 菜单不列技能，@ 过的技能不显示也不发；切回智能体技能还在', async () => {
    const { input, onSend } = setup();
    await userEvent.type(input, '@weekly');
    await userEvent.click(await screen.findByRole('option', { name: /weekly-report/ }));
    expect(within(chips()).getByText('weekly-report')).toBeInTheDocument();

    act(() => useAnswerMode.getState().setMode('quick'));
    expect(screen.queryByRole('list', { name: '这一问 @ 点名的范围与技能' })).toBeNull();
    await userEvent.type(input, '@');
    await waitFor(() => expect(within(menu()).getByRole('option', { name: /知识库/ })).toBeInTheDocument());
    expect(within(menu()).queryByRole('option', { name: /技能/ })).toBeNull();
    fireEvent.keyDown(input, { key: 'Escape' });
    await userEvent.clear(input);
    await userEvent.type(input, '报销标准');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('报销标准', 'followup', [], []));

    await userEvent.click(pill() as HTMLElement);
    expect(within(chips()).getByText('weekly-report')).toBeInTheDocument();
  });

  it('快速问答不带附件：「+」里添加文件不可用；切过去之前选好的文件发送时提示，不发出去', async () => {
    const { input, onSend } = setup();
    fireEvent.change(screen.getByLabelText('选择文件'), {
      target: { files: [new File(['正文'], '合同.docx')] },
    });
    act(() => useAnswerMode.getState().setMode('quick'));
    await userEvent.click(plus());
    expect(await screen.findByRole('menuitem', { name: '添加文件' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' });
    await userEvent.type(input, '看看合同');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText('快速问答不能带附件，去掉附件或切换到智能体再发')).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue('看看合同');
  });
});
