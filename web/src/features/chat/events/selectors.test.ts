import { describe, expect, it } from 'vitest';
import { ev } from './fixtures';
import { chatReducer, initialChatState, reduceWire } from './reducer';
import {
  formatDuration,
  hasProcess,
  selectAwaitingReply,
  selectDiscardedSent,
  selectFileNames,
  selectRunSearch,
  selectTimeline,
  summarizeProcess,
} from './selectors';
import type { RunView, StepView } from './types';

const run = (patch: Partial<RunView> = {}): RunView => ({
  runId: 'run-1',
  status: 'running',
  steps: [],
  notes: [],
  thinking: [],
  answer: '',
  problems: [],
  asked: [],
  trail: [],
  ...patch,
});

const step = (status: StepView['status'], name = 'read_file'): StepView => ({
  callId: `${name}-${status}`,
  kind: 'tool',
  name,
  arguments: '',
  status,
});

describe('formatDuration', () => {
  it.each([
    [0, '0 秒'],
    [38_000, '38 秒'],
    [120_000, '2 分钟'],
    [125_400, '2 分 5 秒'],
    [-5, '0 秒'],
  ])('%i ms → %s', (ms, value) => expect(formatDuration(ms)).toBe(value));
});

describe('summarizeProcess', () => {
  it('结束后显示步骤数与用时', () => {
    const done = run({
      startedAt: 0,
      endedAt: 38_000,
      steps: [step('done'), step('done', 'b'), step('error', 'c'), step('done', 'd')],
    });
    expect(summarizeProcess(done)).toBe('已完成 4 个步骤 · 用时 38 秒');
    expect(summarizeProcess(run({ endedAt: 5 }))).toBe('已完成');
  });

  it('运行中显示当前步骤', () => {
    expect(summarizeProcess(run())).toBe('思考中');
    expect(summarizeProcess(run({ steps: [step('done'), step('running', 'web_search')] }))).toBe(
      '正在执行：web_search',
    );
    expect(summarizeProcess(run({ steps: [step('awaiting-approval', 'write')] }))).toBe('等待批准：write');
  });

  it('模型还在写工具的参数（工具没开始跑）时说它在做什么，不是一直「思考中」', () => {
    expect(summarizeProcess(run(), ['write'])).toBe('正在写入文件');
    expect(summarizeProcess(run(), ['web_search'])).toBe('正在执行：web_search');
    expect(summarizeProcess(run({ steps: [step('running', 'read')] }), ['write'])).toBe('正在读取文件');
  });

  it('模型在写待办、提问这类内部工具时不露工具名（它们本来就不算步骤），仍是「思考中」', () => {
    expect(summarizeProcess(run(), ['todo_write'])).toBe('思考中');
    expect(summarizeProcess(run(), ['write', 'ask_user_question'])).toBe('正在写入文件');
  });

  it('hasProcess：运行中或有细节时显示', () => {
    expect(hasProcess(run())).toBe(true);
    expect(hasProcess(run({ endedAt: 1 }))).toBe(false);
    expect(hasProcess(run({ endedAt: 1, notes: ['x'] }))).toBe(true);
    expect(hasProcess(run({ endedAt: 1, problems: ['x'] }))).toBe(true);
    expect(hasProcess(run({ endedAt: 1, thinking: ['想'] }))).toBe(true);
  });

  it('只有思考没有步骤时写「已思考」', () => {
    expect(summarizeProcess(run({ startedAt: 0, endedAt: 12_000, thinking: ['想'] }))).toBe(
      '已思考 · 用时 12 秒',
    );
  });
});

