/**
 * 登录状态只记一件事：会话是否已失效。当前用户由 TanStack Query 的 ['me'] 缓存持有，
 * 浏览器凭证是 HttpOnly Cookie，前端碰不到也不需要存。
 */
import { create } from 'zustand';

interface SessionState {
  /** 任一接口返回 401 时置为真，受保护页据此跳回登录页。 */
  readonly expired: boolean;
  readonly expire: () => void;
  readonly reset: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  expired: false,
  expire: () => set({ expired: true }),
  reset: () => set({ expired: false }),
}));
