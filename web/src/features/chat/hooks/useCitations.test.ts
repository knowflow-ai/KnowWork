import { describe, expect, it } from 'vitest';
import { initialChatState } from '../events/reducer';
import type { ChatState, RunView, StepView } from '../events/types';
import { knowledgeResults } from './useCitations';

const step = (name: string, status: StepView['status']): StepView => ({
  callId: `${name}-${status}`,
  kind: 'tool',
  name,
  arguments: '{}',
  status,
});

const run = (runId: string, steps: StepView[]): RunView => ({
  runId,
  status: 'running',
  steps,
  notes: [],
  thinking: [],
  answer: '',
  problems: [],
  asked: [],
  trail: [],
});

describe('出处拉取时机', () => {
  it('数知识检索、阅读返回了几次：返回一次就刷新出处，回答里的图边输出边显示', () => {
    const state: ChatState = {
      ...initialChatState,
      runOrder: ['r1', 'r2'],
      runs: {
        r1: run('r1', [step('knowledge_search', 'done'), step('write_file', 'done')]),
        r2: run('r2', [step('knowledge_read', 'done'), step('knowledge_search', 'running')]),
      },
    };
    expect(knowledgeResults(state)).toBe(2);
    expect(knowledgeResults(initialChatState)).toBe(0);
  });
});
