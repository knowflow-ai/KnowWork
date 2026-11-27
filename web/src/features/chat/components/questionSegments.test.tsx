import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { AssistantTurn } from './AssistantTurn';
import { splitAtQuestions } from '../events/segments';
import type { QuestionView, RunView, StepView } from '../events/types';

const step = (callId: string, start: number, end: number): StepView => ({
  callId,
  kind: 'tool',
  name: 'knowledge_search',
  arguments: '{"query":"合同"}',
  status: 'done',
  startedAt: start,
  endedAt: end,
});

const question = (patch: Partial<QuestionView> = {}): QuestionView => ({
  id: 'q1',
  runId: 'run-1',
  status: 'answered',
  continued: false,
  askedAt: 10_000,
  answeredAt: 60_000,
  questions: [
    { id: 'fmt', question: '导出成什么格式？', header: '格式', options: [], multiSelect: false },
    { id: 'cols', question: '包含哪些列？', header: '列', options: [], multiSelect: true },
  ],
  answers: [
    { id: 'fmt', selected: ['Excel (Recommended)'] },
    { id: 'cols', selected: [] },
  ],
  ...patch,
});

const run: RunView = {
  runId: 'run-1',
  status: 'completed',
  startedAt: 0,
  endedAt: 75_000,
  notes: [],
  thinking: ['先问清楚'],
  answer: '导出好了',
  problems: [],
  asked: [],
  trail: [
    { type: 'thinking', text: '先问清楚' },
    { type: 'step', callId: 'c1' },
    { type: 'question', id: 'q1' },
    { type: 'step', callId: 'c2' },
    { type: 'step', callId: 'c3' },
  ],
  steps: [step('c1', 1000, 3000), step('c2', 61_000, 65_000), step('c3', 66_000, 70_000)],
};

function turn(view: RunView, questions: readonly QuestionView[]) {
  return renderWithApp(
    <AssistantTurn
      run={view}
      approvals={[]}
      questions={questions}
      onDecide={vi.fn()}
      onAnswer={vi.fn(async () => true)}
      busy={false}
    />,
  );
}

/** 一轮回答里各块的先后 */
function order(): string[] {
  const article = screen.getByRole('article', { name: '智能体回答' });
  return [
    ...article.querySelectorAll(
      '.kw-process-toggle, .kw-question-reply, .kw-question, .kw-markdown, .kw-thinking-preview',
    ),
  ].map((el) => el.textContent?.trim() ?? '');
}

