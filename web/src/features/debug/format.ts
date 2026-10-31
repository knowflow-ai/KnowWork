/** 运行详情里的文案与格式。 */
import type { DebugStep } from '../../api/debug';

const STATUS: Readonly<Record<string, string>> = {
  completed: '已完成',
  running: '运行中',
  cancelled: '已取消',
  // DSH 的回合结束原因：用户中止是 aborted，和 cancelled 同一个意思
  aborted: '已取消',
  interrupted: '被打断',
  failed: '失败',
  error: '出错',
};

export const statusLabel = (status: string): string => STATUS[status] ?? status;

/** 管理端表格的状态圆点（界面规范 3.3：圆点加文字）；运行详情里状态只写文字，不上色。 */
export const statusTone = (status: string): 'success' | 'processing' | 'default' | 'error' =>
  status === 'completed'
    ? 'success'
    : status === 'running'
      ? 'processing'
      : status === 'cancelled' || status === 'aborted' || status === 'interrupted'
        ? 'default'
        : 'error';

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} 毫秒`;
  const seconds = ms / 1000;
  return seconds < 60
    ? `${seconds.toFixed(1)} 秒`
    : `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

const ROLE: Readonly<Record<string, string>> = {
  user: '用户',
  assistant: '模型',
  tool: '工具结果',
  system: '系统',
};
const SOURCE: Readonly<Record<string, string>> = {
  'runtime-context': '运行上下文',
  'skill-catalog': '技能目录',
  'skill-invocation': '技能内容',
  'subagent-settled': '子 Agent 结果',
  'agent-message': '其他 Agent 的消息',
  'knowwork-knowledge-catalog': '知识库目录',
  'knowwork-turn-context': '随提问带上的背景',
};

/** 消息的说明：用户亲手发的就写「用户」，宿主塞进去的上下文写明是什么。 */
export function messageLabel(role: string, source: string | null): string {
  if (role === 'user' && source && source !== 'user') return SOURCE[source] ?? source;
  return ROLE[role] ?? role;
}

/** 工具参数是 JSON 字符串，排一下版便于看；不是 JSON 就原样。 */
export function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/** 这一步做了什么：调用了哪些工具；没调工具就是回答或思考。 */
export function stepTarget(step: DebugStep): string {
  const tools = step.toolCalls.map((c) => c.name).join('、');
  return tools || (step.text.trim() ? '回答' : '思考');
}

export const stepFailed = (step: DebugStep): boolean =>
  step.failures.length > 0 || step.toolCalls.some((c) => c.isError);
