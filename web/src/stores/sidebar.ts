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
  /** 用户自己选的（记在本浏览器里） */
  readonly collapsed: boolean;
  /**
   * 页面暂时收起（照 ChatGPT Canvas：文档并排打开在对话右边时收起侧栏给两栏腾地方），不改用户的选择，关掉文档就恢复。
   * 显示时按 collapsed 或 folded 算
   */
  readonly folded: boolean;
  readonly toggle: () => void;
  readonly fold: (folded: boolean) => void;
}

export const useSidebar = create<SidebarState>((set, get) => ({
  collapsed: load(),
  folded: false,
  toggle: () => {
    // 暂时收起时点展开：只取消这次暂时收起，用户自己的选择不动
    if (get().folded) {
      set({ folded: false });
      return;
    }
    const collapsed = !get().collapsed;
    save(collapsed);
    set({ collapsed });
  },
  fold: (folded) => set({ folded }),
}));

/** 照 ChatGPT 的快捷键：Ctrl/⌘ + Shift + S 收起或展开侧栏。 */
export function isSidebarShortcut(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 's';
}
