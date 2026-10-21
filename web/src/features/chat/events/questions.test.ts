import { beforeEach, describe, expect, it } from 'vitest';
import { chatReducer, initialChatState, rebuild, reduceWire } from './reducer';
import { ev, resetClock } from './fixtures';
import { selectRunQuestions, selectRunState, summarizeProcess } from './selectors';
import type { ChatState, WireEvent } from './types';

const fold = (events: readonly WireEvent[], from: ChatState = initialChatState): ChatState =>
  events.reduce(reduceWire, from);

beforeEach(resetClock);

/** 模型先说一句、再提问，等回答。 */
function asking(): WireEvent[] {
  return [
    ev.runStarted(1, 1),
    ev.message(2, '我先确认一下导出格式。'),
    ev.toolStarted(3, 'call_1', 'ask_user_question', '{"questions":[]}'),
    ev.questionRequested(4, 'call_1'),
    ev.runWaitingInput('4.1', 'call_1'),
  ];
}

describe('提问', () => {
  it('提问出卡片、这一轮进入等待；提问不算步骤，前面那句话记成说明', () => {
    const state = fold(asking());
    const run = state.runs['run-1']!;
    expect(run.status).toBe('waiting');
    expect(run.steps).toEqual([]);
    expect(run.notes).toEqual(['我先确认一下导出格式。']);
    expect(run.answer).toBe('');
    expect(summarizeProcess(run)).toBe('等待你回答');
    const [question] = selectRunQuestions(state, 'run-1');
    expect(question).toMatchObject({ id: 'call_1', status: 'pending', continued: false });
    expect(question?.questions[0]).toEqual({
      id: 'fmt',
      question: '导出成什么格式？',
      header: '格式',
      detail: undefined,
      multiSelect: false,
      options: [{ label: 'Excel (Recommended)', description: '方便继续编辑' }, { label: 'PDF' }],
    });
    expect(selectRunState(state)).toBe('running');
  });

  it('回答后卡片显示回答、这一轮接着跑；工具结果也不生成步骤', () => {
    const state = fold([
      ...asking(),
      ev.questionAnswered(5, 'call_1', [{ id: 'fmt', selected: ['PDF'] }]),
      ev.toolCompleted(6, 'call_1', '{"answers":[]}'),
      ev.message(7, '好的，已按 PDF 导出。'),
      ev.runCompleted(8),
    ]);
    const run = state.runs['run-1']!;
    expect(run.steps).toEqual([]);
    expect(run.answer).toBe('好的，已按 PDF 导出。');
    expect(state.questions[0]).toMatchObject({
      status: 'answered',
      answers: [{ id: 'fmt', selected: ['PDF'] }],
    });
  });

  it('回答到达时这一轮从等待回到执行中', () => {
    const state = fold([...asking(), ev.questionAnswered(5, 'call_1', [{ id: 'fmt', selected: ['PDF'] }])]);
    expect(state.runs['run-1']!.status).toBe('running');
  });

  it('前台等待到期：卡片仍可答并标明 Agent 先继续；迟到回答落在新一轮，卡片留在原轮', () => {
    const state = fold([
      ...asking(),
      ev.questionContinued(5, 'call_1'),
      ev.message(6, '你确认后我再导出。'),
      ev.runCompleted(7),
      ev.runStarted(8, 2),
      ev.questionAnswered(9, 'call_1', [{ id: 'fmt', selected: [], custom: 'CSV' }], 2, true),
    ]);
    expect(selectRunQuestions(state, 'run-1')[0]).toMatchObject({
      status: 'answered',
      continued: true,
      answers: [{ id: 'fmt', selected: [], custom: 'CSV' }],
    });
    expect(selectRunQuestions(state, 'run-2')).toEqual([]);
  });

  it('关闭原因原样记下，不认识的按取消；已回答的不会被后到的关闭覆盖', () => {
    for (const [reason, expected] of [
      ['timeout', 'timeout'],
      ['interrupted', 'interrupted'],
      ['cancelled', 'cancelled'],
      ['奇怪', 'cancelled'],
    ] as const) {
      const state = fold([...asking(), ev.questionClosed(5, 'call_1', reason)]);
      expect(state.questions[0]).toMatchObject({ status: 'closed', closedReason: expected });
      expect(state.runs['run-1']!.status).toBe('running');
    }
    const answered = fold([
      ...asking(),
      ev.questionAnswered(5, 'call_1', [{ id: 'fmt', selected: ['PDF'] }]),
      ev.questionClosed(6, 'call_1', 'cancelled'),
    ]);
    expect(answered.questions[0]?.status).toBe('answered');
  });

  it('轮次结束不作废还能回答的提问（迟到回答会开新一轮）', () => {
    const state = fold([...asking(), ev.questionContinued(5, 'call_1'), ev.runCompleted(6)]);
    expect(state.questions[0]?.status).toBe('pending');
  });

  it('本页提交时后端说已结束：标成失效，乱序重算后仍是失效', () => {
    const expired = chatReducer(fold(asking()), { type: 'local/question-expired', questionId: 'call_1' });
    expect(expired.questions[0]).toMatchObject({ status: 'closed', closedReason: 'expired' });
    const rebuilt = rebuild(expired.log, expired);
    expect(rebuilt.questions[0]).toMatchObject({ status: 'closed', closedReason: 'expired' });
    expect(chatReducer(expired, { type: 'reset' }).expiredQuestions).toEqual([]);
  });

  it('重复帧幂等；坏数据与没有问题的提问被丢掉；不带轮次的提问挂到当前轮', () => {
    const twice = fold([...asking(), ev.questionRequested(4, 'call_1')]);
    expect(twice.questions).toHaveLength(1);
    const broken = fold([
      ev.runStarted(1),
      ev.questionRequested(2, 'bad', [{ id: 'x' }, { question: '没有 id' }, 'x' as never]),
    ]);
    expect(broken.questions).toEqual([]);
    const loose = fold([ev.runStarted(1), ev.questionRequested(2, 'call_9', undefined, null)]);
    expect(loose.questions[0]?.runId).toBe('run-1');
  });
});

describe('待办', () => {
  it('每轮一份清单，后写的整表替换先写的；todo_write 不算步骤', () => {
    const state = fold([
      ev.runStarted(1),
      ev.toolStarted(2, 'call_t1', 'todo_write', '{"todos":[]}'),
      ev.todos(3, [
        { content: '读取合同', status: 'in_progress' },
        { content: '生成台账', status: 'pending' },
      ]),
      ev.toolCompleted(4, 'call_t1', 'Updated todo list'),
      ev.todos(5, [
        { content: '读取合同', status: 'completed' },
        { content: '生成台账', status: 'in_progress' },
        { content: '坏状态', status: 'done' },
      ]),
      ev.runCompleted(6),
      ev.runStarted(7, 2),
    ]);
    expect(state.runs['run-1']!.steps).toEqual([]);
    expect(state.runs['run-1']!.todos).toEqual([
      { content: '读取合同', status: 'completed' },
      { content: '生成台账', status: 'in_progress' },
    ]);
    expect(state.runs['run-2']!.todos).toBeUndefined();
  });
});
