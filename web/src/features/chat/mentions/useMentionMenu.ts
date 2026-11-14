/**
 * @ 菜单的状态。刚打 @ 时列分组（知识库、文件、标签、扩展登记的、MCP、技能），各带一句选了会怎样；
 * 接着打字就跨组查（每组露前几项，「查看全部」进组）；进了组只看这一组。↑↓ 选，回车、Tab 或 → 确定（→ 只进分组），
 * ← 退回分组列表，Esc 先退出分组再关。
 * 键盘事件由输入框转过来（焦点一直在输入框里，边打字边选）。
 */
import { createContext, useContext, useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  findMentionDocuments,
  getMentionOptions,
  type Mention,
  type MentionDocument,
  type MentionOptions,
} from '../../../api/mentions';
import { sections, type Candidate, type Trigger } from './candidates';
import { extensionGroup, type MentionGroup } from './kinds';
import type { FileReference } from '../sources';

export type Row =
  | { readonly kind: 'group'; readonly group: MentionGroup; readonly count?: number }
  | { readonly kind: 'item'; readonly group: MentionGroup; readonly candidate: Candidate }
  | { readonly kind: 'file'; readonly group: MentionGroup; readonly file: FileReference }
  | { readonly kind: 'more'; readonly group: MentionGroup; readonly total: number };

/** 工作文件夹里的文件（见 sources.ts 的 ConversationFiles.references）：怎么列、选中了怎么写进输入框 */
export interface MentionFiles {
  readonly list: (query: string) => Promise<readonly FileReference[]>;
  readonly insert: (file: FileReference) => void;
}

export const MENTION_OPTIONS_KEY = ['mention-options'] as const;
export const MENTION_MENU_ID = 'kw-mention-menu';

export function optionId(index: number): string {
  return `${MENTION_MENU_ID}-${index}`;
}

/** 输入框的 aria-activedescendant：菜单里当前高亮的那一项。 */
export function activeOption(menu: MentionMenu): string | undefined {
  return menu.open && menu.rows[menu.active] ? optionId(menu.active) : undefined;
}
const EMPTY: MentionOptions = { bases: [], services: [], skills: [], extensions: [] };
const SEARCH_DELAY = 200;
/** 直接输入查找时，工作文件夹里露前几项 */
const FLAT_FILES = 5;

