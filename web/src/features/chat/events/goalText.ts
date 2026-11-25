/**
 * 官方 /goal 命令（dsh-command-goal）的结果是英文，逐行换成中文给人看；认不出的行原样留着。
 * 用词照官方目标栏的中文词条（dsh-client-ui-goal 的 zh：进行中、待继续、已暂停、受阻）；「Commands:」那行不要，
 * 暂停、恢复、清除在输入框上方的目标面板上有按钮。只换显示，记进对话的原文不动。
 */

const PHASES: Readonly<Record<string, string>> = {
  active: '进行中',
  paused: '已暂停',
  blocked: '受阻',
  complete: '已完成',
};

const USAGE = '用法：/goal 目标内容；/goal edit 新目标、/goal pause、/goal resume、/goal clear';

const ACTIONS: Readonly<Record<string, string>> = {
  edit: '改目标',
  pause: '暂停',
  resume: '恢复',
  clear: '清除',
};

/** 整行一样的（官方 renderGoal 的标题和几句固定的话） */
const LINES: Readonly<Record<string, string>> = {
  'Goal created': '已设好目标，智能体会自己一轮接一轮做下去',
  'Goal updated': '已改好目标',
  'Goal paused': '目标已暂停',
  'Goal resumed': '目标已恢复，接着做',
  Goal: '当前目标',
  'Goal cleared.': '目标已清除',
  'No goal to clear.': '没有要清除的目标',
  'No goal is currently set.': '还没有设目标',
  'Goal editing requires a replacement objective.': '改目标要写新的目标内容：/goal edit 新目标',
  'The goal command is not valid for the current state. Run /goal to view available commands.':
    '现在这个状态下不能这样操作目标，打 /goal 看看当前目标',
  'Attachments only accompany a goal objective: /goal <objective> or /goal edit <objective>.':
    '附件只能跟着目标内容一起发：/goal 目标内容，或 /goal edit 新目标',
};

/**
 * 受阻原因里官方自动续跑写死的几句（dsh-goal-round-driver）换成中文；模型自己写的（update_goal 的 blocked_reason）和
 * 我们宿主写的（goal-gate.mjs，本来就是中文）原样
 */
export function goalReasonText(reason: string): string {
  let match = /^Goal reached its configured limit of (\d+) rounds\.$/.exec(reason);
  if (match) return `已经自动接着做满 ${match[1]} 轮；要再做，清除后重新设一个目标（改目标不会把轮数清零）`;
  if (reason === 'Goal round was rejected before entering its step.') return '这一轮没能开始（被拦下了）';
  match = /^Could not queue goal round (\d+):/.exec(reason);
  if (match) return `第 ${match[1]} 轮没能排上`;
  return reason;
}

function line(raw: string, status: string | undefined): string | null {
  const text = raw.trim();
  if (!text || text.startsWith('Commands:')) return null;
  if (LINES[text]) return LINES[text];
  if (text.startsWith('Usage: /goal')) return USAGE;
  let match = /^Status: (\w+)$/.exec(text);
  if (match) return `状态：${PHASES[match[1] ?? ''] ?? match[1]}`;
  match = /^Blocker: [^:]+: ([\s\S]*)$/.exec(text);
  if (match) return `受阻原因：${goalReasonText(match[1] ?? '')}`;
  match = /^Objective: ([\s\S]*)$/.exec(text);
  if (match) return `目标：${match[1]}`;
  match = /^Rounds: (\d+)\/(\d+)$/.exec(text);
  if (match) return `自动接着做了 ${match[1]} / ${match[2]} 轮`;
  match = /^Activation: (\w+)$/.exec(text);
  if (match) {
    // 只在进行中却没开自动续跑时说（官方目标栏的「待继续」）；开着、或已暂停、受阻时不用说
    return match[1] === 'disarmed' && status === 'active'
      ? '待继续：还没开始自动接着做，点目标面板上的「恢复」'
      : null;
  }
  match = /^No goal is currently set; \/goal (\w+) requires one\.(?: Usage: .*)?$/.exec(text);
  if (match) return `还没有设目标，不能${ACTIONS[match[1] ?? ''] ?? match[1]}`;
  match = /^A goal is already (\w+)\./.exec(text);
  if (match)
    return `已经有一个${PHASES[match[1] ?? ''] ?? ''}的目标。要换，先用 /goal edit 改它，或 /goal clear 清除`;
  return raw;
}

export function goalCommandText(text: string): string {
  const status = /^Status: (\w+)$/m.exec(text)?.[1];
  return text
    .split('\n')
    .map((raw) => line(raw, status))
    .filter((item): item is string => item !== null)
    .join('\n');
}
