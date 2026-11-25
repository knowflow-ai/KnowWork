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

/** 动作的中文名：认得的写中文，别的照原名（新接口的动作名由服务端推出，见 audit/actions.py） */
const ACTION_LABELS: Readonly<Record<string, string>> = {
  'content_safety.blocked': '敏感词拦截',
  'content_safety.policy_update': '修改内容安全策略',
  'conversation.command': '执行对话命令',
  'plugin.set_mode': '设置预置插件',
  'plugin.switch': '开关插件',
  'plugin.upload': '上传空间插件',
  'plugin.update': '修改空间插件',
  'plugin.delete': '删除空间插件',
  'plugin.adopt': '个人插件转为空间插件',
  'plugin.settings_update': '修改插件管控',
  'plugin.upload_personal': '上传个人插件',
  'plugin.delete_personal': '删除个人插件',
  'plugin.submit': '提交个人插件给管理员',
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

const DIRECTION_LABELS: Readonly<Record<string, string>> = {
  input: '输入',
  output: '输出',
  tool_args: '工具参数',
};

export interface DetailLine {
  readonly label: string;
  readonly value: string;
}

function plain(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(plain).join('、');
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

/** 敏感词拦截的详情：方向（工具参数带上工具名）、命中词（多了写「等 N 个」）、策略第几版。不记原文 */
function blockedLines(detail: Readonly<Record<string, unknown>>): readonly DetailLine[] {
  const direction = DIRECTION_LABELS[String(detail.direction)] ?? plain(detail.direction);
  const words = Array.isArray(detail.words) ? detail.words.map(String) : [];
  const total = typeof detail.word_count === 'number' ? detail.word_count : words.length;
  return [
    { label: '方向', value: detail.tool ? `${direction}：${plain(detail.tool)}` : direction },
    { label: '命中词', value: `${words.join('、')}${total > words.length ? ` 等 ${total} 个` : ''}` },
    { label: '策略版本', value: `第 ${plain(detail.policy_version)} 版` },
  ];
}

/** 一条审计的详情：认得的动作按中文排好，别的照原样列出键和值 */
export function detailLines(
  action: string,
  detail: Readonly<Record<string, unknown>>,
): readonly DetailLine[] {
  if (action === 'content_safety.blocked') return blockedLines(detail);
  return Object.entries(detail).map(([label, value]) => ({ label, value: plain(value) }));
}
