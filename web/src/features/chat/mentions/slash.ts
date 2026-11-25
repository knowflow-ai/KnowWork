/**
 * 输入框开头的 / （照 DSH 官方：/ 必须是第一个字，后面紧跟名字）：技能和命令两组。
 * - 技能（按什么做法来做）：选中后变成开头的小标签，这一问照常发给模型（同以前 @ 技能的效果，服务端按「本轮选中技能」处理）；
 * - 命令（插件登记的，例如 /goal）：选中后把 /名字 和一个空格写进输入框，接着写参数，回车直接执行，不发给模型。
 * 这里都是纯函数：认出光标前的 /、按输入的字筛、认出要执行的命令行。
 */
import type { ConversationCommand } from '../../../api/conversations';
import type { MentionSkill } from '../../../api/mentions';

export interface SlashTrigger {
  /** / 后面已经打的字 */
  readonly query: string;
}

/** 光标前是「开头的 / 加一串不含空白的字」时才弹菜单（最多 64 个字） */
export function slashTrigger(value: string, caret: number): SlashTrigger | null {
  const match = /^\/(\S{0,64})$/.exec(value.slice(0, caret));
  return match ? { query: match[1] ?? '' } : null;
}

export interface CommandText {
  /** 中文标题（官方内置命令才有）；菜单里显示它，命令名跟在后面 */
  readonly title?: string;
  readonly detail: string;
  /** 中文输入写法：打 /目标 等于 /goal */
  readonly token?: string;
}

/**
 * 官方内置命令按官方给的稳定编号认（名字插件能改）：中文标题、说明照抄官方命令菜单的中文词条
 * （dsh-client-ui-commands 的 label.*、description.*），中文写法照抄 dsh-commands 的 builtinCommandTokens（0.2.2-rc.5）。
 * 插件自己的命令用它登记的说明。
 */
const OFFICIAL: Readonly<Record<string, CommandText>> = {
  '@deepseek-ai/dsh-command-goal': { title: '目标', detail: '设置或查看长期任务目标', token: '目标' },
  '@deepseek-ai/dsh-plan-mode': { title: '计划', detail: '进入或退出计划模式', token: '计划' },
  '@deepseek-ai/dsh-command-compact': { title: '压缩', detail: '压缩以上对话内容', token: '压缩' },
};
const TOKENS: ReadonlySet<string> = new Set(
  Object.values(OFFICIAL).flatMap((text) => (text.token ? [text.token] : [])),
);

export function commandText(command: ConversationCommand): CommandText {
  const official = command.definitionId ? OFFICIAL[command.definitionId] : undefined;
  if (official) return official;
  return { detail: [command.description, command.hint].filter(Boolean).join(' · ') };
}

/** 打的名字认成哪个命令：先按登记的名字，再按官方内置命令的中文写法（同官方 resolveCommand） */
export function resolveCommand(
  token: string,
  commands: readonly ConversationCommand[],
): ConversationCommand | undefined {
  return (
    commands.find((command) => command.name === token) ??
    commands.find((command) => commandText(command).token === token)
  );
}

/**
 * 选中菜单里的命令后写进输入框的名字：打的是中文写法的开头（如 /目）就保留中文写法，同官方「草稿里保留用户的写法，
 * 发出时换成登记的名字」；否则写登记的名字。
 */
export function spelledName(command: ConversationCommand, typed: string): string {
  const token = commandText(command).token;
  const query = typed.trim();
  return token && query && token.startsWith(query) && !command.name.startsWith(query) ? token : command.name;
}

export type SlashRow =
  | { readonly kind: 'skill'; readonly skill: MentionSkill }
  | { readonly kind: 'command'; readonly command: ConversationCommand };

const has = (text: string | undefined, needle: string) => (text ?? '').toLowerCase().includes(needle);

/** 菜单里的行：先技能后命令，按名字和说明筛 */
export function slashRows(
  skills: readonly MentionSkill[],
  commands: readonly ConversationCommand[],
  query: string,
): readonly SlashRow[] {
  const needle = query.trim().toLowerCase();
  const pickedSkills = skills.filter((s) => !needle || has(s.name, needle) || has(s.description, needle));
  const pickedCommands = commands.filter((c) => {
    if (!needle) return true;
    const text = commandText(c);
    return (
      has(c.name, needle) || has(text.title, needle) || has(text.detail, needle) || has(text.token, needle)
    );
  });
  return [
    ...pickedSkills.map((skill): SlashRow => ({ kind: 'skill', skill })),
    ...pickedCommands.map((command): SlashRow => ({ kind: 'command', command })),
  ];
}

/**
 * 输入框里这一行是不是命令（官方语法：/ 开头、小写名字、后面是空白或结束）；是的话给出名字和名字后面的部分。
 * 官方内置命令的中文写法（/目标 等）也算；别的中文开头的照常当普通消息发。
 */
export function parseSlashLine(value: string): { readonly name: string; readonly rest: string } | null {
  const match = /^\/([^\s/]+)(?=\s|$)([\s\S]*)$/.exec(value);
  const name = match?.[1] ?? '';
  return match && (/^[a-z0-9_-]+$/.test(name) || TOKENS.has(name)) ? { name, rest: match[2] ?? '' } : null;
}
