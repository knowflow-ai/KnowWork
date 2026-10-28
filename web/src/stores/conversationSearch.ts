/** 搜索对话的框开没开：侧栏的放大镜、收起时窄栏里的放大镜、Ctrl/⌘ + K（照 ChatGPT）都打开它。 */
import { create } from 'zustand';

interface SearchState {
  readonly open: boolean;
  readonly setOpen: (open: boolean) => void;
}

export const useConversationSearch = create<SearchState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));

export function isSearchShortcut(event: KeyboardEvent): boolean {
  return (
    (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'k'
  );
}
