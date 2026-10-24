import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { AssistantTurn } from './AssistantTurn';
import type { RunView, SearchSummary, StepView } from '../events/types';

const searchStep = (callId: string, search: SearchSummary): StepView => ({
  callId,
  kind: 'tool',
  name: 'knowledge_search',
  arguments: '{}',
  status: 'done',
  search,
});

const turn = (steps: readonly StepView[], running = false): RunView => ({
  runId: 'run-1',
  status: running ? 'running' : 'completed',
  startedAt: 0,
  endedAt: running ? undefined : 10,
  notes: [],
  thinking: [],
  answer: '差旅按标准报销',
  problems: [],
  asked: [],
  trail: [],
  steps,
});

function show(run: RunView, onDropConditions?: (dropped: readonly string[]) => void) {
  renderWithApp(
    <AssistantTurn
      run={run}
      approvals={[]}
      questions={[]}
      artifacts={[]}
      onDecide={vi.fn()}
      onAnswer={vi.fn(async () => true)}
      onDropConditions={onDropConditions}
      busy={false}
    />,
  );
}

describe('回答上方的检索条件', () => {
  it('列出这一轮检索用的条件与命中文档数；撤掉一个时连同之前撤掉的一起交出去', async () => {
    const onDrop = vi.fn();
    show(
      turn([
        searchStep('c1', {
          documents: ['b:d1', 'b:d2'],
          conditions: ['年度 = 2025'],
          removed: ['部门 = 财务部'],
        }),
        searchStep('c2', {
          documents: ['b:d2', 'b:d3'],
          conditions: ['年度 = 2025', '标签：已签'],
          removed: [],
        }),
      ]),
      onDrop,
    );
    const bar = screen.getByRole('group', { name: '检索条件' });
    expect(within(bar).getByText('命中 3 份文档')).toBeInTheDocument();
    expect(within(bar).getByText('部门 = 财务部').tagName).toBe('DEL');
    expect(within(bar).queryByRole('button', { name: /部门 = 财务部/ })).toBeNull();
    await userEvent.click(within(bar).getByRole('button', { name: '撤掉「标签：已签」重新回答' }));
    expect(onDrop).toHaveBeenCalledWith(['部门 = 财务部', '标签：已签']);
  });

  it('不是最近一轮、正在回答或还没答完时只显示，不能撤', () => {
    show(turn([searchStep('c1', { documents: ['b:d1'], conditions: ['年度 = 2025'], removed: [] })]));
    expect(screen.getByText('年度 = 2025')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /撤掉/ })).toBeNull();
  });

  it('答到一半时能看到条件，但要等答完才能撤', () => {
    show(
      turn([searchStep('c1', { documents: ['b:d1'], conditions: ['年度 = 2025'], removed: [] })], true),
      vi.fn(),
    );
    expect(screen.getByText('年度 = 2025')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /撤掉/ })).toBeNull();
  });

  it('检索没用条件时不显示', () => {
    show(turn([searchStep('c1', { documents: ['b:d1'], conditions: [], removed: [] })]), vi.fn());
    expect(screen.queryByRole('group', { name: '检索条件' })).toBeNull();
  });
});
