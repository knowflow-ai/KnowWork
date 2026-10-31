import { describe, expect, it } from 'vitest';
import { register } from '../../../slots/registry';
import { answerNotices } from './notices';
import type { StepView } from './types';

const step = (name: string, args: unknown, extra: Partial<StepView> = {}): StepView => ({
  callId: `c-${name}-${JSON.stringify(args)}`,
  kind: 'tool',
  name,
  arguments: JSON.stringify(args),
  status: 'done',
  resultPreview: '好的',
  ...extra,
});

describe('回答下方的说明（扩展工具在插槽里登记的 notice）', () => {
  it('按这一轮做完的调用逐条算；出错的、没登记的、notice 返回 null 的不出', () => {
    register('chat.tools', {
      name: 'note_tool',
      label: '记',
      notice: ({ arguments: args, result }) =>
        result.startsWith('好')
          ? { text: `已记：${String(args.content)}`, link: { label: '去看看', to: '/settings?tab=notes' } }
          : null,
    });
    const notices = answerNotices([
      step('note_tool', { content: 'A' }),
      step('note_tool', { content: 'B' }, { status: 'error', resultPreview: '好' }),
      step('note_tool', { content: 'C' }, { resultPreview: '没有记' }),
      step('other_tool', { content: 'D' }),
    ]);
    expect(notices).toEqual([
      {
        key: 'c-note_tool-{"content":"A"}',
        text: '已记：A',
        link: { label: '去看看', to: '/settings?tab=notes' },
      },
    ]);
  });
});
