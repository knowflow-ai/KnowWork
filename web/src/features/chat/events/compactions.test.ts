import { beforeEach, describe, expect, it } from 'vitest';
import { initialChatState, reduceWire } from './reducer';
import { ev, resetClock, wire } from './fixtures';
import { selectTimeline } from './selectors';
import type { ChatState, WireEvent } from './types';

const fold = (events: readonly WireEvent[], from: ChatState = initialChatState): ChatState =>
  events.reduce(reduceWire, from);

const compaction = (
  id: string,
  type: 'item.started' | 'item.completed' | 'item.updated' | 'item.failed',
  data: Record<string, unknown>,
  run: number | null = null,
) => wire({ id, type, run, item: 'cp-1', kind: 'compaction', data });

beforeEach(resetClock);

describe('上下文压缩', () => {
  it('一轮里的自动压缩：合成一条，排在那一轮前面；摘要写好后的收尾不改结论', () => {
    const state = fold([
      ev.runStarted(1, 1),
      ev.message(2, '第一轮的回答'),
      ev.runCompleted(3, 1),
      ev.runStarted(4, 2),
      compaction('5', 'item.started', { status: 'running' }, 2),
      compaction('6', 'item.completed', {
        status: 'completed',
        items: 3,
        tokens: 51234,
        summary: '已核对两份',
      }),
      compaction('7', 'item.updated', { status: 'ended' }, 2),
      ev.message(8, '第二轮的回答', 2),
      ev.runCompleted(9, 2),
    ]);
    expect(state.compactions).toEqual([
      expect.objectContaining({
        id: 'cp-1',
        status: 'completed',
        items: 3,
        tokens: 51234,
        summary: '已核对两份',
      }),
    ]);
    expect(selectTimeline(state).map((entry) => entry.type)).toEqual(['run', 'compaction', 'run']);
  });

  it('轮次之间的（/compact）和命令一样按先后排在已有轮次之后；出错写原因；没写出摘要就结束的标成 ended', () => {
    const between = fold([
      ev.runStarted(1, 1),
      ev.runCompleted(2, 1),
      compaction('3', 'item.started', { status: 'running', manual: true }),
      compaction('4', 'item.failed', { status: 'error', error: '摘要模型没有回应' }),
    ]);
    expect(selectTimeline(between).map((entry) => entry.type)).toEqual(['run', 'compaction']);
    expect(between.compactions[0]).toMatchObject({ status: 'error', error: '摘要模型没有回应' });
    const empty = fold([
      compaction('1', 'item.started', { status: 'running' }),
      compaction('2', 'item.updated', { status: 'ended' }),
    ]);
    expect(empty.compactions[0]?.status).toBe('ended');
  });
});
