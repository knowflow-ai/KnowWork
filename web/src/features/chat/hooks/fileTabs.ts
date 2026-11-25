/**
 * 对话右边那栏打开的文件（标签），照 DSH 官方右侧栏（@deepseek-ai/dsh-client-ui-sidebar-right 0.2.3-alpha.2 的 store）：
 * - 按对话各记各的：切到别的对话再回来，标签还在；
 * - 收起这一栏不关标签，展开回来照旧；只有把最后一个标签关掉才清空，这一栏跟着收起；
 * - 记在这台浏览器的 localStorage（键 `kw.file-tabs.v1.<对话>`，同官方按会话一个键）：刷新页面后恢复打开了哪些、
 *   当前是哪个、展开着没有；只记文件位置，不记内容。读出来先校验，形状不对的整份不要。取不到（隐私模式、存储被禁）就只在本页有效。
 * 开在哪、关掉以后切到哪照 ui/docTabList.ts。
 */
import { useCallback, useMemo } from 'react';
import { create } from 'zustand';
import { closeDocTabs, openDocTab } from '../../../ui/docTabList';
import type { FileTarget } from '../components/fileLinks';

export interface FileTabs {
  readonly items: readonly FileTarget[];
  readonly activeId: string | undefined;
  /** 这一栏展开着没有（收起时标签还在） */
  readonly expanded: boolean;
}

export const NO_FILE_TABS: FileTabs = { items: [], activeId: undefined, expanded: false };

/** 一个对话最多记多少个标签：存储里的超过这个数不认 */
const MAX_TABS = 50;
const MAX_PATH = 4096;

const keyOf = (conversationId: string) => `kw.file-tabs.v1.${conversationId}`;
const pathOf = (target: FileTarget) => target.path;

function targetOf(value: unknown): FileTarget | undefined {
  if (typeof value !== 'object' || value === null) return undefined;
  const { path, line } = value as { path?: unknown; line?: unknown };
  if (typeof path !== 'string' || path.length === 0 || path.length > MAX_PATH) return undefined;
  if (line === undefined) return { path };
  return Number.isSafeInteger(line) && (line as number) > 0 ? { path, line: line as number } : undefined;
}

/** 存储里的一份：形状不对、有重复、当前标签不在里面的整份不认 */
export function parseFileTabs(raw: string | null): FileTabs {
  if (raw === null) return NO_FILE_TABS;
  try {
    const value = JSON.parse(raw) as { items?: unknown; activeId?: unknown; expanded?: unknown };
    if (!Array.isArray(value.items) || value.items.length === 0 || value.items.length > MAX_TABS)
      return NO_FILE_TABS;
    const items = value.items.map(targetOf);
    if (items.some((item) => item === undefined)) return NO_FILE_TABS;
    const targets = items as FileTarget[];
    if (new Set(targets.map(pathOf)).size !== targets.length) return NO_FILE_TABS;
    const activeId = targets.some((item) => item.path === value.activeId)
      ? (value.activeId as string)
      : targets[0]!.path;
    return { items: targets, activeId, expanded: value.expanded === true };
  } catch {
    return NO_FILE_TABS;
  }
}

function load(conversationId: string): FileTabs {
  try {
    return parseFileTabs(window.localStorage.getItem(keyOf(conversationId)));
  } catch {
    return NO_FILE_TABS;
  }
}

function save(conversationId: string, tabs: FileTabs): void {
  try {
    if (tabs.items.length === 0) window.localStorage.removeItem(keyOf(conversationId));
    else window.localStorage.setItem(keyOf(conversationId), JSON.stringify(tabs));
  } catch {
    // 存不下就只在本页有效
  }
}

interface FileTabsStore {
  readonly byConversation: Readonly<Record<string, FileTabs>>;
  readonly update: (conversationId: string, change: (tabs: FileTabs) => FileTabs) => void;
}

export const useFileTabsStore = create<FileTabsStore>((set, get) => ({
  byConversation: {},
  update: (conversationId, change) => {
    const next = change(get().byConversation[conversationId] ?? load(conversationId));
    save(conversationId, next);
    set({ byConversation: { ...get().byConversation, [conversationId]: next } });
  },
}));

/** 这段对话的标签与改它的几个动作 */
export function useFileTabs(conversationId: string) {
  const stored = useFileTabsStore((state) => state.byConversation[conversationId]);
  const update = useFileTabsStore((state) => state.update);
  const tabs = useMemo(() => stored ?? load(conversationId), [stored, conversationId]);
  const change = useCallback(
    (fn: (current: FileTabs) => FileTabs) => update(conversationId, fn),
    [update, conversationId],
  );
  const actions = useMemo(
    () => ({
      /** 打开一个文件：放进标签（已开着就切过去），展开这一栏 */
      open: (target: FileTarget) =>
        change((current) => ({ ...openDocTab(current, target, pathOf), expanded: true })),
      /** 关掉这几个；关光了这一栏收起 */
      close: (ids: readonly string[]) =>
        change((current) => {
          const next = closeDocTabs(current, ids, pathOf);
          return { ...next, expanded: next.items.length > 0 && current.expanded };
        }),
      select: (id: string) => change((current) => ({ ...current, activeId: id })),
      /** 收起这一栏，标签留着 */
      collapse: () => change((current) => ({ ...current, expanded: false })),
      /** 展开这一栏（有标签才展开） */
      expand: () => change((current) => ({ ...current, expanded: current.items.length > 0 })),
    }),
    [change],
  );
  return { tabs, ...actions };
}
