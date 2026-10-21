import { describe, expect, it } from 'vitest';
import { formatDuration, hasProcess, summarizeProcess } from './selectors';
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
