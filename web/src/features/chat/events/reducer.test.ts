import { beforeEach, describe, expect, it } from 'vitest';
import { chatReducer, initialChatState, reduceWire } from './reducer';
import { ev, resetClock, wire } from './fixtures';
import {
  selectLastUserText,
  selectPendingApprovals,
  selectRunApprovals,
  selectRunState,
  selectTimeline,
} from './selectors';
import type { ChatState, WireEvent } from './types';

const fold = (events: readonly WireEvent[], from: ChatState = initialChatState): ChatState =>
  events.reduce(reduceWire, from);

/** 去掉日志字段，只比较界面状态。 */
const view = (state: ChatState) => ({ ...state, log: undefined });

beforeEach(resetClock);

/** 一帧（assistant/message）投影出思考与正文两条，id 分别是 3.0 与 3。 */
function happyRun(): WireEvent[] {
  return [
    ev.runStarted(1, 1, 1000),
    ev.thinking('2.0', '先看看文件'),
    ev.message(2, '我先读取文件'),
    ev.toolStarted(3, 'c1', 'read_file'),
    ev.toolCompleted(4, 'c1', '文件内容'),
    ev.message(5, '总结如下：一切正常。'),
    ev.runCompleted(6, 1, 39000),
  ];
}

describe('chatReducer 正常轮次', () => {
  it('把一整轮折叠成说明、执行步骤、思考与回答', () => {
    const state = fold(happyRun());
    const run = state.runs['run-1']!;
    expect(state.runOrder).toEqual(['run-1']);
    expect(run.answer).toBe('总结如下：一切正常。');
    expect(run.notes).toEqual(['我先读取文件']);
    expect(run.thinking).toEqual(['先看看文件']);
    expect(run.steps[0]).toMatchObject({
      callId: 'c1',
      name: 'read_file',
      status: 'done',
      resultPreview: '文件内容',
    });
    expect(run.endReason).toEqual({ kind: 'completed' });
    expect(run.status).toBe('completed');
    expect(run.endedAt! - run.startedAt!).toBe(38000);
    expect(state.lastId).toBe('6');
    expect(selectRunState(state)).toBe('idle');
  });

  it('轮次进行中时运行状态是 running', () => {
    const state = fold(happyRun().slice(0, 4));
    expect(selectRunState(state)).toBe('running');
    expect(state.openRun).toBe('run-1');
  });

  it('同一轮多段回答用空行拼接；非助手消息与空文本忽略', () => {
    const state = fold([
      ev.runStarted(1),
      ev.message(2, '第一段'),
      ev.message(3, '第二段'),
      wire({
        id: '4',
        type: 'item.completed',
        item: 'u',
        kind: 'message',
        data: { role: 'user', text: 'x' },
      }),
      ev.message(5, '   '),
    ]);
    expect(state.runs['run-1']!.answer).toBe('第一段\n\n第二段');
  });

  it('工具失败时取返回内容的第一行作原因，没有内容才给通用说明', () => {
    const state = fold([
      ev.runStarted(1),
      ev.toolStarted(2, 'c1', 'web', { q: 'x' }),
      ev.toolFailed(3, 'c1', '仓库还没有被收录\n请稍后再试'),
      ev.toolStarted(4, 'c2', 'web', {}),
      ev.toolFailed(5, 'c2', ''),
      ev.toolStarted(6, 'c3', 'web', {}),
      ev.toolFailed(7, 'c3', '长'.repeat(80)),
    ]);
    const [first, second, third] = state.runs['run-1']!.steps;
    expect(first).toMatchObject({ status: 'error', errorReason: '仓库还没有被收录', arguments: '{"q":"x"}' });
    expect(second!.errorReason).toBe('工具执行没有成功');
    expect(third!.errorReason).toBe(`${'长'.repeat(60)}…`);
  });

  it('没有 run.started 也能接住后续事件', () => {
    const state = fold([ev.toolStarted(1, 'c1', 'web', '', 3), ev.toolCompleted(2, 'c1', 'ok', 3)]);
    expect(state.runs['run-3']!.steps[0]!.status).toBe('done');
    expect(state.runOrder).toEqual(['run-3']);
  });

  it('不带轮次的条目挂到正在进行的那一轮', () => {
    const state = fold([
      ev.runStarted(1, 2),
      wire({ id: '2', type: 'item.started', run: null, item: 'c', kind: 'tool', data: { name: 'x' } }),
    ]);
    expect(state.runs['run-2']!.steps).toHaveLength(1);
  });

  it('子任务显示为步骤并记录结果', () => {
    const state = fold([
      ev.runStarted(1),
      ev.subagent(2, 'child-1'),
      ev.subagent(3, 'child-1', true, 'completed'),
      ev.subagent(4, 'child-2'),
      ev.subagent(5, 'child-2', true, 'failed'),
    ]);
    expect(state.runs['run-1']!.steps.map((s) => [s.kind, s.name, s.status])).toEqual([
      ['subagent', '子任务', 'done'],
      ['subagent', '子任务', 'error'],
    ]);
  });

  it('事件丢失与技能门失败记成可读的问题，重复的不再追加', () => {
    const state = fold([
      ev.runStarted(1),
      ev.problem(2, 'events_gap'),
      ev.problem(3, 'events_gap'),
      ev.problem(4, 'x'),
    ]);
    expect(state.runs['run-1']!.problems).toEqual(['部分执行过程没有记录下来', '执行中出现了问题']);
  });
});

