/** 审计页的选项与显示文字。 */
import type { Tone } from '../../../ui/StatusDot';

export const ACTOR_OPTIONS = [
  { value: 'human', label: '人员' },
  { value: 'agent', label: 'Agent' },
  { value: 'system', label: '系统' },
] as const;

export const RESULT_OPTIONS = [
  { value: 'success', label: '成功' },
  { value: 'denied', label: '被拒绝' },
  { value: 'failed', label: '失败' },
] as const;

export const RESULT_BADGE: Readonly<Record<string, { readonly status: Tone; readonly text: string }>> = {
  success: { status: 'success', text: '成功' },
  denied: { status: 'warning', text: '被拒绝' },
  failed: { status: 'error', text: '失败' },
};

export function actorTypeLabel(value: string): string {
  return ACTOR_OPTIONS.find((o) => o.value === value)?.label ?? value;
}
