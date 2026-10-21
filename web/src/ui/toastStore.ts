/**
 * 轻提示的状态：在任何地方调用 toast.success / toast.error，不需要拿 hook。
 * 顶部居中、3 秒后消失，最多同时 3 条（界面规范 4.4）；显示由 Toaster 负责，它挂在 Providers 里。
 */
import { create } from 'zustand';

export type Kind = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  readonly id: number;
  readonly kind: Kind;
  readonly text: string;
}

interface ToastState {
  readonly items: readonly Toast[];
  readonly push: (kind: Kind, text: string) => void;
  readonly dismiss: (id: number) => void;
  readonly clear: () => void;
}

const DURATION_MS = 3000;
const MAX = 3;
let nextId = 1;

export const useToasts = create<ToastState>((set) => ({
  items: [],
  push: (kind, text) => {
    const id = nextId++;
    set((state) => ({ items: [...state.items, { id, kind, text }].slice(-MAX) }));
    setTimeout(() => set((state) => ({ items: state.items.filter((t) => t.id !== id) })), DURATION_MS);
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((t) => t.id !== id) })),
  clear: () => set({ items: [] }),
}));

const push = (kind: Kind) => (text: string) => useToasts.getState().push(kind, text);

export const toast = {
  success: push('success'),
  error: push('error'),
  warning: push('warning'),
  info: push('info'),
};