describe('chatReducer 幂等与乱序', () => {
  it('重复帧只应用一次，并返回同一个对象', () => {
    const events = happyRun();
    const once = fold(events);
    expect(view(fold([...events, ...events]))).toEqual(view(once));
    expect(reduceWire(once, events[3]!)).toBe(once);
  });

  it('乱序到达的结果与按序到达一致', () => {
    const events = happyRun();
    const shuffled = [
      events[0],
      events[2],
      events[1],
      events[4],
      events[3],
      events[6],
      events[5],
    ] as WireEvent[];
    const disordered = fold(shuffled);
    expect(view(disordered)).toEqual(view(fold(events)));
    expect(disordered.log.map((e) => e.id)).toEqual(['1', '2.0', '2', '3', '4', '5', '6']);
  });

  it('断线续传：从中间接上与一次收完相同', () => {
    const events = happyRun();
    const resumed = fold(events.slice(2), fold(events.slice(0, 3)));
    expect(view(resumed)).toEqual(view(fold(events)));
  });

  it('不认识的事件类型与条目类型被忽略', () => {
    const state = fold([
      wire({ id: '1', type: 'kernel.native' }),
      wire({ id: '2', type: 'item.started', item: 'x', kind: 'artifact' }),
      wire({ id: '3', type: 'item.delta', item: 'x', kind: 'message' }),
      wire({ id: '4', type: 'item.completed', kind: 'message', data: { text: '无条目 id' } }),
      wire({ id: '5', type: 'run.started', run: null }),
    ]);
    expect(state.runOrder).toEqual([]);
    expect(state.lastId).toBe('5');
  });
});

describe('chatReducer 结束原因', () => {
  it('取消后挂着的步骤标为未完成，挂着的审批作废', () => {
    const state = fold([
      ev.runStarted(1),
      ev.toolStarted(2, 'c1', 'bash'),
      ev.approvalRequested(3, 'ap1', 'bash', 'c1'),
      ev.runCancelled(4),
    ]);
    expect(state.runs['run-1']!.steps[0]).toMatchObject({ status: 'error', errorReason: '已停止' });
    expect(state.approvals[0]!.status).toBe('cancelled');
    expect(selectRunState(state)).toBe('idle');
  });

  it.each([
    ['UNKNOWN', '这一轮没有完成，请稍后重试'],
    ['blocked', '这一轮被拦截，没有继续执行'],
    ['skill_gate_failed', '没有完成技能要求的收尾步骤'],
  ])('失败代码 %s 给出可读说明', (code, message) => {
    const state = fold([ev.runStarted(1), ev.runFailed(2, 1, code)]);
    expect(state.runs['run-1']!.endReason).toEqual({ kind: 'failed', message });
    expect(state.runs['run-1']!.status).toBe('failed');
  });

  it('回答过长被截断时说明', () => {
    const state = fold([ev.runStarted(1), ev.runCompleted(2, 1, undefined, { reason: 'max_tokens' })]);
    expect(state.runs['run-1']!.endReason?.message).toBe('回答过长，已被截断');
  });

  it('只有结束事件时也建立轮次', () => {
    const state = fold([ev.runFailed(1, 2)]);
    expect(state.runs['run-2']!.endReason?.kind).toBe('failed');
  });
});