function useDebounced(value: string, delay: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** 外层自己收窄 @ 候选（例如还没建对话的专家新对话页，按专家设的范围）；不给的那一类不收窄。 */
export interface MentionNarrowing {
  readonly bases?: (id: string) => boolean;
  readonly skills?: (name: string) => boolean;
  readonly services?: (name: string) => boolean;
  readonly extension?: (kind: string, id: string) => boolean;
}

/** @ 候选按谁的范围取：已有对话给对话 id（服务端按对话的范围收窄），还没有对话的由外层收窄。 */
export interface MentionScope {
  readonly conversationId?: string;
  readonly narrowing?: MentionNarrowing;
}

/** 输入框把自己的范围放进来，里面的小标签等取 @ 候选时按同一份 */
export const MentionScopeContext = createContext<MentionScope>({});

function narrowed(options: MentionOptions, by: MentionNarrowing | undefined): MentionOptions {
  if (!by) return options;
  return {
    bases: by.bases ? options.bases.filter((b) => by.bases!(b.id)) : options.bases,
    skills: by.skills ? options.skills.filter((s) => by.skills!(s.name)) : options.skills,
    services: by.services ? options.services.filter((s) => by.services!(s.name)) : options.services,
    extensions: by.extension
      ? options.extensions.map((e) => ({ ...e, items: e.items.filter((i) => by.extension!(e.kind, i.id)) }))
      : options.extensions,
  };
}

/** 没有范围时就是 MENTION_OPTIONS_KEY：和解析小标签名字的那份（resolve.ts）共用一次请求 */
const optionsKey = (conversationId?: string) =>
  conversationId ? ([...MENTION_OPTIONS_KEY, conversationId] as const) : MENTION_OPTIONS_KEY;

export function useMentionOptions(scope?: MentionScope): {
  readonly options: MentionOptions;
  readonly loading: boolean;
} {
  const inherited = useContext(MentionScopeContext);
  const { conversationId, narrowing } = scope ?? inherited;
  const query = useQuery({
    queryKey: optionsKey(conversationId),
    queryFn: () => getMentionOptions(conversationId),
    staleTime: 60_000,
  });
  const options = useMemo(() => narrowed(query.data ?? EMPTY, narrowing), [query.data, narrowing]);
  return { options, loading: query.isPending };
}

const NO_DOCUMENTS: readonly MentionDocument[] = [];

function rootRows(options: MentionOptions, files: boolean): Row[] {
  const tags = options.bases.filter((b) => b.pinnable).reduce((sum, b) => sum + b.tags.length, 0);
  const rows: Row[] = files ? [{ kind: 'group', group: 'workspace' }] : [];
  if (options.bases.length > 0) rows.push({ kind: 'group', group: 'bases', count: options.bases.length });
  if (options.bases.some((b) => b.pinnable)) rows.push({ kind: 'group', group: 'documents' });
  if (tags > 0) rows.push({ kind: 'group', group: 'tags', count: tags });
  for (const extension of options.extensions)
    if (extension.items.length > 0)
      rows.push({ kind: 'group', group: extensionGroup(extension.kind), count: extension.items.length });
  if (options.services.length > 0)
    rows.push({ kind: 'group', group: 'services', count: options.services.length });
  if (options.skills.length > 0) rows.push({ kind: 'group', group: 'skills', count: options.skills.length });
  return rows;
}

export interface MentionMenu {
  readonly open: boolean;
  /** 扩展登记的类型（分组标题与小标签要用它们的名字和效果） */
  readonly options: MentionOptions;
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
  /** 按谁的范围列候选（见 MentionScope）；不给按本人能用的全部 */
  scope: MentionScope = {},
  /** 工作文件夹里的文件：给了多一组「工作文件夹」 */
  files?: MentionFiles,
): MentionMenu {
  const { options: offered, loading } = useMentionOptions(scope);
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
    queryKey: ['mention-documents', searched, scope.conversationId ?? null],
    queryFn: () => findMentionDocuments(searched, scope.conversationId),
    enabled: wantsDocuments,
    staleTime: 30_000,
  });
  // 进了「工作文件夹」组就列（没打字时是文件夹最上一层）；直接输入查找时有字才列，路径的斜杠要留着，不去空白
  const filesQuery = useDebounced(query, SEARCH_DELAY);
  const wantsFiles =
    open && files !== undefined && (group === 'workspace' || (group === null && filesQuery.length > 0));
  const references = useQuery({
    queryKey: ['file-references', scope.conversationId ?? null, filesQuery],
    queryFn: () => files!.list(filesQuery),
    enabled: wantsFiles,
    staleTime: 10_000,
  });
  const bases = scope.narrowing?.bases;
  const docs = useMemo(
    () =>
      bases
        ? (documents.data ?? NO_DOCUMENTS).filter((d) => bases(d.base))
        : (documents.data ?? NO_DOCUMENTS),
    [documents.data, bases],
  );

  const rows = useMemo<readonly Row[]>(() => {
    if (!open) return [];
    if (group === null && !query) return rootRows(options, files !== undefined);
    const fileRows = (wantsFiles ? (references.data ?? []) : [])
      .slice(0, group === 'workspace' ? undefined : FLAT_FILES)
      .map((file): Row => ({ kind: 'file', group: 'workspace', file }));
    if (group === 'workspace') return fileRows;
    const found = sections(options, wantsDocuments ? docs : [], query, group);
    return fileRows.concat(
      found.flatMap((section): Row[] => [
        ...section.items.map((candidate): Row => ({ kind: 'item', group: section.group, candidate })),
        ...(group === null && section.total > section.items.length
          ? [{ kind: 'more' as const, group: section.group, total: section.total }]
          : []),
      ]),
    );
  }, [open, group, query, options, wantsDocuments, docs, files, wantsFiles, references.data]);

  const busy = loading || (wantsDocuments && documents.isFetching) || (wantsFiles && references.isFetching);
  const empty = rows.length > 0 || busy ? null : '没有匹配的内容';
  const note =
    group === 'documents' && !query && rows.length > 0
      ? '这里是最近更新的文件，接着打字按名字查找'
      : group === 'workspace' && rows.length > 0
        ? '接着打字按路径查找；选文件夹会接着往下列'
        : null;

  const choose = (row: Row) => {
    if (row.kind === 'item') onPick(row.candidate.mention);
    else if (row.kind === 'file') files?.insert(row.file);
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
        if (!row || row.kind === 'item' || row.kind === 'file') return false;
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

  return { open, options, group, rows, active, loading: busy, empty, note, setActive, choose, leave, onKey };
}
