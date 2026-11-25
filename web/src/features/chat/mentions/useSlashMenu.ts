/**
 * / 菜单的状态（规则见 slash.ts）。命令按对话向执行的一方要（云端问业务服务、本机问桌面端），菜单打开时才要；技能和 @ 菜单
 * 同一份候选。↑↓ 选，回车或 Tab 确定，Esc 关。键盘事件由输入框转过来。
 */
import { useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ConversationCommand } from '../../../api/conversations';
import type { MentionSkill } from '../../../api/mentions';
import { slashRows, type SlashRow, type SlashTrigger } from './slash';

export const SLASH_MENU_ID = 'kw-slash-menu';
export const slashOptionId = (index: number) => `${SLASH_MENU_ID}-${index}`;

/** 这段对话的命令怎么列（不给就没有命令，菜单里只有技能） */
export interface SlashCommands {
  /** 查询键里区分是哪段对话 */
  readonly key: string;
  readonly list: () => Promise<readonly ConversationCommand[]>;
}

export interface SlashMenu {
  readonly open: boolean;
  readonly rows: readonly SlashRow[];
  readonly active: number;
  readonly loading: boolean;
  readonly empty: string | null;
  /** 列不出命令时的原因（例如执行环境没起来）；技能照常列 */
  readonly problem: string | null;
  /** 已经拿到的命令（发送时认命令行用） */
  readonly commands: readonly ConversationCommand[] | undefined;
  setActive(index: number): void;
  choose(row: SlashRow): void;
  onKey(event: KeyboardEvent): boolean;
}

const NO_COMMANDS: readonly ConversationCommand[] = [];

export function useSlashMenu(
  trigger: SlashTrigger | null,
  skills: readonly MentionSkill[],
  commands: SlashCommands | undefined,
  onPick: (row: SlashRow) => void,
  onDismiss: () => void,
): SlashMenu {
  const open = trigger !== null;
  const listed = useQuery({
    queryKey: ['conversation-commands', commands?.key ?? null],
    queryFn: () => commands!.list(),
    // 打开菜单时才要：列命令要宿主上开着这段对话，空闲回收了的会被重新打开，不能一进页面就要
    enabled: commands !== undefined && open,
    staleTime: 30_000,
    retry: false,
  });
  const query = trigger?.query ?? '';
  const rows = open ? slashRows(skills, listed.data ?? NO_COMMANDS, query) : [];
  const [cursor, setCursor] = useState({ query: '', index: 0 });
  const active = cursor.query === query ? Math.min(cursor.index, Math.max(rows.length - 1, 0)) : 0;
  const setActive = (index: number) => setCursor({ query, index });
  const loading = open && commands !== undefined && listed.isPending;
  const empty =
    !open || rows.length > 0 || loading
      ? null
      : commands === undefined && skills.length === 0
        ? '这里没有可用的技能；命令在对话开始以后才能用'
        : '没有匹配的技能或命令';
  const problem = listed.isError ? '命令暂时列不出来，请稍后再试' : null;

  const onKey = (event: KeyboardEvent): boolean => {
    if (!open || event.nativeEvent.isComposing) return false;
    switch (event.key) {
      case 'ArrowDown':
        setActive(Math.min(active + 1, rows.length - 1));
        return true;
      case 'ArrowUp':
        setActive(Math.max(active - 1, 0));
        return true;
      case 'Enter':
      case 'Tab': {
        const row = rows[active];
        if (!row) return event.key === 'Tab';
        onPick(row);
        return true;
      }
      case 'Escape':
        onDismiss();
        return true;
      default:
        return false;
    }
  };

  return {
    open,
    rows,
    active,
    loading,
    empty,
    problem,
    commands: listed.data,
    setActive,
    choose: onPick,
    onKey,
  };
}