describe('一轮回答在提问处切成两段（照 ChatGPT「问一句 → 你答 → 接着做」）', () => {
  it('切段：各段只含自己的步骤，用时从开始（或答上）算到提问（或结束）', () => {
    const [before, after] = splitAtQuestions(run, [question()]);
    expect(before?.run.steps.map((s) => s.callId)).toEqual(['c1']);
    expect([before?.run.startedAt, before?.run.endedAt, before?.question?.id]).toEqual([0, 10_000, 'q1']);
    expect(after?.run.steps.map((s) => s.callId)).toEqual(['c2', 'c3']);
    expect([after?.run.startedAt, after?.run.endedAt, after?.question]).toEqual([60_000, 75_000, undefined]);
  });

  it('答完：提问之前一行过程，你的回答是一条用户气泡，后面另起一行过程，紧跟着最终回答', () => {
    turn(run, [question()]);
    expect(order()).toEqual([
      '已完成 1 个步骤 · 用时 10 秒',
      '格式：Excel列：跳过',
      '已完成 2 个步骤 · 用时 15 秒',
      '导出好了',
    ]);
    const reply = screen.getByLabelText('你的回答');
    expect(reply.closest('.kw-user-row')).not.toBeNull();
  });

  it('等你回答：这一轮停在提问卡片那里，下面不再转一行；智能体先继续了的照常显示进行中', () => {
    const waiting: RunView = {
      ...run,
      status: 'waiting',
      endedAt: undefined,
      answer: '',
      trail: run.trail.slice(0, 3),
      steps: [step('c1', 1000, 3000)],
    };
    const view = turn(waiting, [question({ status: 'pending', answers: undefined, answeredAt: undefined })]);
    expect(order()).toEqual([
      expect.stringContaining('已完成 1 个步骤'),
      expect.stringContaining('导出成什么格式？'),
    ]);
    expect(screen.queryByText('思考中')).toBeNull();
    view.unmount();
    turn({ ...waiting, status: 'running' }, [
      question({ status: 'pending', continued: true, answers: undefined, answeredAt: undefined }),
    ]);
    expect(screen.getByText('思考中')).toBeInTheDocument();
  });

  it('还在干活时，过程那一行下面只留最新的一段说明，正在写的替换它；这一轮结束后收进过程，只留最终回答', () => {
    const working: RunView = {
      ...run,
      status: 'running',
      endedAt: undefined,
      answer: '',
      notes: ['先查一下知识库'],
      trail: [
        { type: 'note', text: '先查一下知识库' },
        { type: 'step', callId: 'c1' },
      ],
      steps: [step('c1', 1000, 3000)],
    };
    const view = renderWithApp(
      <AssistantTurn
        run={working}
        approvals={[]}
        questions={[]}
        onDecide={vi.fn()}
        onAnswer={vi.fn(async () => true)}
        busy={false}
        liveText="找到了，接着整理"
      />,
    );
    expect(order()).toEqual(['思考中', '找到了，接着整理']);
    view.unmount();
    const settled = turn(working, []);
    expect(order()).toEqual(['思考中', '先查一下知识库']);
    settled.unmount();
    turn({ ...working, status: 'completed', endedAt: 9000, answer: '整理好了' }, []);
    expect(screen.queryByText('先查一下知识库')).toBeNull();
    expect(order()).toEqual([expect.stringContaining('已完成 1 个步骤'), '整理好了']);
  });

  describe('干活时只露一块：过程一行、思考框、最新的一段说明（内容换、高度不跳）', () => {
    const working: RunView = {
      ...run,
      status: 'running',
      endedAt: undefined,
      answer: '',
      thinking: [],
      notes: ['先查一下知识库', '没找到，换个说法再搜'],
      trail: [
        { type: 'note', text: '先查一下知识库' },
        { type: 'step', callId: 'c1' },
        { type: 'note', text: '没找到，换个说法再搜' },
        { type: 'step', callId: 'c2' },
      ],
      steps: [step('c1', 1000, 3000), step('c2', 4000, 6000)],
    };
    const workingTurn = (live: { liveReasoning?: string; liveText?: string }) =>
      renderWithApp(
        <AssistantTurn
          run={working}
          approvals={[]}
          questions={[]}
          onDecide={vi.fn()}
          onAnswer={vi.fn(async () => true)}
          busy={false}
          {...live}
        />,
      );

    it('思考在最上面，下面只留最新的一段说明，之前的收进过程', () => {
      workingTurn({ liveReasoning: '还是没有，要不要问用户' });
      expect(order()).toEqual(['思考中', '还是没有，要不要问用户', '没找到，换个说法再搜']);
      expect(screen.queryByText('先查一下知识库')).toBeNull();
    });

    it('开始写下一句：思考框不收起（收起的话下面往上跳），正在写的替换上一段说明', () => {
      workingTurn({ liveReasoning: '还是没有，要不要问用户', liveText: '换成按状态筛' });
      expect(order()).toEqual(['思考中', '还是没有，要不要问用户', '换成按状态筛']);
    });

    it('跑着时点开过程：说明和正在想的只在过程里按先后列一遍，下面不再重复', async () => {
      workingTurn({ liveReasoning: '还是没有，要不要问用户' });
      await userEvent.click(screen.getByRole('button', { name: /思考中/ }));
      expect(document.querySelector('.kw-thinking-preview')).toBeNull();
      expect(screen.getAllByText('先查一下知识库')).toHaveLength(1);
      expect(screen.getAllByText('没找到，换个说法再搜')).toHaveLength(1);
      const body = document.querySelector('.kw-process-body')?.textContent ?? '';
      expect(body.indexOf('没找到，换个说法再搜')).toBeLessThan(body.indexOf('还是没有，要不要问用户'));
      await userEvent.click(screen.getByRole('button', { name: /思考中/ }));
      expect(order()).toEqual(['思考中', '还是没有，要不要问用户', '没找到，换个说法再搜']);
    });
  });
});
