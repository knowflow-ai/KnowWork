/**
 * @ 菜单的状态。刚打 @ 时列四个分组（知识库、文件、标签、技能），各带一句选了会怎样；
 * 接着打字就跨组查（每组露前几项，「查看全部」进组）；进了组只看这一组。↑↓ 选，回车、Tab 或 → 确定（→ 只进分组），
 * ← 退回分组列表，Esc 先退出分组再关。
 * 键盘事件由输入框转过来（焦点一直在输入框里，边打字边选）。
 */
import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  findMentionDocuments,
  getMentionOptions,
  type Mention,
  type MentionOptions,
} from '../../../api/mentions';
import { sections, type Candidate, type Trigger } from './candidates';
import type { MentionGroup } from './kinds';

export type Row =
  | { readonly kind: 'group'; readonly group: MentionGroup; readonly count?: number }
  | { readonly kind: 'item'; readonly group: MentionGroup; readonly candidate: Candidate }
  | { readonly kind: 'more'; readonly group: MentionGroup; readonly total: number };

export const MENTION_OPTIONS_KEY = ['mention-options'] as const;
export const MENTION_MENU_ID = 'kw-mention-menu';

export function optionId(index: number): string {
  return `${MENTION_MENU_ID}-${index}`;
}

/** 输入框的 aria-activedescendant：菜单里当前高亮的那一项。 */
export function activeOption(menu: MentionMenu): string | undefined {
  return menu.open && menu.rows[menu.active] ? optionId(menu.active) : undefined;
}
const EMPTY: MentionOptions = { bases: [], services: [], skills: [] };
const SEARCH_DELAY = 200;

function useDebounced(value: string, delay: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

export function useMentionOptions(): { readonly options: MentionOptions; readonly loading: boolean } {
  const query = useQuery({ queryKey: MENTION_OPTIONS_KEY, queryFn: getMentionOptions, staleTime: 60_000 });
  return { options: query.data ?? EMPTY, loading: query.isPending };
}

function rootRows(options: MentionOptions): Row[] {
  const tags = options.bases.filter((b) => b.pinnable).reduce((sum, b) => sum + b.tags.length, 0);
  const rows: Row[] = [];
  if (options.bases.length > 0) rows.push({ kind: 'group', group: 'bases', count: options.bases.length });
  if (options.bases.some((b) => b.pinnable)) rows.push({ kind: 'group', group: 'documents' });
  if (tags > 0) rows.push({ kind: 'group', group: 'tags', count: tags });
  if (options.services.length > 0)
    rows.push({ kind: 'group', group: 'services', count: options.services.length });
  if (options.skills.length > 0) rows.push({ kind: 'group', group: 'skills', count: options.skills.length });
  return rows;
}

export interface MentionMenu {
  readonly open: boolean;
  readonly group: MentionGroup | null;
  readonly rows: readonly Row[];
  readonly active: number;
  readonly loading: boolean;
  /** 没有可选项时的说明 */
  readonly empty: string | null;
  /** 列表下面的一句说明 */
  readonly note: string | null;
  setActive(index: number): void;
  choose(row: Row): void;
  leave(): void;
  /** 输入框的按键先交给菜单；返回 true 表示菜单处理了，输入框不再处理。 */
  onKey(event: KeyboardEvent): boolean;
}

export function useMentionMenu(
  trigger: Trigger | null,
  onPick: (mention: Mention) => void,
  onDismiss: () => void,
  /** 智能体模式：快速问答用不了技能和 MCP 工具，菜单里不列 */
  agent = true,
): MentionMenu {
  const { options: offered, loading } = useMentionOptions();
  const options = useMemo(
    () => (agent ? offered : { ...offered, services: [], skills: [] }),
    [offered, agent],
  );
  const open = trigger !== null;
  const start = trigger?.start;
  const query = trigger?.query ?? '';
  // 进了哪一组、高亮第几项都跟着「这一个 @、这一个查找词」记：打新的 @ 从分组列表开始，改了字从第一项开始
  const [entered, setEntered] = useState<{ readonly start?: number; readonly group: MentionGroup | null }>({
    group: null,
  });
  const group = entered.start === start ? entered.group : null;
  const view = `${start}|${query}|${group}`;
  const [cursor, setCursor] = useState({ view: '', index: 0 });
  const active = cursor.view === view ? cursor.index : 0;
  const setActive = (index: number) => setCursor({ view, index });
  const setGroup = (next: MentionGroup | null) => setEntered({ start, group: next });
  const searched = useDebounced(query.trim(), SEARCH_DELAY);
  // 进了「文件」组就列（没打字时是最近更新的）；直接输入查找时有字才搜
  const wantsDocuments = open && (group === 'documents' || (group === null && searched.length > 0));
  const documents = useQuery({
    queryKey: ['mention-documents', searched],
    queryFn: () => findMentionDocuments(searched),
    enabled: wantsDocuments,
    staleTime: 30_000,
  });

  const rows = useMemo<readonly Row[]>(() => {
    if (!open) return [];
    if (group === null && !query) return rootRows(options);
    const found = sections(options, wantsDocuments ? (documents.data ?? []) : [], query, group);
    return found.flatMap((section): Row[] => [
      ...section.items.map((candidate): Row => ({ kind: 'item', group: section.group, candidate })),
      ...(group === null && section.total > section.items.length
        ? [{ kind: 'more' as const, group: section.group, total: section.total }]
        : []),
    ]);
  }, [open, group, query, options, wantsDocuments, documents.data]);

  const busy = loading || (wantsDocuments && documents.isFetching);
  const empty = rows.length > 0 || busy ? null : '没有匹配的内容';
  const note =
    group === 'documents' && !query && rows.length > 0 ? '这里是最近更新的文件，接着打字按名字查找' : null;

  const choose = (row: Row) => {
    if (row.kind === 'item') onPick(row.candidate.mention);
    else setGroup(row.group);
  };
  const leave = () => setGroup(null);

  const onKey = (event: KeyboardEvent): boolean => {
    if (!open || event.nativeEvent.isComposing) return false;
    const move = (delta: number) => setActive(Math.min(Math.max(active + delta, 0), rows.length - 1));
    switch (event.key) {
      case 'ArrowDown':
        move(1);
        return true;
      case 'ArrowUp':
        move(-1);
        return true;
      case 'Enter':
      case 'Tab': {
        const row = rows[active];
        if (!row) return event.key === 'Enter' && rows.length === 0 && busy;
        choose(row);
        return true;
      }
      // → 进入高亮的分组（或「查看全部」），← 退回分组列表；高亮的是具体一项、或已在分组列表时，照常移动光标
      case 'ArrowRight': {
        const row = rows[active];
        if (!row || row.kind === 'item') return false;
        choose(row);
        return true;
      }
      case 'ArrowLeft':
        if (group === null) return false;
        leave();
        return true;
      case 'Escape':
        if (group !== null) leave();
        else onDismiss();
        return true;
      default:
        return false;
    }
  };

  return { open, group, rows, active, loading: busy, empty, note, setActive, choose, leave, onKey };
}