describe('chatReducer 审批', () => {
  const asking = () => [
    ev.runStarted(1),
    ev.toolStarted(2, 'c1', 'write_file', '{"path":"报告.docx"}'),
    ev.approvalRequested('3.0', 'ap1', 'write_file', 'c1'),
    ev.runWaiting(3, 'ap1'),
  ];

  it('请求审批后进入等待，步骤标为待批准', () => {
    const state = fold(asking());
    expect(selectRunState(state)).toBe('waiting-approval');
    expect(selectPendingApprovals(state)).toHaveLength(1);
    expect(selectRunApprovals(state, 'run-1')[0]).toMatchObject({ id: 'ap1', reason: '需要写入文件' });
    expect(state.runs['run-1']!.steps[0]!.status).toBe('awaiting-approval');
    expect(state.runs['run-1']!.status).toBe('waiting');
  });

  it('批准后步骤与轮次继续运行', () => {
    const state = fold([...asking(), ev.approvalResolved(4, 'ap1', 'allowed-once')]);
    expect(state.approvals[0]!.status).toBe('allowed');
    expect(state.runs['run-1']!.steps[0]!.status).toBe('running');
    expect(state.runs['run-1']!.status).toBe('running');
    expect(selectRunState(state)).toBe('running');
  });

  it('拒绝后步骤标为已拒绝，工具报错也不覆盖', () => {
    const state = fold([
      ...asking(),
      ev.approvalResolved(4, 'ap1', 'rejected'),
      ev.toolFailed(5, 'c1', '用户拒绝'),
    ]);
    expect(state.approvals[0]!.status).toBe('rejected');
    expect(state.runs['run-1']!.steps[0]!.status).toBe('rejected');
  });

  it('重复请求与重复结果幂等，未知审批的结果被忽略', () => {
    const state = fold([
      ...asking(),
      ev.approvalRequested(4, 'ap1', 'write_file', 'c1'),
      ev.approvalResolved(5, 'ap1', 'allowed-once'),
      ev.approvalResolved(6, 'ap1', 'rejected'),
      ev.approvalResolved(7, 'nope', 'rejected'),
      ev.approvalResolved(8, 'x', 'cancelled'),
    ]);
    expect(state.approvals).toHaveLength(1);
    expect(state.approvals[0]!.status).toBe('allowed');
  });

  it('审批先于工具调用到达时，步骤直接是待批准；超时按作废', () => {
    const before = fold([
      ev.runStarted(1),
      ev.approvalRequested(2, 'ap1', 'bash', 'c9'),
      ev.toolStarted(3, 'c9', 'bash'),
    ]);
    expect(before.runs['run-1']!.steps[0]!.status).toBe('awaiting-approval');
    expect(fold([ev.approvalResolved(4, 'ap1', 'timeout')], before).approvals[0]!.status).toBe('cancelled');
  });

  it('不带轮次的审批也能显示', () => {
    const state = fold([ev.approvalRequested(1, 'ap1', 'bash', undefined, null)]);
    expect(state.approvals[0]).toMatchObject({ runId: undefined, toolName: 'bash', callId: undefined });
  });
});

describe('chatReducer 本地发送的消息', () => {
  it('按发送时的轮数摆进时间线，重算后位置不变', () => {
    let state = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: '你好',
      mode: 'followup',
    });
    state = fold([ev.runStarted(1), ev.message(2, '答一')], state);
    state = chatReducer(state, { type: 'local/sent', localId: 'l2', text: '再问', mode: 'followup' });
    state = fold([ev.runCompleted(4), ev.runStarted(5, 2)], state);
    const expected = [
      { type: 'user', message: expect.objectContaining({ text: '你好' }) },
      { type: 'run', runId: 'run-1' },
      { type: 'user', message: expect.objectContaining({ text: '再问' }) },
      { type: 'run', runId: 'run-2' },
    ];
    expect(selectTimeline(state)).toEqual(expected);
    // 乱序帧触发重算，消息位置不变
    expect(selectTimeline(reduceWire(state, ev.message(3, '补')))).toEqual(expected);
  });

  it('送达后可用于重新生成，失败时撤回', () => {
    const sent = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: 'a',
      mode: 'steer',
    });
    expect(selectLastUserText(sent)).toBeUndefined();
    expect(selectLastUserText(chatReducer(sent, { type: 'local/delivered', localId: 'l1' }))).toBe('a');
    expect(chatReducer(sent, { type: 'local/failed', localId: 'l1' }).sent).toEqual([]);
  });

  it('批量应用与逐帧应用一致，reset 清空，不修改入参', () => {
    const events = happyRun();
    const batched = chatReducer(initialChatState, { type: 'wire/batch', events });
    expect(view(batched)).toEqual(view(fold(events)));
    expect(chatReducer(batched, { type: 'reset' })).toBe(initialChatState);
    const partial = fold(events.slice(0, 4));
    const snapshot = JSON.stringify(partial);
    reduceWire(partial, events[4]!);
    reduceWire(partial, events[1]!);
    expect(JSON.stringify(partial)).toBe(snapshot);
  });
});

