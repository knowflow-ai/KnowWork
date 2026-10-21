import { afterEach, describe, expect, it, vi } from 'vitest';
import * as d from './debug';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/conversations/c1/debug';

describe('运行调试接口', () => {
  it('解析一轮的步骤、工具调用、用量与子 Agent', async () => {
    routeFetch({
      [`GET ${B}/turns/2`]: jsonResponse({
        summary: {
          turn: 2,
          status: 'completed',
          request: '导出 word',
          steps: 1,
          toolCalls: 1,
          errors: 0,
          inputTokens: 10,
          outputTokens: 2,
          cost: '0.01',
          durationMs: 900,
          startedMs: 1,
        },
        steps: [
          {
            step: 3,
            durationMs: 500,
            reasoning: '想',
            text: '好',
            failures: ['第 1 次重试：504'],
            toolCalls: [
              { callId: 'k', name: 'bash', arguments: '{}', result: null, isError: true, durationMs: 9 },
            ],
            usage: { model: 'default', inputTokens: 10, outputTokens: 2, cost: null, durationMs: 400 },
          },
          { bad: 1 },
        ],
        subagents: [{ sessionId: 's', label: '子任务', steps: [] }],
        events: ['加载技能 report 1.0.0'],
      }),
    });
    const turn = await d.getTurn(d.ownRun('c1'), 2);
    expect(turn.summary).toMatchObject({ turn: 2, status: 'completed', cost: '0.01' });
    expect(turn.steps).toHaveLength(1);
    expect(turn.steps[0]?.toolCalls[0]).toMatchObject({ name: 'bash', result: null, isError: true });
    expect(turn.steps[0]?.usage).toEqual({
      model: 'default',
      inputTokens: 10,
      outputTokens: 2,
      cost: null,
      durationMs: 400,
    });
    expect(turn.subagents[0]?.label).toBe('子任务');
  });

  it('解析某一步的输入，给出导出地址与轮次号', async () => {
    routeFetch({
      [`GET ${B}/turns/2/steps/3/input`]: jsonResponse({
        system: ['你是助手'],
        tools: [{ name: 'bash', description: '执行命令' }],
        notes: ['按日志还原'],
        messages: [{ role: 'user', source: 'runtime-context', blocks: [{ type: 'text', text: '上下文' }] }],
      }),
    });
    const input = await d.getStepInput(d.ownRun('c1'), 2, 3);
    expect(input.messages[0]).toMatchObject({ role: 'user', source: 'runtime-context', toolCallId: null });
    expect(input.tools).toEqual([{ name: 'bash', description: '执行命令' }]);
    expect(d.exportUrl(d.ownRun('c 1'), 2)).toBe('/api/conversations/c%201/debug/turns/2/export');
    expect(d.exportUrl(d.workspaceRun('c1'), 2)).toBe('/api/workspaces/default/runs/c1/turns/2/export');
    expect(d.turnOfRun('run-12')).toBe(12);
    expect(d.turnOfRun('pending')).toBeUndefined();
  });
});
