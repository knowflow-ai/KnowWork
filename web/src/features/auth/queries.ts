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

/** 登录或注册成功后先写入缓存（不然进页面时缓存里还是「未登录」，又被送回登录页），再重新取一次 /me：
 * 登录接口不带本部署启用了哪些扩展，取到之前登录守卫先等着（RequireAuth），菜单不会先多后少。 */
export function rememberUser(client: QueryClient, user: CurrentUser): void {
  client.setQueryData<Me>(ME_KEY, { ...user, authMethod: 'session', scopes: null });
  void client.invalidateQueries({ queryKey: ME_KEY, exact: true });
  void client.invalidateQueries({ queryKey: ADMIN_KEY });
}
