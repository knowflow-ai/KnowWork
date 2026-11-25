/**
 * 打开的文档标签的增减（照 DSH 官方右侧栏：@deepseek-ai/dsh-client-ui-dockkit 0.2.3-alpha.2 的 planner 与
 * tree.neighbourTabId）：
 * - 打开已经开着的：换成新的这份（例如带了新的行号）并切过去，不另开一个；
 * - 打开新的：放在最后，切过去；
 * - 关掉当前的：切到它左边那个，左边没有就切到剩下的第一个；关掉别的不换当前；一次关几个按顺序一个个关，同一套规则；
 * - 全关掉了当前就是 undefined，由调用方决定收起。
 */

export interface DocTabsState<T> {
  readonly items: readonly T[];
  readonly activeId: string | undefined;
}

export const EMPTY_TABS: DocTabsState<never> = { items: [], activeId: undefined };

export function openDocTab<T>(state: DocTabsState<T>, item: T, idOf: (item: T) => string): DocTabsState<T> {
  const id = idOf(item);
  const at = state.items.findIndex((existing) => idOf(existing) === id);
  const items =
    at < 0 ? [...state.items, item] : state.items.map((existing, index) => (index === at ? item : existing));
  return { items, activeId: id };
}

export function closeDocTabs<T>(
  state: DocTabsState<T>,
  ids: readonly string[],
  idOf: (item: T) => string,
): DocTabsState<T> {
  return ids.reduce((current, id) => closeOne(current, id, idOf), state);
}

function closeOne<T>(state: DocTabsState<T>, id: string, idOf: (item: T) => string): DocTabsState<T> {
  const at = state.items.findIndex((item) => idOf(item) === id);
  if (at < 0) return state;
  const items = state.items.filter((_, index) => index !== at);
  if (state.activeId !== id) return { items, activeId: state.activeId };
  const neighbour = items[Math.max(0, at - 1)];
  return { items, activeId: neighbour === undefined ? undefined : idOf(neighbour) };
}
