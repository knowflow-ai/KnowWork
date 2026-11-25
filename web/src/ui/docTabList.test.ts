import { describe, expect, it } from 'vitest';
import { EMPTY_TABS, closeDocTabs, openDocTab, type DocTabsState } from './docTabList';

interface Doc {
  readonly path: string;
  readonly line?: number;
}

const idOf = (doc: Doc) => doc.path;
const open = (state: DocTabsState<Doc>, path: string, line?: number) =>
  openDocTab(state, line === undefined ? { path } : { path, line }, idOf);
const paths = (state: DocTabsState<Doc>) => state.items.map(idOf);

describe('文档标签的增减（照官方右侧栏）', () => {
  it('新开的放在最后并切过去；已经开着的不另开，换成新的这份再切过去', () => {
    let state: DocTabsState<Doc> = EMPTY_TABS;
    state = open(open(open(state, 'a.docx'), 'b.xlsx'), 'c.pptx');
    expect(paths(state)).toEqual(['a.docx', 'b.xlsx', 'c.pptx']);
    expect(state.activeId).toBe('c.pptx');
    state = open(state, 'a.docx', 12);
    expect(paths(state)).toEqual(['a.docx', 'b.xlsx', 'c.pptx']);
    expect(state.activeId).toBe('a.docx');
    expect(state.items[0]).toEqual({ path: 'a.docx', line: 12 });
  });

  it('关掉当前的切到左边那个，最左边的切到剩下的第一个；关掉别的不换当前；全关掉没有当前', () => {
    const three = open(open(open(EMPTY_TABS, 'a'), 'b'), 'c');
    const middle = { ...three, activeId: 'b' };
    expect(closeDocTabs(middle, ['b'], idOf)).toEqual({
      items: [{ path: 'a' }, { path: 'c' }],
      activeId: 'a',
    });
    expect(closeDocTabs({ ...three, activeId: 'a' }, ['a'], idOf).activeId).toBe('b');
    expect(closeDocTabs(middle, ['c'], idOf).activeId).toBe('b');
    expect(closeDocTabs(middle, ['a', 'b', 'c'], idOf)).toEqual({ items: [], activeId: undefined });
    expect(closeDocTabs(middle, ['x'], idOf)).toBe(middle);
  });

  it('关闭其他、关闭右侧：一个个按同一套规则关，留下的那个成了当前', () => {
    const four = { ...open(open(open(open(EMPTY_TABS, 'a'), 'b'), 'c'), 'd'), activeId: 'd' };
    const others = closeDocTabs(four, ['a', 'c', 'd'], idOf);
    expect(others).toEqual({ items: [{ path: 'b' }], activeId: 'b' });
    const right = closeDocTabs(four, ['c', 'd'], idOf);
    expect(paths(right)).toEqual(['a', 'b']);
    expect(right.activeId).toBe('b');
  });
});
