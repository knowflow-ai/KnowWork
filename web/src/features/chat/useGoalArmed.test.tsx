import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { testClient } from '../../test/render';
import type { GoalView } from './events/types';
import { useGoalArmed } from './useGoalArmed';

const GOAL: GoalView = { objective: '核对三份合同', phase: 'active', rounds: 1, maxRounds: 5 };

function wrapper() {
  const client = testClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('useGoalArmed', () => {
  it('目标进行中时向来源现取自动续跑开没开', async () => {
    const fetchArmed = vi.fn(async () => false);
    const { result } = renderHook(() => useGoalArmed('server', 'c1', GOAL, fetchArmed, false), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current).toBe(false));
    expect(fetchArmed).toHaveBeenCalledWith('c1');
  });

  it('目标变了（多跑了一轮）重新问', async () => {
    const fetchArmed = vi.fn(async () => true);
    const { result, rerender } = renderHook(
      ({ goal }: { goal: GoalView }) => useGoalArmed('server', 'c1', goal, fetchArmed, false),
      { wrapper: wrapper(), initialProps: { goal: GOAL } },
    );
    await waitFor(() => expect(result.current).toBe(true));
    rerender({ goal: { ...GOAL, rounds: 2 } });
    await waitFor(() => expect(fetchArmed).toHaveBeenCalledTimes(2));
  });

  it('没在进行中、或来源不给，不问也不知道', () => {
    const fetchArmed = vi.fn(async () => false);
    const paused = renderHook(
      () => useGoalArmed('server', 'c1', { ...GOAL, phase: 'paused' }, fetchArmed, false),
      { wrapper: wrapper() },
    );
    expect(paused.result.current).toBeUndefined();
    const local = renderHook(() => useGoalArmed('local', 'c1', GOAL, undefined, false), {
      wrapper: wrapper(),
    });
    expect(local.result.current).toBeUndefined();
    expect(fetchArmed).not.toHaveBeenCalled();
  });
});
