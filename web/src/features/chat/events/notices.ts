/**
 * 回答下方的说明：扩展工具在插槽 chat.tools 里登记了 notice 的，这一轮每次做完、没出错的调用各算一行
 * （例如「已记住：回答用表格」）。执行过程默认收起，这类用户要知道的副作用单独露出来。
 */
import { slotItems, type ToolNotice } from '../../../slots/registry';
import type { StepView } from './types';

export interface AnswerNotice extends ToolNotice {
  readonly key: string;
}

function argumentsOf(text: string): Readonly<Record<string, unknown>> {
  try {
    const value: unknown = JSON.parse(text);
    return value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

export function answerNotices(steps: readonly StepView[]): readonly AnswerNotice[] {
  const tools = slotItems('chat.tools');
  return steps.flatMap((step) => {
    const notice = tools.find((tool) => tool.name === step.name)?.notice;
    if (!notice || step.kind !== 'tool' || step.status !== 'done') return [];
    const found = notice({ arguments: argumentsOf(step.arguments), result: step.resultPreview ?? '' });
    return found ? [{ ...found, key: step.callId }] : [];
  });
}
