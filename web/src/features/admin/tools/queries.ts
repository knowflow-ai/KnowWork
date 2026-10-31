/** 工具与 MCP 页的查询与变更：管理接口按当前范围（scope.ts，空间服务或企业 MCP）；另有到达本空间的企业 MCP。 */
import { useQuery } from '@tanstack/react-query';
import {
  listEnterpriseHere,
  setEnterpriseHereOff,
  setEnterpriseHereUsers,
  type McpServer,
  type ToolSettings,
} from '../../../api/mcp';
import { KEYS } from '../queries';
import { useAction } from '../useAction';
import { useMcpScope } from './scope';

export function useMcpServers() {
  const { api, key } = useMcpScope();
  return useQuery({ queryKey: key, queryFn: api.list });
}

export function useMcpActions() {
  const { api, key } = useMcpScope();
  const invalidate = [key] as const;
  return {
    sync: useAction((server: McpServer) => api.sync(server.id), {
      invalidate,
      success: '已刷新工具清单',
      failure: '刷新没有成功',
    }),
    toggle: useAction(
      ({ server, enabled }: { server: McpServer; enabled: boolean }) => api.setEnabled(server.id, enabled),
      { invalidate, failure: '没有切换成功' },
    ),
    remove: useAction((server: McpServer) => api.remove(server.id), {
      invalidate,
      success: '已删除 MCP 服务',
      failure: '没有删除成功',
    }),
    configure: useAction(
      ({ server, tool, settings }: { server: McpServer; tool: string; settings: ToolSettings }) =>
        api.configureTool(server.id, tool, settings),
      { invalidate, failure: '设置没有保存成功' },
    ),
  };
}

export const ENTERPRISE_HERE_KEY = ['admin', 'enterprise-mcp'] as const;

/** 到达本空间的企业 MCP 与本空间的设置。 */
export const useEnterpriseHere = (enabled = true) =>
  useQuery({ queryKey: ENTERPRISE_HERE_KEY, queryFn: listEnterpriseHere, enabled });

export function useEnterpriseHereActions() {
  return {
    toggle: useAction(
      ({ serverId, off }: { serverId: string; off: boolean }) => setEnterpriseHereOff(serverId, off),
      {
        invalidate: [ENTERPRISE_HERE_KEY],
        success: '已切换，新对话里生效',
        failure: '没有切换成功',
      },
    ),
    users: useAction(
      ({ serverId, subjects }: { serverId: string; subjects: readonly string[] }) =>
        setEnterpriseHereUsers(serverId, subjects),
      { invalidate: [ENTERPRISE_HERE_KEY, KEYS.grants], success: '已保存给谁用', failure: '没有保存成功' },
    ),
  };
}
