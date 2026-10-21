/** 工具与 MCP 页的查询与变更。 */
import { useQuery } from '@tanstack/react-query';
import {
  configureTool,
  deleteServer,
  listServers,
  setServerEnabled,
  syncServer,
  type McpServer,
  type ToolSettings,
} from '../../../api/mcp';
import { KEYS } from '../queries';
import { useAction } from '../useAction';

export const useMcpServers = () => useQuery({ queryKey: KEYS.mcpServers, queryFn: listServers });

const invalidate = [KEYS.mcpServers] as const;

export function useMcpActions() {
  return {
    sync: useAction((server: McpServer) => syncServer(server.id), {
      invalidate,
      success: '已刷新工具清单',
      failure: '刷新没有成功',
    }),
    toggle: useAction(
      ({ server, enabled }: { server: McpServer; enabled: boolean }) => setServerEnabled(server.id, enabled),
      { invalidate, failure: '没有切换成功' },
    ),
    remove: useAction((server: McpServer) => deleteServer(server.id), {
      invalidate,
      success: '已删除 MCP 服务',
      failure: '没有删除成功',
    }),
    configure: useAction(
      ({ server, tool, settings }: { server: McpServer; tool: string; settings: ToolSettings }) =>
        configureTool(server.id, tool, settings),
      { invalidate, failure: '设置没有保存成功' },
    ),
  };
}
