import { describe, expect, it } from 'vitest';
import { formatDuration, hasProcess, selectRunSearch, summarizeProcess } from './selectors';
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

  it('hasProcess：运行中或有细节时显示', () => {
    expect(hasProcess(run())).toBe(true);
    expect(hasProcess(run({ endedAt: 1 }))).toBe(false);
    expect(hasProcess(run({ endedAt: 1, notes: ['x'] }))).toBe(true);
    expect(hasProcess(run({ endedAt: 1, problems: ['x'] }))).toBe(true);
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
