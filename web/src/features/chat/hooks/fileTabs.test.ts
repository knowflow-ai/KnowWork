import { describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { NO_FILE_TABS, parseFileTabs, useFileTabs, useFileTabsStore } from './fileTabs';

describe('对话右边那栏的标签（照官方右侧栏：按对话记、收起不关、刷新恢复）', () => {
  it('打开就展开；收起标签留着、展开回来照旧；关光了才清空、跟着收起', () => {
    const { result } = renderHook(() => useFileTabs('c1'));
    act(() => result.current.open({ path: 'a.docx' }));
    act(() => result.current.open({ path: 'b.xlsx' }));
    expect(result.current.tabs).toEqual({
      items: [{ path: 'a.docx' }, { path: 'b.xlsx' }],
      activeId: 'b.xlsx',
      expanded: true,
    });
    act(() => result.current.collapse());
    expect(result.current.tabs.items).toHaveLength(2);
    expect(result.current.tabs.expanded).toBe(false);
    act(() => result.current.expand());
    expect(result.current.tabs.expanded).toBe(true);
    act(() => result.current.close(['b.xlsx']));
    expect(result.current.tabs).toMatchObject({ activeId: 'a.docx', expanded: true });
    act(() => result.current.close(['a.docx']));
    expect(result.current.tabs).toEqual(NO_FILE_TABS);
    act(() => result.current.expand());
    expect(result.current.tabs.expanded).toBe(false);
  });

  it('按对话各记各的；记在 localStorage，刷新（内存清空）后照样恢复；关光了就删掉那一份', () => {
    const first = renderHook(() => useFileTabs('c1'));
    const second = renderHook(() => useFileTabs('c2'));
    act(() => first.result.current.open({ path: 'a.docx', line: 3 }));
    act(() => second.result.current.open({ path: 'z.pdf' }));
    expect(second.result.current.tabs.items).toEqual([{ path: 'z.pdf' }]);
    expect(JSON.parse(window.localStorage.getItem('kw.file-tabs.v1.c1')!)).toEqual({
      items: [{ path: 'a.docx', line: 3 }],
      activeId: 'a.docx',
      expanded: true,
    });
    useFileTabsStore.setState({ byConversation: {} });
    const reloaded = renderHook(() => useFileTabs('c1'));
    expect(reloaded.result.current.tabs.items).toEqual([{ path: 'a.docx', line: 3 }]);
    act(() => reloaded.result.current.close(['a.docx']));
    expect(window.localStorage.getItem('kw.file-tabs.v1.c1')).toBeNull();
  });

  it('存储里形状不对、有重复、太多的整份不认；当前标签不在里面就取第一个', () => {
    const ok = JSON.stringify({ items: [{ path: 'a' }, { path: 'b' }], activeId: 'x', expanded: true });
    expect(parseFileTabs(ok)).toEqual({
      items: [{ path: 'a' }, { path: 'b' }],
      activeId: 'a',
      expanded: true,
    });
    for (const bad of [
      'not json',
      JSON.stringify({ items: 'a' }),
      JSON.stringify({ items: [] }),
      JSON.stringify({ items: [{ path: '' }] }),
      JSON.stringify({ items: [{ path: 'a', line: 0 }] }),
      JSON.stringify({ items: [{ path: 'a' }, { path: 'a' }] }),
      JSON.stringify({ items: Array.from({ length: 51 }, (_, i) => ({ path: `f${i}` })) }),
    ]) {
      expect(parseFileTabs(bad)).toEqual(NO_FILE_TABS);
    }
    expect(parseFileTabs(null)).toEqual(NO_FILE_TABS);
  });
});