describe('chatReducer 产物', () => {
  const files = [{ id: 'art_1', name: '总结.docx', size: 10, runId: 'run-1', url: '/x' }];

  it('整体替换产物列表；乱序帧触发重算时不丢；reset 清空', () => {
    const loaded = chatReducer(fold(happyRun()), { type: 'artifacts/loaded', artifacts: files });
    expect(loaded.artifacts).toEqual(files);
    const reordered = reduceWire(loaded, ev.thinking('1.0', '插回来的思考'));
    expect(reordered.artifacts).toEqual(files);
    expect(chatReducer(reordered, { type: 'artifacts/loaded', artifacts: [] }).artifacts).toEqual([]);
    expect(chatReducer(loaded, { type: 'reset' }).artifacts).toEqual([]);
  });
});

describe('引用目录', () => {
  const sources = { 1: { title: '采购合同.pdf', page: 3 } };

  it('整体替换；乱序帧触发重算时不丢；reset 回到「还没拉到」', () => {
    const loaded = chatReducer(fold(happyRun()), { type: 'citations/loaded', citations: sources });
    expect(loaded.citations).toEqual(sources);
    const reordered = reduceWire(loaded, ev.thinking('1.0', '插回来的思考'));
    expect(reordered.citations).toEqual(sources);
    expect(chatReducer(loaded, { type: 'reset' }).citations).toBeUndefined();
  });
});

describe('历史提问', () => {
  it('事件流里的提问挂在它那一轮前面，刷新后也在；不算进回答', () => {
    const state = fold([
      ev.runStarted(1, 1),
      ev.asked(2, '宿舍退宿有什么规定？', 1),
      ev.message(3, '离职两天内退宿', 1),
      ev.runCompleted(4, 1),
      ev.runStarted(5, 2),
      ev.asked(6, '押金怎么退？', 2),
    ]);
    expect(state.runs['run-1']?.answer).toBe('离职两天内退宿');
    expect(selectTimeline(state)).toEqual([
      { type: 'user', message: expect.objectContaining({ text: '宿舍退宿有什么规定？', status: 'sent' }) },
      { type: 'run', runId: 'run-1' },
      { type: 'user', message: expect.objectContaining({ text: '押金怎么退？' }) },
      { type: 'run', runId: 'run-2' },
    ]);
    expect(selectLastUserText(state)).toBe('押金怎么退？');
  });

  it('本页刚发出的消息等事件流里的同一条到了就合并，不显示两遍', () => {
    const sent = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: '你好',
      mode: 'followup',
    });
    const delivered = chatReducer(sent, { type: 'local/delivered', localId: 'l1' });
    expect(selectTimeline(delivered)).toEqual([
      { type: 'user', message: expect.objectContaining({ text: '你好' }) },
    ]);
    const echoed = fold([ev.runStarted(1, 1), ev.asked(2, '你好', 1)], delivered);
    const users = selectTimeline(echoed).filter((entry) => entry.type === 'user');
    expect(users).toHaveLength(1);
  });

  it('拿到消息 id 后按 id 合并：回显的原话截短过（超长消息只回显前面一段）也只显示一遍', () => {
    const long = '长'.repeat(9000);
    const sent = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: long,
      mode: 'followup',
    });
    const delivered = chatReducer(sent, { type: 'local/delivered', localId: 'l1', messageId: 'msg-1' });
    const echoed = fold([ev.runStarted(1, 1), ev.asked(2, long.slice(0, 8000), 1, 'msg-1')], delivered);
    expect(selectTimeline(echoed).filter((entry) => entry.type === 'user')).toHaveLength(1);
  });

  it('发送请求还没返回、回显先到：原话首尾带空白也认得出是同一条', () => {
    const sent = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: '帮我把上面的图转成 html ',
      mode: 'followup',
    });
    const echoed = fold([ev.runStarted(1, 1), ev.asked(2, '帮我把上面的图转成 html', 1, 'msg-1')], sent);
    expect(selectTimeline(echoed).filter((entry) => entry.type === 'user')).toHaveLength(1);
    const delivered = chatReducer(echoed, { type: 'local/delivered', localId: 'l1', messageId: 'msg-1' });
    expect(selectTimeline(delivered).filter((entry) => entry.type === 'user')).toHaveLength(1);
  });

  it('同一句话再问一遍（重新生成）：已经发出、还没回显的这条不会被上一轮的同一句顶掉', () => {
    const history = fold([ev.runStarted(1, 1), ev.asked(2, '继续', 1, 'msg-1'), ev.runCompleted(3, 1)]);
    const sent = chatReducer(history, { type: 'local/sent', localId: 'l2', text: '继续', mode: 'followup' });
    const delivered = chatReducer(sent, { type: 'local/delivered', localId: 'l2', messageId: 'msg-2' });
    const ids = (state: typeof delivered) =>
      selectTimeline(state).flatMap((entry) => (entry.type === 'user' ? [entry.message.localId] : []));
    expect(ids(delivered)).toEqual(['msg-1', 'l2']);
    expect(ids(fold([ev.runStarted(4, 2), ev.asked(5, '继续', 2, 'msg-2')], delivered))).toEqual([
      'msg-1',
      'msg-2',
    ]);
  });

  it('同一个问题问两次，两条都显示', () => {
    const state = fold([
      ev.runStarted(1, 1),
      ev.asked(2, '再说一遍', 1),
      ev.runCompleted(3, 1),
      ev.runStarted(4, 2),
      ev.asked(5, '再说一遍', 2),
    ]);
    expect(selectTimeline(state).filter((entry) => entry.type === 'user')).toHaveLength(2);
  });
});

