/**
 * 当前工作空间：登录后从「我能进的空间」里选定（见 features/auth/RequireWorkspace），之后各接口的路径都带上它。
 * 上次用的空间只记在本浏览器里，取不到（隐私模式、存储被禁）或已经进不去时取第一个。
 */
import { create } from 'zustand';

/** 选哪个空间：偏好的那个还能进就用它，否则第一个；一个都进不了返回 null。 */
export function pickWorkspace(
  workspaces: readonly { readonly id: string }[],
  preferred: string | null,
): string | null {
  return workspaces.find((w) => w.id === preferred)?.id ?? workspaces[0]?.id ?? null;
}

const KEY = 'kw.workspace';

export function lastWorkspace(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function remember(id: string): void {
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // 存不下就只在本页有效
  }
}

interface WorkspaceState {
  readonly id: string | null;
  readonly enter: (id: string) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  id: null,
  enter: (id) => {
    remember(id);
    set({ id });
  },
}));

/** 接口层用：还没选定空间就调了要空间的接口，是代码顺序错了，直接报错别悄悄发到别的空间。 */
export function currentWorkspaceId(): string {
  const id = useWorkspaceStore.getState().id;
  if (!id) throw new Error('还没选定工作空间');
  return id;
}
