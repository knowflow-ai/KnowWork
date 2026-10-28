/** 当前用户与管理员身份的查询。用户信息只放在 TanStack Query 缓存里，不落本地存储。 */
import { useQuery, type QueryClient } from '@tanstack/react-query';
import { fetchMe, type CurrentUser, type Me } from '../../api/auth';
import { checkWorkspaceAdmin, listMyWorkspaces } from '../../api/workspace';

export const ME_KEY = ['me'] as const;
export const ADMIN_KEY = ['me', 'workspace-admin'] as const;
export const MY_WORKSPACES_KEY = ['me', 'workspaces'] as const;

export function useMe() {
  return useQuery<Me | null>({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: Infinity, retry: false });
}

/** 我能进的空间（选定当前空间用）。 */
export function useMyWorkspaces() {
  return useQuery({
    queryKey: MY_WORKSPACES_KEY,
    queryFn: listMyWorkspaces,
    staleTime: 60_000,
    retry: false,
  });
}

/** 是否是空间 owner/admin。判定失败按否处理（fail-closed）；仅决定菜单显隐，服务端仍会再判。 */
export function useIsWorkspaceAdmin(enabled = true) {
  return useQuery({ queryKey: ADMIN_KEY, queryFn: checkWorkspaceAdmin, staleTime: 60_000, enabled });
}

/** 登录或注册成功后直接写入缓存，免得进页面再等一次 /me。 */
export function rememberUser(client: QueryClient, user: CurrentUser): void {
  client.setQueryData<Me>(ME_KEY, { ...user, authMethod: 'session', scopes: null });
  void client.invalidateQueries({ queryKey: ADMIN_KEY });
}