describe('执行过程的先后顺序', () => {
  it('知识检索结束时记下业务服务给的检索摘要，格式不对的不要', () => {
    const search = { documents: ['b:d1'], conditions: ['年度 = 2025'], removed: ['部门 = 财务部'] };
    const state = fold([
      ev.runStarted(1, 1),
      wire({
        id: '2',
        type: 'item.started',
        run: 1,
        item: 'c1',
        kind: 'tool',
        data: { name: 'knowledge_search' },
      }),
      wire({
        id: '3',
        type: 'item.completed',
        run: 1,
        item: 'c1',
        kind: 'tool',
        data: { text: '{}', search },
      }),
      wire({
        id: '4',
        type: 'item.started',
        run: 1,
        item: 'c2',
        kind: 'tool',
        data: { name: 'knowledge_search' },
      }),
      wire({
        id: '5',
        type: 'item.completed',
        run: 1,
        item: 'c2',
        kind: 'tool',
        data: { text: '{}', search: 'x' },
      }),
    ]);
    const [first, second] = state.runs['run-1']?.steps ?? [];
    expect(first?.search).toEqual(search);
    expect(second?.search).toBeUndefined();
  });

  it('思考、说明与步骤按发生先后交错记下', () => {
    const state = fold([
      ev.runStarted(1, 1),
      ev.thinking('2.0', '先查知识库', 1),
      ev.message(2, '我来检索一下', 1),
      wire({
        id: '3',
        type: 'item.started',
        run: 1,
        item: 'c1',
        kind: 'tool',
        data: { name: 'knowledge_search' },
      }),
      wire({ id: '4', type: 'item.completed', run: 1, item: 'c1', kind: 'tool', data: { text: '{}' } }),
      ev.thinking('5.0', '没找到依据', 1),
      ev.message(5, '知识库里没有相关内容', 1),
    ]);
    expect(state.runs['run-1']?.trail).toEqual([
      { type: 'thinking', text: '先查知识库' },
      { type: 'note', text: '我来检索一下' },
      { type: 'step', callId: 'c1' },
      { type: 'thinking', text: '没找到依据' },
    ]);
    expect(state.runs['run-1']?.answer).toBe('知识库里没有相关内容');
  });
});
