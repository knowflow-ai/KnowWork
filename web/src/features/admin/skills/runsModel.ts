/** 「最近运行」的显示规则：状态、失败阶段、选择方式的文案，成功率的算法。 */
import type { SkillRunStatus, SkillVersionStats } from '../../../api/skillRuns';
import type { Tone } from '../../../ui/StatusDot';

/** 与运行记录同一套颜色：成功绿、失败红、取消灰。 */
export const RUN_STATUS: Readonly<Record<SkillRunStatus, { readonly tone: Tone; readonly text: string }>> = {
  succeeded: { tone: 'success', text: '成功' },
  failed: { tone: 'error', text: '失败' },
  cancelled: { tone: 'default', text: '已取消' },
};

const STAGES: Readonly<Record<string, string>> = {
  selection: '选择',
  permission: '权限',
  step: '步骤',
  tool: '工具',
  knowledge: '知识库',
  generation: '生成',
  delivery: '交付',
};

/** 服务端的失败原因码翻成人话；模型厂商的错误码不给用户看，一律说成模型调用失败。 */
const REASONS: Readonly<Record<string, string>> = {
  completion_missing: '没完成技能要求的步骤',
  max_tokens: '输出超出长度上限',
  blocked: '请求被策略拦下',
  artifact_missing: '交付的文件没有收下',
};

const MODES: Readonly<Record<string, string>> = {
  manual: '用户调用',
  automatic: '模型选用',
  workflow: '固定工作流',
};

export const stageLabel = (stage: string | null): string => (stage ? (STAGES[stage] ?? '其他') : '—');

export function reasonLabel(stage: string | null, code: string | null): string {
  if (code && REASONS[code]) return REASONS[code];
  return stage === 'generation' ? '模型调用失败' : '';
}

export const modeLabel = (mode: string): string => MODES[mode] ?? mode;

/** 成功率只看有结果的运行：用户中途取消的不算失败，也不算成功。 */
export function successRate(stats: SkillVersionStats): string {
  const decided = stats.succeeded + stats.failed;
  return decided === 0 ? '—' : `${Math.round((stats.succeeded / decided) * 100)}%`;
}
