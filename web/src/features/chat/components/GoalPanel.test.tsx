import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { GoalPanel } from './GoalPanel';
import { TriggerNotice } from './TriggerNotice';

describe('长任务目标的进度', () => {
  it('进行中：写目标和第几轮，能暂停、清除；按钮执行的是 /goal 命令', async () => {
    const run = vi.fn(async () => undefined);
    renderWithApp(
      <GoalPanel goal={{ objective: '核对三份合同', phase: 'active', rounds: 3, maxRounds: 20 }} run={run} />,
    );
    expect(screen.getByText('进行中')).toBeInTheDocument();
    expect(screen.getByText('自动接着做了 3 / 20 轮')).toBeInTheDocument();
    expect(screen.getByText('核对三份合同')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '暂停' }));
    expect(run).toHaveBeenCalledWith('/goal pause');
    await userEvent.click(screen.getByRole('button', { name: '清除' }));
    expect(run).toHaveBeenLastCalledWith('/goal clear');
  });

  it('受阻：写原因，能恢复；这里不能继续这段对话时只显示、没有按钮', async () => {
    const run = vi.fn(async () => undefined);
    const goal = {
      objective: '核对三份合同',
      phase: 'blocked' as const,
      blockedReason: '额度用完了，长任务目标先停下',
      rounds: 5,
      maxRounds: 20,
    };
    const first = renderWithApp(<GoalPanel goal={goal} run={run} />);
    expect(screen.getByText('受阻')).toBeInTheDocument();
    expect(screen.getByText('原因：额度用完了，长任务目标先停下')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '恢复' }));
    expect(run).toHaveBeenCalledWith('/goal resume');
    first.unmount();
    renderWithApp(<GoalPanel goal={goal} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('进行中但自动续跑关着（对话重新打开过）：写「待继续」，给「恢复」不给「暂停」', async () => {
    const run = vi.fn(async () => undefined);
    renderWithApp(
      <GoalPanel
        goal={{ objective: '核对三份合同', phase: 'active', rounds: 3, maxRounds: 20, armed: false }}
        run={run}
      />,
    );
    expect(screen.getByText('待继续')).toBeInTheDocument();
    expect(screen.getByText(/自动接着做先停下了/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '暂停' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '恢复' }));
    expect(run).toHaveBeenCalledWith('/goal resume');
  });

  it('自动接着做的那一轮：显示成「长任务目标」的通知，不当成人发的话', async () => {
    renderWithApp(
      <TriggerNotice
        trigger={{ id: 't1', source: 'goal', text: '第 2 轮（最多 20 轮）：核对三份合同', time: 1 }}
      />,
    );
    expect(screen.getByRole('region', { name: '长任务目标发起的这一轮' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /长任务目标/ }));
    expect(screen.getByText('第 2 轮（最多 20 轮）：核对三份合同')).toBeInTheDocument();
  });
});
