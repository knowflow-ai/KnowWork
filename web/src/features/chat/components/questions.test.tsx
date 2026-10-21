import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { QuestionCard } from './QuestionCard';
import { answerText, displayLabel } from './questionText';
import { TodoList } from './TodoList';
import type { QuestionItemView, QuestionView } from '../events/types';

const FORMAT: QuestionItemView = {
  id: 'fmt',
  question: '导出成什么格式？',
  header: '格式',
  options: [{ label: 'Excel (Recommended)', description: '方便继续编辑' }, { label: 'PDF' }],
  multiSelect: false,
};

const COLUMNS: QuestionItemView = {
  id: 'cols',
  question: '要哪些列？',
  options: [{ label: '金额' }, { label: '日期' }, { label: '客户' }],
  multiSelect: true,
};

const NOTE: QuestionItemView = { id: 'note', question: '还有什么要求？', options: [], multiSelect: false };

function pending(questions: readonly QuestionItemView[], patch: Partial<QuestionView> = {}): QuestionView {
  return { id: 'call_1', runId: 'run-1', questions, status: 'pending', continued: false, ...patch };
}

const submitButton = () => screen.getByRole('button', { name: /提交回答/ });

describe('QuestionCard', () => {
  it('单选：推荐选项显示「推荐」标记，选了才能提交，提交的是原标签', async () => {
    const onAnswer = vi.fn(async () => true);
    renderWithApp(<QuestionCard question={pending([FORMAT])} onAnswer={onAnswer} />);
    expect(screen.getByRole('region', { name: '需要你回答的问题' })).toBeInTheDocument();
    expect(screen.getByText('格式')).toBeInTheDocument();
    expect(screen.getByText('推荐')).toBeInTheDocument();
    expect(screen.queryByText(/Recommended/)).toBeNull();
    expect(screen.getByText('回答后 Agent 接着执行')).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
    await userEvent.click(screen.getByRole('radio', { name: /Excel/ }));
    await userEvent.click(submitButton());
    await waitFor(() =>
      expect(onAnswer).toHaveBeenCalledWith([{ id: 'fmt', selected: ['Excel (Recommended)'] }]),
    );
  });

  it('单选填了文字就以文字为准：选项被清掉，再选选项则清掉文字', async () => {
    const onAnswer = vi.fn(async () => true);
    renderWithApp(<QuestionCard question={pending([FORMAT])} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole('radio', { name: 'PDF' }));
    const input = screen.getByRole('textbox', { name: '回答：导出成什么格式？' });
    await userEvent.type(input, 'CSV');
    expect(screen.getByRole('radio', { name: 'PDF' })).not.toBeChecked();
    await userEvent.click(screen.getByRole('radio', { name: 'PDF' }));
    expect(input).toHaveValue('');
    await userEvent.type(input, 'CSV');
    await userEvent.click(submitButton());
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith([{ id: 'fmt', selected: [], custom: 'CSV' }]));
  });

  it('几题一起提交：多选可再补文字，没有选项的题要填文字，都答了才能提交', async () => {
    const onAnswer = vi.fn(async () => true);
    renderWithApp(<QuestionCard question={pending([COLUMNS, NOTE])} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole('checkbox', { name: '金额' }));
    await userEvent.click(screen.getByRole('checkbox', { name: '客户' }));
    await userEvent.type(screen.getByRole('textbox', { name: '回答：要哪些列？' }), '备注');
    expect(submitButton()).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: '回答：还有什么要求？' }), '按月汇总');
    await userEvent.click(submitButton());
    await waitFor(() =>
      expect(onAnswer).toHaveBeenCalledWith([
        { id: 'cols', selected: ['金额', '客户'], custom: '备注' },
        { id: 'note', selected: [], custom: '按月汇总' },
      ]),
    );
  });

  it('提交中与提交成功后锁住卡片，等事件流里的回答；提交失败则恢复可提交', async () => {
    let finish: (ok: boolean) => void = () => undefined;
    const onAnswer = vi.fn(() => new Promise<boolean>((resolve) => (finish = resolve)));
    renderWithApp(<QuestionCard question={pending([FORMAT])} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole('radio', { name: 'PDF' }));
    await userEvent.click(submitButton());
    expect(screen.getByRole('radio', { name: 'PDF' })).toBeDisabled();
    await userEvent.click(submitButton());
    expect(onAnswer).toHaveBeenCalledTimes(1);
    finish(false);
    await waitFor(() => expect(submitButton()).not.toHaveAttribute('data-loading'));
    await userEvent.click(submitButton());
    await waitFor(() => expect(onAnswer).toHaveBeenCalledTimes(2));
    finish(true);
    expect(await screen.findByText('已提交，等 Agent 接收')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /提交回答/ })).toBeNull();
    expect(screen.getByRole('radio', { name: 'PDF' })).toBeDisabled();
  });

  it('前台等待已结束时说明 Agent 先继续了', () => {
    renderWithApp(<QuestionCard question={pending([FORMAT], { continued: true })} onAnswer={vi.fn()} />);
    expect(screen.getByText('Agent 先继续做别的了，你回答后会接着处理')).toBeInTheDocument();
  });

  it('已回答：显示问题与回答，没有输入与提交', () => {
    const question = pending([FORMAT, NOTE], {
      status: 'answered',
      answers: [
        { id: 'fmt', selected: ['Excel (Recommended)'] },
        { id: 'note', selected: [] },
      ],
    });
    renderWithApp(<QuestionCard question={question} onAnswer={vi.fn()} />);
    expect(screen.getByText('你的回答：Excel')).toBeInTheDocument();
    expect(screen.getByText('你的回答：（跳过）')).toBeInTheDocument();
    expect(screen.getByText('已回答')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it.each([
    ['timeout', '等待超时，这个问题已失效'],
    ['interrupted', '运行时重启过，这个问题没等到回答，请在输入框里直接回复'],
    ['expired', '这个问题已经结束，请在输入框里直接回复'],
    ['cancelled', '已失效'],
  ] as const)('关闭（%s）时说明原因，不能再答', (closedReason, text) => {
    renderWithApp(
      <QuestionCard question={pending([FORMAT], { status: 'closed', closedReason })} onAnswer={vi.fn()} />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('推荐标记兼容中文括号；回答写成一句话', () => {
    expect(displayLabel('PDF（推荐）')).toEqual({ text: 'PDF', recommended: true });
    expect(displayLabel('(Recommended)')).toEqual({ text: '(Recommended)', recommended: true });
    expect(displayLabel('PDF')).toEqual({ text: 'PDF', recommended: false });
    expect(answerText({ id: 'x', selected: ['甲 (Recommended)', '乙'], custom: '丙' })).toBe('甲、乙、丙');
    expect(answerText(undefined)).toBe('');
  });
});

describe('TodoList', () => {
  const todos = [
    { content: '读取合同', status: 'completed' as const },
    { content: '抽取条款', status: 'in_progress' as const },
    { content: '生成台账', status: 'pending' as const },
  ];

  it('执行中默认展开，列出每项与状态', () => {
    renderWithApp(<TodoList todos={todos} running />);
    expect(screen.getByRole('button', { name: /待办 · 已完成 1\/3/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByRole('listitem', { name: '抽取条款（进行中）' })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '生成台账（未开始）' })).toBeInTheDocument();
  });

  it('结束后默认收起，点开再看；空清单不显示', async () => {
    const { rerender } = renderWithApp(<TodoList todos={todos} running={false} />);
    const toggle = screen.getByRole('button', { name: /待办 · 已完成 1\/3/ });
    expect(screen.queryByRole('list', { name: '待办' })).toBeNull();
    await userEvent.click(toggle);
    expect(screen.getByRole('list', { name: '待办' })).toBeInTheDocument();
    rerender(<TodoList todos={[]} running={false} />);
    expect(screen.queryByRole('button', { name: /待办/ })).toBeNull();
  });
});
