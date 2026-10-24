/**
 * 使用端侧栏收起与否：照 ChatGPT，收起后是一条只有图标的窄栏。只记在本浏览器里，取不到（隐私模式、存储被禁）时展开。
 */
import { create } from 'zustand';

const KEY = 'kw.sidebarCollapsed';

function load(): boolean {
  try {
    return window.localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

function save(collapsed: boolean): void {
  try {
    window.localStorage.setItem(KEY, collapsed ? '1' : '0');
  } catch {
    // 存不下就只在本页有效
  }
}

interface SidebarState {
  readonly collapsed: boolean;
  readonly toggle: () => void;
}

export const useSidebar = create<SidebarState>((set, get) => ({
  collapsed: load(),
  toggle: () => {
    const collapsed = !get().collapsed;
    save(collapsed);
    set({ collapsed });
  },
}));

/** 照 ChatGPT 的快捷键：Ctrl/⌘ + Shift + S 收起或展开侧栏。 */
export function isSidebarShortcut(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 's';
}
