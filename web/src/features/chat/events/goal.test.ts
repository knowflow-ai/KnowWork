import { beforeEach, describe, expect, it } from 'vitest';
import { initialChatState, reduceWire } from './reducer';
import { resetClock, wire } from './fixtures';
import type { ChatState, WireEvent } from './types';

const fold = (events: readonly WireEvent[], from: ChatState = initialChatState): ChatState =>
  events.reduce(reduceWire, from);
const goal = (id: string, data: Record<string, unknown>) =>
  wire({ id, type: 'item.updated', run: null, item: 'goal', kind: 'goal', data });

beforeEach(resetClock);

describe('长任务目标', () => {
  it('整份的换上，只带轮数的合进去；卡住带原因；清除就没有', () => {
    let state = fold([
      goal('1', {
        operation: 'create',
        objective: '核对三份合同',
        phase: 'active',
        rounds: 0,
        max_rounds: 20,
      }),
      goal('2', { rounds: 1 }),
      goal('3', { rounds: 2 }),
    ]);
    expect(state.goal).toEqual({ objective: '核对三份合同', phase: 'active', rounds: 2, maxRounds: 20 });
    state = fold(
      [
        goal('4', {
          operation: 'block',
          objective: '核对三份合同',
          phase: 'blocked',
          rounds: 2,
          max_rounds: 20,
          blocked_reason: '额度用完了',
        }),
      ],
      state,
    );
    expect(state.goal).toMatchObject({ phase: 'blocked', blockedReason: '额度用完了', rounds: 2 });
    // 自动续跑关上了（会话重开）：记下来，面板写「待继续」；之后的整份目标沿用它，直到宿主再报
    state = fold([goal('5', { armed: false })], state);
    expect(state.goal?.armed).toBe(false);
    state = fold(
      [
        goal('6', {
          operation: 'edit',
          objective: '核对五份合同',
          phase: 'active',
          rounds: 2,
          max_rounds: 20,
        }),
      ],
      state,
    );
    expect(state.goal).toMatchObject({ objective: '核对五份合同', armed: false });
    state = fold([goal('7', { armed: true })], state);
    expect(state.goal?.armed).toBe(true);
    state = fold([goal('8', { cleared: true })], state);
    expect(state.goal).toBeUndefined();
    expect(fold([goal('9', { rounds: 3 })], state).goal).toBeUndefined();
  });
});
