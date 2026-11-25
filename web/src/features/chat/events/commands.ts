/**
 * 用户的 / 命令（DSH 官方 dsh-commands）在对话里的样子：一条命令一个条目，不归任何一轮，排在它执行时已有的那些轮次之后。
 * 服务端把官方会话日志里的 command/run（名字与参数）、command/done（成功或失败、给人看的文字）投影成同一个 item_id 的
 * item.started、item.completed（events_v1.py），这里合成一条。本页刚执行的先放进 localCommands，事件到了以事件为准。
 */
import type { ChatState, CommandView, V1Event } from './types';

const str = (value: unknown): string | undefined => (typeof value === 'string' ? value : undefined);

export function onCommand(state: ChatState, event: V1Event, itemId: string): ChatState {
  const data = event.data;
  const current = state.commands.find((command) => command.id === itemId);
  const name = str(data.name) ?? current?.name;
  if (!name) return state;
  const args = str(data.args) ?? current?.args;
  const done = event.type === 'item.completed';
  const status = done ? (data.status === 'error' ? 'error' : 'success') : (current?.status ?? 'running');
  const text = str(data.text) ?? current?.text;
  const next: CommandView = {
    id: itemId,
    name,
    ...(args !== undefined ? { args } : {}),
    status,
    ...(text !== undefined ? { text } : {}),
    time: current?.time ?? event.time,
    afterRuns: current?.afterRuns ?? state.runOrder.length,
  };
  return {
    ...state,
    commands: current
      ? state.commands.map((command) => (command.id === itemId ? next : command))
      : [...state.commands, next],
  };
}

/** 本页刚执行完的一条（结果当场就有）：事件到了以事件为准，这里不再显示 */
export function onLocalCommand(state: ChatState, command: Omit<CommandView, 'afterRuns'>): ChatState {
  if (state.localCommands.some((c) => c.id === command.id)) return state;
  return {
    ...state,
    localCommands: [...state.localCommands, { ...command, afterRuns: state.runOrder.length }],
  };
}

/** 要显示的命令：事件里的，加上本页刚执行、事件还没到的 */
export function visibleCommands(state: ChatState): readonly CommandView[] {
  const seen = new Set(state.commands.map((command) => command.id));
  return [...state.commands, ...state.localCommands.filter((command) => !seen.has(command.id))];
}

/** 命令那一行的原样（气泡里显示的）：/名字 加上参数 */
export function commandLine(command: Pick<CommandView, 'name' | 'args'>): string {
  const args = command.args ?? '';
  return `/${command.name}${args && !/^\s/.test(args) ? ' ' : ''}${args}`;
}
