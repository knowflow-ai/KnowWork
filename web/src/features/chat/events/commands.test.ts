import { beforeEach, describe, expect, it } from 'vitest';
import { chatReducer, initialChatState, rebuild, reduceWire } from './reducer';
import { ev, resetClock, wire } from './fixtures';
import { selectTimeline } from './selectors';
import type { ChatState, WireEvent } from './types';

const fold = (events: readonly WireEvent[], from: ChatState = initialChatState): ChatState =>
  events.reduce(reduceWire, from);

const commandRun = (id: string, item: string, name: string, args?: string) =>
  wire({
    id,
    type: 'item.started',
    run: null,
    item,
    kind: 'command',
    data: { name, ...(args ? { args } : {}) },
  });
const commandDone = (id: string, item: string, status: 'success' | 'error', text?: string) =>
  wire({
    id,
    type: 'item.completed',
    run: null,
    item,
    kind: 'command',
    data: { status, ...(text ? { text } : {}) },
  });

beforeEach(resetClock);

describe('/ 命令', () => {
  it('开始与结束合成一条，排在执行时已有的轮次之后，不进任何一轮', () => {
    const state = fold([
      ev.runStarted(1, 1),
      ev.message(2, '第一轮的回答'),
      ev.runCompleted(3, 1),
      commandRun('4', 'cmd-1', 'goal', ' 把合同核对完'),
      commandDone('5', 'cmd-1', 'success', 'Goal set'),
      ev.runStarted(6, 2),
      ev.message(7, '第二轮的回答', 2),
      ev.runCompleted(8, 2),
    ]);
    expect(state.commands).toEqual([
      expect.objectContaining({
        id: 'cmd-1',
        name: 'goal',
        args: ' 把合同核对完',
        status: 'success',
        text: 'Goal set',
      }),
    ]);
    expect(selectTimeline(state).map((entry) => entry.type)).toEqual(['run', 'command', 'run']);
    expect(state.runs['run-1']!.answer).toBe('第一轮的回答');
    expect(state.runs['run-2']!.answer).toBe('第二轮的回答');
  });

  it('本页刚执行的先显示；事件到了以事件为准，不重复；乱序重算时本页那条还在', () => {
    let state = chatReducer(initialChatState, {
      type: 'local/command',
      command: { id: 'cmd-2', name: 'goal', status: 'error', text: 'No goal', time: 1 },
    });
    expect(selectTimeline(state).filter((entry) => entry.type === 'command')).toHaveLength(1);
    state = rebuild(state.log, state);
    expect(selectTimeline(state).filter((entry) => entry.type === 'command')).toHaveLength(1);
    state = fold([commandRun('1', 'cmd-2', 'goal'), commandDone('2', 'cmd-2', 'error', 'No goal')], state);
    const shown = selectTimeline(state).filter((entry) => entry.type === 'command');
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ command: { id: 'cmd-2', status: 'error' } });
  });
});
