import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '../../../test/user';
import { initialChatState, reduceWire } from '../events/reducer';
import { ev, resetClock, wire } from '../events/fixtures';
import { formatClock, selectLastUserText, selectTimeline } from '../events/selectors';
import type { WireEvent } from '../events/types';
import { TriggerNotice } from './TriggerNotice';

const fold = (events: readonly WireEvent[]) => events.reduce(reduceWire, initialChatState);

const reminder = (id: string, text: string, run = 1) =>
  wire({
    id,
    type: 'item.completed',
    run,
    item: `s-${id}`,
    kind: 'message',
    data: { role: 'trigger', source: 'schedule', text },
    time: Date.UTC(2026, 10, 6, 1, 0),
  });

beforeEach(resetClock);

describe('定时任务发起的一轮', () => {
  it('记在这一轮上，时间线里排在回答前面；不算用户的话，重新生成不拿它', () => {
    const state = fold([ev.runStarted(1), reminder('2', '写日报'), ev.message(3, '日报写好了')]);
    expect(state.runs['run-1']!.trigger).toMatchObject({ source: 'schedule', text: '写日报' });
    expect(state.runs['run-1']!.asked).toEqual([]);
    expect(state.runs['run-1']!.answer).toBe('日报写好了');
    expect(selectTimeline(state).map((e) => e.type)).toEqual(['trigger', 'run']);
    expect(selectLastUserText(state)).toBeUndefined();
  });

  it('通知默认收起，只写由什么发起与时间；点开看任务内容', async () => {
    const time = new Date(2026, 10, 6, 9, 5).getTime();
    render(
      <TriggerNotice trigger={{ id: 's1', source: 'schedule', text: '写日报：列出今天的进展', time }} />,
    );
    const head = screen.getByRole('button', { name: /定时任务/ });
    expect(head).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('写日报：列出今天的进展')).toBeNull();
    await userEvent.click(head);
    expect(head).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('写日报：列出今天的进展')).toBeInTheDocument();
  });

  it('后台任务做完开的一轮同样显示；Agent 忙时塞进用户这一轮的通知不算发起', () => {
    const notice = (id: string, run: number) =>
      wire({
        id,
        type: 'item.completed',
        run,
        item: `n-${id}`,
        kind: 'message',
        data: { role: 'trigger', source: 'tool-jobs', text: 'bash npm test [status: exited]' },
      });
    const asked = wire({
      id: '4',
      type: 'item.completed',
      run: 2,
      item: 'u-4',
      kind: 'message',
      data: { role: 'user', text: '顺便看看测试' },
    });
    const state = fold([ev.runStarted(1), notice('2', 1), ev.runStarted(3, 2), asked, notice('5', 2)]);
    expect(state.runs['run-1']!.trigger?.source).toBe('tool-jobs');
    expect(state.runs['run-2']!.trigger).toBeUndefined();
    render(<TriggerNotice trigger={state.runs['run-1']!.trigger!} />);
    expect(screen.getByRole('button', { name: /后台任务完成/ })).toBeInTheDocument();
  });

  it('今天的只写几点几分，别的日子带上月日', () => {
    const now = new Date(2026, 10, 6, 18, 0);
    expect(formatClock(new Date(2026, 10, 6, 9, 5).getTime(), now)).toBe('09:05');
    expect(formatClock(new Date(2026, 10, 4, 21, 30).getTime(), now)).toBe('11月4日 21:30');
  });
});
