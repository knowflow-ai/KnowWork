import { describe, expect, it, vi } from 'vitest';
import { onTurnEnd, turnEnded } from './turnEnds';

describe('一轮结束的通知（按对话）', () => {
  it('只通知这段对话的；取消订阅后不再通知；一个出错不影响别的', () => {
    const mine = vi.fn();
    const other = vi.fn();
    const broken = vi.fn(() => {
      throw new Error('坏了');
    });
    const stopMine = onTurnEnd('c1', mine);
    const stopBroken = onTurnEnd('c1', broken);
    const stopOther = onTurnEnd('c2', other);
    turnEnded('c1');
    expect(mine).toHaveBeenCalledTimes(1);
    expect(broken).toHaveBeenCalledTimes(1);
    expect(other).not.toHaveBeenCalled();
    stopMine();
    turnEnded('c1');
    expect(mine).toHaveBeenCalledTimes(1);
    expect(broken).toHaveBeenCalledTimes(2);
    stopBroken();
    stopOther();
    turnEnded('c2');
    expect(other).not.toHaveBeenCalled();
  });
});