describe('selectRunSearch', () => {
  const searched = (callId: string, documents: string[], conditions: string[], removed: string[] = []) => ({
    ...step('done', 'knowledge_search'),
    callId,
    search: { documents, conditions, removed },
  });

  it('合并这一轮各次检索的条件与文档，撤掉的不算用上的', () => {
    const view = run({
      steps: [
        searched('a', ['s:1', 's:2'], ['年度 = 2025', '部门 = 财务部'], ['部门 = 财务部']),
        searched('b', ['s:2'], ['年度 = 2025', '标签：已签']),
        step('done'),
      ],
    });
    expect(selectRunSearch(view)).toEqual({
      conditions: ['年度 = 2025', '标签：已签'],
      removed: ['部门 = 财务部'],
      documents: 2,
    });
  });

  it('没检索或检索没带条件时没有', () => {
    expect(selectRunSearch(run({ steps: [step('done')] }))).toBeUndefined();
    expect(selectRunSearch(run({ steps: [searched('a', ['s:1'], [])] }))).toBeUndefined();
  });
});

describe('selectAwaitingReply', () => {
  it('刚发出、这一轮还没开始时显示「思考中」；这一轮开始、发送失败后不再显示', () => {
    const sent = chatReducer(initialChatState, {
      type: 'local/sent',
      localId: 'l1',
      text: '你好',
      mode: 'followup',
    });
    expect(selectAwaitingReply(sent)).toBe(true);
    const delivered = chatReducer(sent, { type: 'local/delivered', localId: 'l1', messageId: 'm1' });
    expect(selectAwaitingReply(delivered)).toBe(true);
    expect(selectAwaitingReply(reduceWire(delivered, ev.runStarted(1)))).toBe(false);
    expect(selectAwaitingReply(chatReducer(sent, { type: 'local/failed', localId: 'l1' }))).toBe(false);
  });

  it('一轮还在跑时发的消息排着队、随停止一起取消了：不再「思考中」，也不在时间线上，交给输入框放回原文', () => {
    const running = reduceWire(initialChatState, ev.runStarted(1, 5));
    const queued = chatReducer(
      chatReducer(running, {
        type: 'local/sent',
        localId: 'l2',
        text: '2025 年整体良率走势',
        mode: 'followup',
      }),
      { type: 'local/delivered', localId: 'l2', messageId: 'm2' },
    );
    const stopped = reduceWire(reduceWire(queued, ev.discarded(2, 'm2')), ev.runCancelled(3, 5));
    expect(selectAwaitingReply(stopped)).toBe(false);
    expect(selectTimeline(stopped).filter((e) => e.type === 'user')).toEqual([]);
    expect(selectDiscardedSent(stopped).map((m) => m.text)).toEqual(['2025 年整体良率走势']);
  });

  it('取消的事件先到、发送接口的消息 id 后到，一样认得出', () => {
    const sent = chatReducer(reduceWire(initialChatState, ev.runStarted(1, 5)), {
      type: 'local/sent',
      localId: 'l3',
      text: '再问一句',
      mode: 'followup',
    });
    const early = reduceWire(sent, ev.discarded(2, 'm3'));
    expect(selectDiscardedSent(early)).toEqual([]);
    const late = chatReducer(early, { type: 'local/delivered', localId: 'l3', messageId: 'm3' });
    expect(selectDiscardedSent(late).map((m) => m.localId)).toEqual(['l3']);
    expect(selectAwaitingReply(reduceWire(late, ev.runCancelled(3, 5)))).toBe(false);
  });
});

describe('对话里出现过的文件（给提前起编辑器看）', () => {
  it('提问带的附件、交付的文件、刚发出还没回显的附件，去重、按出现先后', () => {
    const events = [
      ev.runStarted(1),
      ev.asked(2, '看看', 1, 'u2', [{ name: '合同.docx', size: 1 }]),
      ev.delivered(3, [{ path: '/sessions/c1/output/报告.pptx' }]),
      ev.runStarted(4, 2),
      ev.asked(5, '再看', 2, 'u5', [{ name: '合同.docx', size: 1 }]),
    ];
    const replayed = events.reduce(reduceWire, initialChatState);
    const sent = chatReducer(replayed, {
      type: 'local/sent',
      localId: 'l1',
      text: '还有这个',
      mode: 'followup',
      attachments: [{ name: '预算.xlsx', size: 2 }],
    });
    expect(selectFileNames(sent)).toEqual(['合同.docx', '/sessions/c1/output/报告.pptx', '预算.xlsx']);
    expect(selectFileNames(initialChatState)).toEqual([]);
  });
});
